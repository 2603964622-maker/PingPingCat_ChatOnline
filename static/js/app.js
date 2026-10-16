/* ===== app.js — ChatOnline 主逻辑（Telegram 风格 SPA）===== */
(function () {
  'use strict';

  /* ---------------- 小工具 ---------------- */
  const $ = (s, p) => (p || document).querySelector(s);
  const $$ = (s, p) => Array.from((p || document).querySelectorAll(s));
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  /* echarts 懒加载：~1MB 图表库不再随聊天页首屏阻塞下载，只在群/频道预览卡真要画饼图时才拉 */
  let echartsLoading = null;
  function loadEcharts() {
    if (window.echarts) return Promise.resolve();
    if (!echartsLoading) {
      echartsLoading = new Promise((resolve, reject) => {
        const s = document.createElement('script');
        s.src = 'https://cdn.bootcdn.net/ajax/libs/echarts/5.4.3/echarts.min.js';
        s.onload = () => resolve();
        s.onerror = () => { echartsLoading = null; reject(new Error('echarts 加载失败')); };
        document.head.appendChild(s);
      });
    }
    return echartsLoading;
  }

  async function api(url, data, method) {
    const opts = { method: method || (data ? 'POST' : 'GET'), credentials: 'same-origin' };
    if (data !== undefined && data !== null) {
      opts.headers = { 'Content-Type': 'application/json' };
      opts.body = JSON.stringify(data);
    }
    const res = await fetch(url, opts);
    const ct = res.headers.get('Content-Type') || '';
    if (!ct.includes('json')) {
      // 只有真正的未登录（401/重定向到登录页）才提示登录过期；其它非 JSON 响应报状态码，避免误导到登录页
      if (res.status === 401 || res.redirected && /\/user\/login/.test(res.url)) {
        toast('登录状态已失效，请重新登录');
        setTimeout(() => location.href = '/user/login', 900);
        throw new Error('unauthorized');
      }
      throw new Error('请求失败（HTTP ' + res.status + '）');
    }
    return res.json();
  }

  function toast(msg, ms) {
    const wrap = $('#toastWrap');
    const el = document.createElement('div');
    el.className = 'toast';
    el.textContent = msg;
    wrap.appendChild(el);
    setTimeout(() => { el.classList.add('out'); setTimeout(() => el.remove(), 320); }, ms || 2400);
  }

  /* 全局加载遮罩：上传文件 / 发布帖子等耗时操作时给出即时反馈并阻止重复提交 */
  let _loadingEl = null;
  function showLoading(text) {
    if (!_loadingEl) {
      _loadingEl = document.createElement('div');
      _loadingEl.className = 'loading-mask';
      _loadingEl.innerHTML = '<div class="loading-box"><div class="loading-spin"></div><span class="loading-text"></span></div>';
      document.body.appendChild(_loadingEl);
    }
    $('.loading-text', _loadingEl).textContent = text || '处理中…';
    _loadingEl.classList.add('show');
  }
  function hideLoading() { if (_loadingEl) _loadingEl.classList.remove('show'); }

  // 去掉编号的 user_/group_/channel_ 前缀，仅用于展示
  function stripNum(n) {
    if (!n) return '';
    return String(n).replace(/^(user_|group_|channel_)/, '');
  }

  /* ---------------- 头像/图片放大查看器（FLIP 弹簧动画） ---------------- */
  /* viewerOpen 只是“已打开”的意图标记，真实状态以 DOM 为准：一旦它因异常或关闭淡出竞态卡在 true，
     后续所有图片/视频放大都会静默失效（用户表现为“点图片怎么也没反应”），故每次打开前先做一次自愈。 */
  let viewerOpen = false;
  function viewerInDom() { return document.querySelector('.avatar-viewer,.video-viewer'); }
  function resetViewerFlag() { if (viewerOpen && !viewerInDom()) viewerOpen = false; }
  function purgeStaleViewers() {
    if (viewerOpen) return;   // 正常展示中，交给它自己的关闭流程
    // 上一张仍在淡出移除队列里，或异常残留的孤儿节点：先清掉再开新的，避免“第二次点击无效”
    document.querySelectorAll('.avatar-viewer,.video-viewer').forEach((n) => { try { n.remove(); } catch (e) {} });
  }
  /* 点开大图时先拿列表里那张已经加载过的小图占位（只查 DOM，绝不额外发请求）：
     点下去立即有画面，原图在后台下完再无缝替换 —— 用户看到的是“从糊变清”，
     而不是“点了等三秒”。缩略图与原图同比例同位置，替换时不跳版。 */
  function cachedThumbOf(url) {
    for (const size of [400, 900]) {
      const u = thumbUrl(url, size);
      if (!u || u === url) continue;
      // 用页面上已有元素反推“这张小图已经下好了”，比用 new Image() 探缓存更便宜（后者会真的发请求）
      if (document.querySelector('img[src="' + u + '"], img[data-src="' + u + '"]')) return u;
    }
    return '';
  }
  function openAvatarViewer(src, rect, round, caption, gallery, index) {
    resetViewerFlag();
    purgeStaleViewers();
    if (viewerOpen || !src) return;
    viewerOpen = true;
    if (!rect) rect = { left: window.innerWidth / 2 - 1, top: window.innerHeight / 2 - 1, width: 2, height: 2 };
    const list = (Array.isArray(gallery) && gallery.length > 1) ? gallery.slice() : null;
    let idx = list ? Math.min(Math.max(index | 0, 0), list.length - 1) : 0;
    const viewer = document.createElement('div');
    viewer.className = 'avatar-viewer';
    const img = document.createElement('img');
    /* seq 用于作废在途的原图：图集翻页或查看器已关时，旧原图不能盖到新图上 */
    let seq = 0;
    const ph = cachedThumbOf(src);
    img.dataset.full = src;
    img.src = ph || src;
    // 原图后台下：下完才替换，不下完一直看缩略图（不白屏也不转圈）
    function loadFullRes(url, mySeq) {
      const hi = new Image();
      const swap = () => {
        if (viewerOpen && seq === mySeq && img.dataset.full === url) img.src = url;
      };
      hi.onload = () => { if (hi.decode) hi.decode().then(swap, () => {}); else swap(); };
      hi.src = url;
    }
    if (ph) loadFullRes(src, ++seq);
    img.style.borderRadius = round ? '50%' : '16px';
    // 照片按原始宽高比完整展示（contain），头像保持方形裁切（cover）
    if (!round) img.style.objectFit = 'contain';
    Object.assign(img.style, { left: rect.left + 'px', top: rect.top + 'px', width: rect.width + 'px', height: rect.height + 'px' });
    viewer.appendChild(img);
    if (caption) {
      const cap = document.createElement('div');
      cap.className = 'av-caption';
      cap.textContent = caption;
      viewer.appendChild(cap);
    }
    // 图集：左右切换按钮 + 计数（仅多图时）
    let counter = null, prevBtn = null, nextBtn = null;
    if (list) {
      counter = document.createElement('div');
      counter.className = 'av-counter';
      viewer.appendChild(counter);
      prevBtn = document.createElement('button');
      prevBtn.className = 'av-nav av-prev'; prevBtn.type = 'button'; prevBtn.innerHTML = '&#8249;';
      nextBtn = document.createElement('button');
      nextBtn.className = 'av-nav av-next'; nextBtn.type = 'button'; nextBtn.innerHTML = '&#8250;';
      viewer.appendChild(prevBtn); viewer.appendChild(nextBtn);
    }
    function updateCounter() { if (counter) counter.textContent = (idx + 1) + ' / ' + list.length; }
    document.body.appendChild(viewer);
    function targetBox(nwOv, nhOv) {
      if (round) {
        const size = Math.min(window.innerWidth * .78, window.innerHeight * .72, 420);
        return { w: size, h: size };
      }
      const maxW = window.innerWidth * .92, maxH = window.innerHeight * .86;
      const nw = nwOv || img.naturalWidth || rect.width || maxW, nh = nhOv || img.naturalHeight || rect.height || maxH;
      const scale = Math.min(maxW / nw, maxH / nh);
      return { w: Math.round(nw * scale), h: Math.round(nh * scale) };
    }
    function centerTo(w, h) { Object.assign(img.style, { left: (window.innerWidth - w) / 2 + 'px', top: (window.innerHeight - h) / 2 + 'px', width: w + 'px', height: h + 'px' }); }
    function animateOpen() {
      if (!viewerOpen) return;
      const { w, h } = targetBox();
      centerTo(w, h);
      viewer.classList.add('show');
      updateCounter();
    }
    if (round || (img.complete && img.naturalWidth)) {
      requestAnimationFrame(() => requestAnimationFrame(animateOpen));
    } else {
      // 非方形照片：等解码得到 naturalWidth/Height 再按真实比例放大
      img.onload = () => requestAnimationFrame(() => requestAnimationFrame(animateOpen));
      img.onerror = () => requestAnimationFrame(() => requestAnimationFrame(animateOpen));
    }
    // 图集切换：淡出→先上下一张的缩略图→按真实比例居中→淡入，原图再后台替换（关闭 FLIP 位移动画避免形变）；顺带复位缩放
    function show(i) {
      if (!list || !viewerOpen) return;
      idx = (i + list.length) % list.length;
      z.s = 1; z.tx = 0; z.ty = 0; img.style.transform = '';
      img.style.cursor = 'zoom-out';
      const prevTransition = img.style.transition;
      img.style.transition = 'opacity .16s ease';
      img.style.opacity = '0';
      const target = list[idx];
      const mySeq = ++seq;
      // 等的是几十 KB 的缩略图而不是几 MB 的原图：翻页从“白屏 1~3 秒”变成立刻出图
      const small = cachedThumbOf(target) || thumbUrl(target, 400);
      img.dataset.full = target;
      const loader = new Image();
      const done = () => {
        if (!viewerOpen) return;
        img.src = loader.naturalWidth ? small : target;
        const { w, h } = targetBox(loader.naturalWidth, loader.naturalHeight);
        centerTo(w, h);
        img.style.opacity = '1';
        updateCounter();
        setTimeout(() => { if (viewerOpen) img.style.transition = prevTransition; }, 180);
        loadFullRes(target, mySeq);
      };
      loader.onload = done; loader.onerror = done; loader.src = small;
    }
    function close() {
      if (!viewerOpen) return;
      viewerOpen = false;
      clearTimeout(watchdog);
      document.removeEventListener('keydown', onKey, true);
      // 收回前先复位缩放/平移，让 FLIP 动画从“原始适配尺寸”飞回缩略图
      img.style.transition = '';
      img.style.transform = '';
      z.s = 1; z.tx = 0; z.ty = 0;
      img.style.objectFit = 'cover'; // 收回时按缩略图裁切回位
      Object.assign(img.style, { left: rect.left + 'px', top: rect.top + 'px', width: rect.width + 'px', height: rect.height + 'px' });
      viewer.classList.remove('show');
      setTimeout(() => viewer.remove(), 520);
    }
    /* ---- 缩放与平移：手机双指捏合缩放、单指拖拽；电脑滚轮缩放（以光标为锚点）、左键按住拖拽 ---- */
    const z = { s: 1, tx: 0, ty: 0 };
    let swallowClick = false;                        // 拖拽后紧随的 click 被吞掉，避免误关
    img.style.touchAction = 'none';                  // 禁浏览器默认手势（页面滚动/系统缩放），手势交给下面
    // 屏蔽原生“拖出图片”：不禁掉 draggable/dragstart，左键按住会拽出半透明鬼影图而不是平移；
    // user-select 禁掉拖拽时选中背后页面文字
    img.draggable = false;
    img.addEventListener('dragstart', (e) => e.preventDefault());
    viewer.style.userSelect = 'none';
    viewer.style.webkitUserSelect = 'none';
    function applyZ() {
      img.style.transition = 'none';                 // 变换期间不走 CSS 动画，跟手
      img.style.transform = 'translate(' + z.tx + 'px,' + z.ty + 'px) scale(' + z.s + ')';
      img.style.cursor = z.s > 1 ? 'grab' : 'zoom-out';
    }
    function clampPan() {
      if (z.s <= 1) { z.tx = 0; z.ty = 0; return; }
      const lx = (z.s - 1) * img.offsetWidth / 2 + 40;
      const ly = (z.s - 1) * img.offsetHeight / 2 + 40;
      z.tx = Math.max(-lx, Math.min(lx, z.tx));
      z.ty = Math.max(-ly, Math.min(ly, z.ty));
    }
    /* 以屏幕点 (cx,cy) 为锚点缩放到 ns：光标下的图像点在缩放前后保持不动 */
    function zoomAt(ns, cx, cy) {
      ns = Math.min(8, Math.max(1, ns));
      const X0 = parseFloat(img.style.left) + img.offsetWidth / 2;
      const Y0 = parseFloat(img.style.top) + img.offsetHeight / 2;
      if (ns === 1) { z.s = 1; z.tx = 0; z.ty = 0; }
      else {
        z.tx = cx - X0 - (ns / z.s) * (cx - X0 - z.tx);
        z.ty = cy - Y0 - (ns / z.s) * (cy - Y0 - z.ty);
        z.s = ns;
        clampPan();
      }
      applyZ();
    }
    // 滚轮缩放（桌面）：passive:false 才能 preventDefault 压住页面滚动
    viewer.addEventListener('wheel', (e) => {
      e.preventDefault();
      zoomAt(z.s * Math.exp(-e.deltaY * 0.0022), e.clientX, e.clientY);
    }, { passive: false });
    // 指针事件统一处理：单指/鼠标左键拖拽平移，双指捏合缩放
    const ptrs = new Map();
    let pinch = null, drag = null;
    img.addEventListener('pointerdown', (e) => {
      if (e.pointerType === 'mouse' && e.button !== 0) return;   // 右键交给保存菜单
      ptrs.set(e.pointerId, { x: e.clientX, y: e.clientY });
      try { img.setPointerCapture(e.pointerId); } catch (_) {}
      if (ptrs.size === 1) drag = { x: e.clientX, y: e.clientY, tx: z.tx, ty: z.ty, moved: false };
      if (ptrs.size === 2) {
        const p = Array.from(ptrs.values());
        pinch = { d: Math.hypot(p[0].x - p[1].x, p[0].y - p[1].y) || 1, s: z.s };
        drag = null;
      }
    });
    img.addEventListener('pointermove', (e) => {
      if (!ptrs.has(e.pointerId)) return;
      ptrs.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (pinch && ptrs.size >= 2) {
        const p = Array.from(ptrs.values());
        const d = Math.hypot(p[0].x - p[1].x, p[0].y - p[1].y) || 1;
        zoomAt(pinch.s * (d / pinch.d), (p[0].x + p[1].x) / 2, (p[0].y + p[1].y) / 2);
      } else if (drag && ptrs.size === 1) {
        const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
        if (Math.abs(dx) > 3 || Math.abs(dy) > 3) drag.moved = true;
        if (drag.moved && z.s > 1) { z.tx = drag.tx + dx; z.ty = drag.ty + dy; clampPan(); applyZ(); }
      }
    });
    const endPtr = (e) => {
      ptrs.delete(e.pointerId);
      if (drag && drag.moved) { swallowClick = true; setTimeout(() => { swallowClick = false; }, 400); }  // 无 click 跟随则自动复位
      if (ptrs.size < 2) pinch = null;
      // 双指抬一指：以剩余手指重新锚定，避免跳变
      if (ptrs.size === 1) { const p = Array.from(ptrs.values())[0]; drag = { x: p.x, y: p.y, tx: z.tx, ty: z.ty, moved: true }; }
      else if (ptrs.size === 0) drag = null;
    };
    img.addEventListener('pointerup', endPtr);
    img.addEventListener('pointercancel', endPtr);
    viewer.addEventListener('click', (e) => {
      if (swallowClick) { swallowClick = false; return; }
      if (z.s > 1 && e.target === img) return;    // 放大状态下点图不关闭，点背景才退出
      close();
    });
    if (prevBtn) prevBtn.addEventListener('click', (e) => { e.stopPropagation(); show(idx - 1); });
    if (nextBtn) nextBtn.addEventListener('click', (e) => { e.stopPropagation(); show(idx + 1); });
    // 捕获阶段拦截：Esc 关闭，←/→ 图集翻页（优先于抽屉/弹层的全局处理）
    const onKey = (e) => {
      if (e.key === 'Escape') { e.stopPropagation(); close(); }
      else if (list && e.key === 'ArrowLeft') { e.stopPropagation(); show(idx - 1); }
      else if (list && e.key === 'ArrowRight') { e.stopPropagation(); show(idx + 1); }
    };
    document.addEventListener('keydown', onKey, true);
    // 看门狗：极端网络下图片既不触发 onload 也不触发 onerror 时，全屏遮罩会一直压在页面上吞掉所有点击，超时强制关掉
    const watchdog = setTimeout(close, 12000);
  }

  /* ---------------- 视频放大查看器（点频道帖子视频：先放大再播放） ---------------- */
  function openVideoViewer(src) {
    resetViewerFlag();
    purgeStaleViewers();
    if (viewerOpen || !src) return;
    viewerOpen = true;
    const viewer = document.createElement('div');
    viewer.className = 'video-viewer';
    const box = document.createElement('div');
    box.className = 'vv-box';
    const video = document.createElement('video');
    video.src = src;
    video.controls = true;
    video.autoplay = true;
    video.playsInline = true;
    video.setAttribute('playsinline', '');
    const closeBtn = document.createElement('button');
    closeBtn.className = 'vv-close';
    closeBtn.type = 'button';
    closeBtn.innerHTML = '&times;';
    box.appendChild(video);
    viewer.appendChild(box);
    viewer.appendChild(closeBtn);
    document.body.appendChild(viewer);
    requestAnimationFrame(() => requestAnimationFrame(() => viewer.classList.add('show')));
    function close() {
      if (!viewerOpen) return;
      viewerOpen = false;
      clearTimeout(watchdog);
      document.removeEventListener('keydown', onKey, true);
      try { video.pause(); } catch (e) {}
      viewer.classList.remove('show');
      setTimeout(() => viewer.remove(), 320);
    }
    video.addEventListener('click', (e) => e.stopPropagation());
    box.addEventListener('click', (e) => e.stopPropagation());
    closeBtn.addEventListener('click', (e) => { e.stopPropagation(); close(); });
    viewer.addEventListener('click', close);
    const onKey = (e) => { if (e.key === 'Escape') { e.stopPropagation(); close(); } };
    document.addEventListener('keydown', onKey, true);
    const watchdog = setTimeout(close, 12000);
  }

  function parseDate(v) {
    if (!v) return null;
    if (typeof v === 'string' && v.indexOf('T') < 0) v = v.replace(' ', 'T') + 'Z';
    else if (typeof v === 'string' && /Z|[+-]\d{2}:\d{2}$/.test(v) === false && v.indexOf('T') >= 0) v += 'Z';
    const d = new Date(v);
    return isNaN(d) ? null : d;
  }
  function fmtTime(v) { const d = parseDate(v); return d ? d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false }) : ''; }
  function fmtDay(v) {
    const d = parseDate(v); if (!d) return '';
    const today = new Date(), yest = new Date(today); yest.setDate(today.getDate() - 1);
    const same = (a, b) => a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
    if (same(d, today)) return '今天';
    if (same(d, yest)) return '昨天';
    return d.toLocaleDateString('zh-CN', { year: 'numeric', month: 'long', day: 'numeric' });
  }
  function fmtRelTime(v) {
    const d = parseDate(v); if (!d) return '';
    const diff = (Date.now() - d.getTime()) / 1000;
    if (diff < 60) return '刚刚';
    if (diff < 3600) return Math.floor(diff / 60) + ' 分钟前';
    if (diff < 86400) return Math.floor(diff / 3600) + ' 小时前';
    if (diff < 604800) return Math.floor(diff / 86400) + ' 天前';
    return d.toLocaleDateString('zh-CN');
  }
  function fmtBytes(n) {
    if (!n && n !== 0) return '';
    if (n < 1024) return n + ' B';
    if (n < 1048576) return (n / 1024).toFixed(1) + ' KB';
    if (n < 1073741824) return (n / 1048576).toFixed(1) + ' MB';
    return (n / 1073741824).toFixed(2) + ' GB';
  }
  function avatarOf(c) {
    if (c.avatar) return c.avatar;
    return 'https://robohash.org/' + encodeURIComponent(c.conv_type + '_' + c.target_id) + '?set=set2&size=180x180';
  }
  function nameFromUrl(url) {
    // 存储名格式：时间戳_uuid_原始文件名 → 剪掉前两段还原真名；旧格式（时间戳_uuid）无原名可回退
    try {
      const seg = url.split('/').pop().split('_');
      const name = seg.length >= 3 ? seg.slice(2).join('_') : seg.join('_');
      return decodeURIComponent(name) || '文件';
    } catch (e) { return '文件'; }
  }

  /* 头像兜底：robohash 等外链不可达时，换成本地生成的首字母彩色圆（无任何外部请求） */
  function letterAvatar(name) {
    let ch = (name || '?').trim().slice(0, 1).toUpperCase() || '?';
    let h = 0; const s = name || '';
    for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
    const svg = "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><rect width='100' height='100' rx='50' fill='hsl(" + (h % 360) + ",55%,52%)'/><text x='50' y='50' dy='.36em' text-anchor='middle' fill='#fff' font-size='44' font-family='sans-serif'>" + ch.replace(/&/g, '&amp;').replace(/</g, '&lt;') + "</text></svg>";
    return 'data:image/svg+xml,' + encodeURIComponent(svg);
  }
  function watchAvatar(img, name) {
    if (!img || img.dataset.watched) return;
    img.dataset.watched = '1';
    img.addEventListener('error', function () {
      // 缩略图不可用时先回退原图，原图也拿不到才换首字母彩色圆
      if (!img.dataset.fb && img.dataset.full && img.src !== img.dataset.full) {
        img.dataset.fb = '1'; img.src = img.dataset.full; return;
      }
      if (img.dataset.fb) return;
      img.dataset.fb = '1';
      img.src = letterAvatar(name);
    });
    // 绑定监听前就已加载失败的情况
    if (img.complete && img.naturalWidth === 0 && img.getAttribute('src')) img.dispatchEvent(new Event('error'));
  }

  /* ---------------- 缩略图与受控图片加载池 ----------------
     原图字节永不改动：列表/网格一律显示 /upload/thumb 派生的缩略图（后端首次被请求时生成并回写对象存储），
     点开大图、另存为、下载仍走 data-full 上的原图，所以画质零损失，只是桶里多几份小副本。
     加载不再交给浏览器 loading=lazy——手机自带浏览器会一次拉十几张大图，解码内存超限整片破图；
     这里用 IntersectionObserver 决定“何时开始”、信号量决定“同时几张”，失败先回退原图再给重试占位。 */
  const IMG_POOL_SIZE = 4;
  /* 单张图最长等多久：服务端可能正在现生成缩略图，但槽位绝不能被一个挂死的请求
     永久占住——否则 4 张卡住 = 整个池子死锁，后面所有图片永远不会开始。 */
  const IMG_LOAD_TIMEOUT = 25000;
  const imgPending = [];
  let imgInFlight = 0;
  const IMG_RETRY_SRC = 'data:image/svg+xml,' + encodeURIComponent(
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 160 160'><rect width='160' height='160' fill='#dfe4ea'/>" +
    "<g fill='none' stroke='#8b95a1' stroke-width='7' stroke-linecap='round'><path d='M80 42a30 30 0 1 1-28 18'/><path d='M48 34v26h26'/></g>" +
    "<text x='80' y='126' font-size='18' fill='#7c8794' text-anchor='middle' font-family='sans-serif'>点击重试</text></svg>");

  function thumbUrl(url, size) {
    // data:/blob: 本地预览、站内 /static 资源与 robohash 外链都不包：它们不是本桶对象，交回浏览器直读
    if (!url || !/^https?:/i.test(url) || /robohash\.org/i.test(url) || url.indexOf('/upload/thumb?') >= 0) return url;
    return '/upload/thumb?s=' + (size || 400) + '&u=' + encodeURIComponent(url);
  }
  /* 生成一个受控加载的图片标签：保留各场景原有 class（CSS 全按容器/类名选中），
     真地址放 data-src，原图放 data-full 供点开大图与另存为使用 */
  function mediaImgHtml(url, size, cls) {
    return '<img' + (cls ? ' class="' + cls + '"' : '') +
      ' data-src="' + esc(thumbUrl(url, size || 400)) + '" data-full="' + esc(url) + '" alt="" decoding="async">';
  }
  /* 头像：体积小但数量多（会话列表/成员列表一屏几十张），同样上缩略图；
     不用进加载池——它们本来就要立即呈现，回退交给 watchAvatar（先看 data-full 再换首字母圆） */
  function avatarImgHtml(url, cls, attrs) {
    const full = url || '';
    return '<img class="' + (cls || '') + '" src="' + esc(thumbUrl(full, 400)) + '" data-full="' + esc(full) +
      '" alt="" decoding="async"' + (attrs || '') + '>';
  }
  function imgFail(img) {
    // 先清掉 src 再挂占位图，否则浏览器自己那个破图标会叠在占位上一起露出来
    img.classList.add('img-fail');
    img.removeAttribute('src');
    img.src = IMG_RETRY_SRC;
  }
  function pumpImgPool() {
    while (imgInFlight < IMG_POOL_SIZE && imgPending.length) {
      const img = imgPending.shift();
      if (!img.isConnected || !img.dataset.src) continue;
      imgInFlight++;
      let settled = false;
      const timer = setTimeout(() => img.dispatchEvent(new Event('error')), IMG_LOAD_TIMEOUT);
      const done = () => {
        if (settled) return;          // 超时与真实 load/error 可能都来，只结算一次
        settled = true;
        clearTimeout(timer);
        imgInFlight = Math.max(0, imgInFlight - 1);
        pumpImgPool();
      };
      img.addEventListener('load', done, { once: true });
      img.addEventListener('error', () => {
        if (!img.dataset.triedFull && img.dataset.full && img.src !== img.dataset.full) {
          // 缩略图服务不可用或太慢（未装 Pillow / 历史本地路径 / 外链 / 生成中挂死）：
          // 就地换原图重排队，不算失败——原图至少能直连对象存储，不经服务端
          img.dataset.triedFull = '1';
          img.dataset.src = img.dataset.full;
          img.removeAttribute('src');
          imgPending.unshift(img);
        } else {
          imgFail(img);
        }
        done();
      }, { once: true });
      img.src = img.dataset.src;
    }
  }
  const imgIO = ('IntersectionObserver' in window) ? new IntersectionObserver((entries) => {
    entries.forEach(en => {
      if (!en.isIntersecting) return;
      imgIO.unobserve(en.target);
      const img = en.target;
      if (img.dataset.src && imgPending.indexOf(img) < 0) { imgPending.push(img); pumpImgPool(); }
    });
  }, { root: null, rootMargin: '300px 0px' }) : null;
  function registerLazyImg(img) {
    if (img.dataset.pool) return;
    img.dataset.pool = '1';
    if (!imgIO) { imgPending.push(img); pumpImgPool(); return; }   // 老浏览器没有 IO：直接排队加载
    imgIO.observe(img);
  }
  function watchLazyMedia(root) {
    const scope = root || document;
    if (scope.matches && scope.matches('img[data-src]')) registerLazyImg(scope);
    $$('img[data-src]', scope).forEach(registerLazyImg);
  }
  // 失败占位图本身也是一张 img，靠 .img-fail 区分，否则一点就套一层死循环
  function isRealImg(img) { return img.tagName === 'IMG' && !img.classList.contains('img-fail'); }
  function tgGallery(cell) {
    // 同一图集的原始地址列表（点开大图后左右翻页用）：优先读容器上的 data-tgall，回退到 DOM
    const box = cell.parentElement;
    if (box && box.dataset.tgall) { try { return JSON.parse(box.dataset.tgall); } catch (e) {} }
    return Array.from((box || document).querySelectorAll('.tg-cell[data-tgimg]')).map(c => c.dataset.tgimg);
  }
  // 取一张图背后的“原图地址”：列表与网格渲染的都是缩略图，点开大图 / 另存为 / 下载必须回到原图，画质不受影响
  function fullSrc(el) {
    if (!el) return '';
    return el.dataset.full || el.dataset.tgimg || el.dataset.img || el.getAttribute('data-src') || el.currentSrc || el.src || '';
  }
  // 刚上传完的媒体必须立刻出现，不能等它滚进视口：直接插到队首加载
  function loadImgNow(img) {
    if (!img || !img.dataset.src) return;
    img.dataset.pool = '1';
    if (imgIO) imgIO.unobserve(img);
    const i = imgPending.indexOf(img);
    if (i >= 0) imgPending.splice(i, 1);
    imgPending.unshift(img);
    pumpImgPool();
  }

  /* 页面里所有列表都是 innerHTML 拼出来的，逐个渲染点去调 watchLazyMedia 太散：
     用 MutationObserver 统一兜底——只扫描新插入的节点，已入册的靠 dataset.pool 去重 */
  if ('MutationObserver' in window) {
    new MutationObserver((records) => {
      records.forEach(r => {
        r.addedNodes && r.addedNodes.forEach(n => { if (n.nodeType === 1) watchLazyMedia(n); });
      });
    }).observe(document.body, { childList: true, subtree: true });
  }
  watchLazyMedia(document);

  // 失败占位图：点一下重走“缩略图→原图”，而不是让用户刷新整页（比正常图片的点击放大优先）
  document.addEventListener('click', (e) => {
    const img = e.target;
    if (!img || img.tagName !== 'IMG' || !img.classList.contains('img-fail')) return;
    e.stopPropagation(); e.preventDefault();
    img.classList.remove('img-fail');
    img.removeAttribute('src');
    delete img.dataset.triedFull;
    loadImgNow(img);
  }, true);
  // 重试占位图自己也是一张 img，不拦住会自己触发自己的 error 循环
  document.addEventListener('error', (e) => {
    const img = e.target;
    if (img && img.tagName === 'IMG' && img.classList.contains('img-fail')) e.stopPropagation();
  }, true);

  /* ---------------- 全局状态 ---------------- */
  const S = {
    me: null,
    sock: null,
    current: null,          // {type:'private|group|channel', id, name, avatar}
    oldestId: null,
    hasMore: false,
    loadingOlder: false,
    convs: new Map(),       // key -> data
    openPostRoom: null,     // {channel_id, post_id}
    userCache: new Map(),   // id -> {nickname, avatar}
    blocked: new Set(),     // 黑名单 key：'user:id' | 'group:id' | 'channel:id'
    remarks: new Map(),     // 我给好友设置的备注：friendId(number) -> remark
    tab: 'chats',
    feedScrollTop: null,    // 进入帖子详情前的频道帖子流滚动位置，返回时还原，避免每次跳回顶部/底部
  };
  const convListEl = $('#convList');
  const msgListEl = $('#msgList');
  const msgScrollEl = $('#msgScroll');
  const panelEl = $('#chatPanel');
  const convInfoView = $('#convInfoView');

  /* 首次交互时浏览器自动播放策略由 SFX 内部 resume 处理 */

  /* ---------------- 会话列表 ---------------- */
  function convKey(t, id) { return t + ':' + id; }

  /* ---------------- 好友备注（单向，仅影响“我”看到的名字） ---------------- */
  // 统一显示名：有备注用备注，否则用后端返回的原生昵称/名称
  function dispName(uid, fallback) {
    if (uid == null) return fallback || '';
    const rk = S.remarks.get(+uid);
    return rk || fallback || '';
  }
  async function loadRemarks() {
    const r = await api('/friend/remarks').catch(() => null);
    S.remarks = new Map();
    if (r && r.code === 0 && r.data && r.data.items) {
      r.data.items.forEach(it => { if (it.remark) S.remarks.set(+it.friend_id, it.remark); });
    }
  }
  // 设置/清除备注弹窗：留空即清除。成功后刷新本地映射与相关界面
  function openRemarkEditor(uid, current, done) {
    openModal('设置备注',
      '<div class="modal-hint" style="margin:0 0 8px">仅你自己可见，设置后在所有场景都会以备注名显示该好友。</div>' +
      '<input class="modal-input" id="mRemark" maxlength="64" placeholder="输入备注名（留空则清除）" value="' + esc(current || '') + '">',
      async () => {
        const val = ($('#mRemark').value || '').trim();
        const r = await api('/friend/remark', { friend_id: uid, remark: val });
        if (r.code === 0) {
          closeModal();
          if (val) S.remarks.set(+uid, val); else S.remarks.delete(+uid);
          toast(val ? '备注已设置' : '已清除备注');
          await reloadConversations();
          if (S.current && S.current.type === 'private' && S.current.id === +uid) {
            $('#chatTitle').textContent = dispName(uid, S.current.name);
          }
          if (S.tab === 'contacts') renderContacts();
          if (done) done();
        } else toast(r.msg || '操作失败');
      }, '保存');
    setTimeout(() => { const el = $('#mRemark'); if (el) { el.focus(); el.select && el.select(); } }, 60);
  }

  function buildConvEl(c) {
    const el = document.createElement('div');
    el.className = 'conv-item';
    el.dataset.key = convKey(c.conv_type, c.target_id);
    el.dataset.type = c.conv_type;
    el.dataset.id = c.target_id;
    el.innerHTML =
      '<img class="conv-avatar" src="' + esc(thumbUrl(avatarOf(c), 400)) + '" data-full="' + esc(avatarOf(c)) + '" alt="">' +
      '<div class="conv-body">' +
      '<div class="conv-top"><div class="conv-name">' + esc(c.conv_type === 'private' ? dispName(c.target_id, c.name) : c.name) + '</div>' +
      '<div class="conv-time">' + esc(fmtRelTime(c.updated_at)) + '</div></div>' +
      '<div class="conv-bottom">' +
      '<div class="conv-sub">' + esc(c.last_content || '暂无消息') + '</div>' +
      (c.unread_count > 0 ? '<div class="conv-badge' + (c.notify_level === 3 ? ' dot' : '') + '">' + (c.notify_level === 3 ? '' : (c.unread_count > 99 ? '99+' : c.unread_count)) + '</div>' : '') +
      '</div></div>';
    el.addEventListener('click', () => openConversation(c));
    watchAvatar($('.conv-avatar', el), c.name);
    return el;
  }

  async function reloadConversations() {
    try {
      const r = await api('/chat/conversations');
      if (r.code !== 0) return;
      S.convs.clear();
      convListEl.querySelectorAll('.conv-item').forEach(x => x.remove());
      const list = r.data.conversations || [];
      $('#convEmpty').hidden = list.length > 0;
      list.forEach(c => {
        S.convs.set(convKey(c.conv_type, c.target_id), c);
        convListEl.appendChild(buildConvEl(c));
      });
      markActive();
      updateChatTabDot();
    } catch (e) { /* 静默 */ }
  }

  function markActive() {
    $$('.conv-item').forEach(el => el.classList.remove('active'));
    if (!S.current) return;
    const el = $('[data-key="' + convKey(S.current.type, S.current.id) + '"]', convListEl);
    if (el) { el.classList.add('active'); const b = $('.conv-badge', el); if (b) b.remove(); }
  }

  /* FLIP：会话带着动画上浮到列表顶部 */
  function flyToTop(el) {
    const list = convListEl;
    const others = $$('.conv-item', list).filter(x => x !== el);
    const before = others.map(o => o.getBoundingClientRect().top);
    const elBefore = el.getBoundingClientRect().top;
    list.prepend(el);
    others.forEach((o, i) => {
      const dy = before[i] - o.getBoundingClientRect().top;
      if (Math.abs(dy) > 1) {
        o.style.transition = 'none';
        o.style.transform = 'translateY(' + dy + 'px)';
        requestAnimationFrame(() => {
          o.style.transition = 'transform .55s cubic-bezier(.34,1.56,.64,1)';
          o.style.transform = '';
        });
      }
    });
    const dy = elBefore - el.getBoundingClientRect().top;
    el.style.setProperty('--fly-dy', dy > 0 ? dy + 'px' : '-24px');
    el.classList.remove('fly', 'pulse'); void el.offsetWidth;
    el.classList.add('fly', 'pulse');
    setTimeout(() => {
      el.classList.remove('fly', 'pulse');
      others.forEach(o => { o.style.transition = ''; o.style.transform = ''; });
    }, 1000);
  }

  /* 收到消息：本地更新会话项并置顶（不存在则整体刷新）；静音频道不响铃、角标退化为灰点 */
  function bumpConversation(type, id, preview) {
    const key = convKey(type, id);
    let el = $('[data-key="' + key + '"]', convListEl);
    if (!el) { reloadConversations(); return; }
    const conv = S.convs.get(key);
    const muted = (type === 'channel' || type === 'private' || type === 'group') && conv && conv.notify_level === 3;
    const sub = $('.conv-sub', el); if (sub) sub.textContent = preview || '新消息';
    const tm = $('.conv-time', el); if (tm) tm.textContent = '刚刚';
    const isActive = S.current && convKey(S.current.type, S.current.id) === key && !document.hidden;
    if (!isActive) {
      if (conv) conv.unread_count = (conv.unread_count || 0) + 1;
      let badge = $('.conv-badge', el);
      if (!badge) { badge = document.createElement('div'); badge.className = 'conv-badge' + (muted ? ' dot' : ''); badge.textContent = muted ? '' : String(Math.min(99, conv ? conv.unread_count : 1)); $('.conv-bottom', el).appendChild(badge); void badge.offsetWidth; badge.style.transform = 'scale(0)'; requestAnimationFrame(() => { badge.style.transform = 'scale(1)'; }); }
      else if (muted) { badge.classList.add('dot'); badge.textContent = ''; badge.style.transform = 'scale(1.25)'; requestAnimationFrame(() => badge.style.transform = 'scale(1)'); }
      else { badge.textContent = String(Math.min(99, conv ? conv.unread_count : 1)); badge.style.transform = 'scale(1.25)'; requestAnimationFrame(() => badge.style.transform = 'scale(1)'); }
      updateChatTabDot();
    }
    flyToTop(el);
    if (window.SFX && !muted) SFX.bump();
  }

  /* ---------------- 窗口开合（Apple 式缩放） ---------------- */
  function isMobile() { return window.matchMedia('(max-width:720px)').matches; }

  function panelShow(anim) {
    panelEl.hidden = false;
    $('#chatPlaceholder').hidden = true;
    if (isMobile()) $('#main').classList.add('open');
    annSyncVisible();   // 打开会话后送信小狗休息，回到会话列表再放出来
    if (anim !== false) {
      panelEl.classList.remove('closing'); void panelEl.offsetWidth;
      panelEl.classList.add('opening');
      panelEl.addEventListener('animationend', () => panelEl.classList.remove('opening'), { once: true });
      if (window.SFX) SFX.whoosh(true);
    }
  }
  function panelHide() {
    if (isMobile()) { $('#main').classList.remove('open'); }
    annSyncVisible();   // 关闭会话回到列表，重新放出送信小狗（有未确认公告时）
    panelEl.classList.remove('opening'); void panelEl.offsetWidth;
    panelEl.classList.add('closing');
    if (window.SFX) SFX.whoosh(false);
    panelEl.addEventListener('animationend', function done() {
      panelEl.classList.remove('closing');
      panelEl.removeEventListener('animationend', done);
    });
    setTimeout(() => {
      if (!S.current) { panelEl.hidden = true; $('#chatPlaceholder').hidden = false; }
    }, 300);
  }

  function leaveRoomOf(cur) {
    if (!S.sock || !cur) return;
    if (cur.type === 'group') S.sock.emit('leave_group', { group_id: cur.id });
    if (cur.type === 'channel') S.sock.emit('leave_channel', { channel_id: cur.id });
  }

  function openConversation(c) {
    // 硬性规则：不能打开与自己的会话（后端接口/发送事件/会话列表已层层拦截，这里封住所有前端入口）
    if (c.conv_type === 'private' && c.target_id === S.me.id) { toast('不能查看与自己的聊天记录'); return; }
    // 从通讯录/搜索结果等入口打开会话时，侧栏自动切回聊天 tab
    if (S.tab !== 'chats') switchTab('chats');
    panelEl.classList.remove('info-open');  // 切换/重开会话时回到聊天视图，不残留群信息面板
    closeConvSearch();                       // 收起上一个会话的会话内搜索面板
    if (S.current && S.current.type === c.conv_type && S.current.id === c.target_id) { if (isMobile() && panelEl.hidden) panelShow(); return; }
    removeAnnounceBar();                     // 清掉上一个会话的公告横幅（同会话重开保留）
    leaveRoomOf(S.current);
    if (S.openPostRoom) { S.sock && S.sock.emit('leave_channel_post', S.openPostRoom); S.openPostRoom = null; }  // 切换会话时离开残留的帖子评论房间
    S.current = { type: c.conv_type, id: c.target_id, name: c.name, avatar: c.avatar, role: c.role, notify: c.notify_level };
    S.pendingMentions = []; S.groupMembers = null; hideMentionPop();  // 切换会话清空未发送的@选择
    exitMsgReply();  // 清除上一个会话残留的回复态
    // 清零未读
    api('/chat/conversations/read', { conv_type: c.conv_type, target_id: c.target_id }).catch(() => {});
    const stored = S.convs.get(convKey(c.conv_type, c.target_id)); if (stored) stored.unread_count = 0;
    updateChatTabDot();

    $('#chatAvatar').src = avatarOf(c);
    watchAvatar($('#chatAvatar'), c.name);
    $('#chatTitle').textContent = c.conv_type === 'private' ? dispName(c.target_id, c.name) : c.name;
    $('#chatSub').textContent = c.conv_type === 'private' ? '' : (c.conv_type === 'group' ? '群聊' : ('频道 · ' + (c.subscriber_count != null ? c.subscriber_count : 0) + ' 订阅者'));
    // 私聊/群聊头部可点击进入资料页：标记 clickable 供桌面端 hover 可点击反馈
    $('#chatId').classList.toggle('clickable', c.conv_type === 'private' || c.conv_type === 'group');

    // 模式切换
    const isChannel = c.conv_type === 'channel';
    msgListEl.classList.toggle('channel-mode', isChannel);  // 频道下帖子流/详情整列左对齐
    $('#composer').style.display = isChannel ? 'none' : 'flex';
    // 频道底部栏：进入时复位到帖子流视图（发帖按钮/订阅者隐藏由 setChannelBottom 按 role 决定）
    if (isChannel) { S.channelView = 'feed'; setChannelBottom('feed'); }
    else { $('#channelPublish').classList.remove('show'); }
    $('#attachBtn').style.visibility = c.conv_type === 'private' || c.conv_type === 'group' ? 'visible' : 'hidden';

    msgListEl.innerHTML = '';
    S.oldestId = null; S.hasMore = false;

    panelShow();
    markActive();

    // 注意：会话对象上的标识字段是 target_id（后端 /chat/conversations 契约），不是 id
    if (c.conv_type === 'private') loadPrivate(c.target_id);
    else if (c.conv_type === 'group') { S.sock && S.sock.emit('join_group', { group_id: c.target_id }); loadGroup(c.target_id); refreshAnnounceBar(); }
    else { S.sock && S.sock.emit('join_channel', { channel_id: c.target_id }); loadChannel(c.target_id); refreshAnnounceBar(); }
  }

  function closeChat() {
    leaveRoomOf(S.current);
    if (S.openPostRoom) { S.sock && S.sock.emit('leave_channel_post', S.openPostRoom); S.openPostRoom = null; }
    S.channelView = 'feed';
    panelEl.classList.remove('post-detail');
    $('#channelPublish').classList.remove('show');
    removeAnnounceBar();
    S.current = null;
    panelEl.classList.remove('info-open');
    panelHide();
    markActive();
    if (isMobile()) $('#chatPlaceholder').hidden = false;
  }

  // 私聊标题副行：显示对方在线/最近活跃（笼统分档），带状态圆点
  function setChatSubPresence(online, text) {
    const sub = $('#chatSub'); if (!sub) return;
    sub.innerHTML = '<span class="pres-dot' + (online ? ' on' : '') + '"></span>' + esc(text || '');
  }

  /* ---------------- 消息渲染 ---------------- */
  function daySepEl(content) {
    const d = document.createElement('div');
    d.className = 'day-sep';
    d.textContent = content;
    return d;
  }
  function needDaySep(isOlderAppend) {
    const last = msgListEl.querySelector('[data-day]');
    return last ? last.dataset.day : null;
  }

  // 移除消息后规整日期分隔条：删掉“下方没挂任何消息”的孤立 day-sep（含连续重复与末尾孤儿）。
  // 场景：发一条→撤回→再发…会不断遗留孤儿“今天”，下次发送又新增一条，累积成多条日期胶囊。
  function normalizeDaySeps() {
    const kids = Array.prototype.slice.call(msgListEl.children);
    let pendingSep = null;
    for (let i = 0; i < kids.length; i++) {
      const node = kids[i];
      if (node.nodeType !== 1) continue;
      if (node.classList.contains('day-sep')) {
        if (pendingSep) pendingSep.remove();  // 上一个分隔条没有消息跟随 → 孤儿，移除
        pendingSep = node;
      } else {
        pendingSep = null;                     // 遇到消息行 → 前一个分隔条有效，保留
      }
    }
    if (pendingSep) pendingSep.remove();       // 末尾孤儿分隔条
  }

  // 统一“整行粒子化移除”：撤回 / 删除 / 收到撤回广播都走这里，避免各自实现漂移
  function dissolveRow(row) {
    if (!row) return;
    if (row.dataset.removing) return;          // 防重复：自己撤回与后端撤回广播可能同时到达
    row.dataset.removing = '1';
    const bubble = (row.querySelector && row.querySelector('.bubble')) || row;
    if (window.PARTICLES) {
      PARTICLES.dissolve(bubble);
      // dissolve 内部约 990ms 移除气泡，这里稍后连整行一起移除并规整日期条
      setTimeout(() => { row.remove(); normalizeDaySeps(); }, 1000);
    } else {
      row.remove();
      normalizeDaySeps();
    }
  }

  /* 中间省略：过长时保留头尾、中间用…连接（CSS 的 ellipsis 只能截尾，做不到头尾都留） */
  function middleEllipsis(s, max) {
    s = s || '';
    if (s.length <= max) return s;
    const head = Math.ceil((max - 1) * 0.6);
    const tail = max - 1 - head;
    return s.slice(0, head) + '…' + s.slice(-tail);
  }
  /* 文件类型 → static/svg 下对应图标；未知扩展名兜底 Default。图片不走文件卡片，视频有 MP4 图标备档 */
  const FILE_ICON_MAP = {
    pdf: 'PDF', doc: 'Word', docx: 'Word', xls: 'Excel', xlsx: 'Excel', csv: 'Excel',
    ppt: 'PPT', pptx: 'PPT', txt: 'TXT', md: 'TXT', log: 'TXT',
    mp3: 'MP3', wav: 'MP3', flac: 'MP3', aac: 'MP3', ogg: 'MP3', m4a: 'MP3',
    mp4: 'MP4', mov: 'MP4', avi: 'MP4', mkv: 'MP4', webm: 'MP4', wmv: 'MP4', flv: 'MP4',
    zip: 'ZIP', tar: 'ZIP', gz: 'ZIP', rar: 'RAR', '7z': 'RAR',
  };
  function fileIconUrl(nameOrUrl) {
    const e = ((nameOrUrl || '').split('.').pop() || '').toLowerCase();
    return '/static/svg/' + (FILE_ICON_MAP[e] || 'Default') + '_Icon.svg';
  }
  function fileIcHtml(name, style) {
    return '<div class="file-ic svg-ic"' + (style ? ' style="' + style + '"' : '') + '><img src="' + fileIconUrl(name) + '" alt="" draggable="false"></div>';
  }
  function fileCardHtml(url, filename, size) {
    const full = filename || nameFromUrl(url);
    return '<div class="file-card">' + fileIcHtml(full) +
      '<div class="file-meta"><b title="' + esc(full) + '">' + esc(middleEllipsis(full, 20)) + '</b>' +
      '<span>' + esc(size ? fmtBytes(size) : '点击下载') + ' · 下载</span></div></div>';
  }
  const VIDEO_RE = /\.(mp4|avi|mkv|mov|wmv|flv|webm)$/i;
  function isVideo(nameOrUrl) { return VIDEO_RE.test(nameOrUrl || ''); }
  /* 视频右上角“放大”按钮：气泡内的视频原本只能就地小窗播放，没有任何全屏入口，
     点它会走 .vid-expand 委托→openVideoViewer（用捕获阶段监听，避开“点击即播放”的委托） */
  function vidExpandBtn(url, name) {
    return '<button class="vid-expand" type="button" title="放大观看" aria-label="放大观看" data-vv="' + esc(url) + '" data-vn="' + esc(name || nameFromUrl(url)) + '">' +
      '<svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round">' +
      '<path d="M9 4H4v5M15 4h5v5M9 20H4v-5M15 20h5v-5"/></svg></button>';
  }
  /* 视频消息：默认只显示首帧+播放按钮，点击后才启用控制条并开始播放 */
  function videoHtml(url, name) {
    return '<div class="vid-click"><video class="media" src="' + esc(url) + '" preload="metadata" playsinline></video>' +
      '<span class="tg-play"><svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z" fill="#fff"/></svg></span>' +
      vidExpandBtn(url, name) + '</div>' +
      '<a class="dl-link" href="' + esc(url) + '" download="' + esc(name || nameFromUrl(url)) + '">下载原视频</a>';
  }
  /* 委托点击：.vid-click 首次点击启用原生控制条并播放，同时遮罩淡出；后续点击交给视频自身处理 */
  document.addEventListener('click', (e) => {
    const wrap = e.target && e.target.closest ? e.target.closest('.vid-click') : null;
    if (!wrap || wrap.dataset.playing) return;
    const v = wrap.querySelector('video');
    if (!v) return;
    wrap.dataset.playing = '1';
    v.controls = true;
    const mask = wrap.querySelector('.tg-play');
    if (mask) mask.classList.add('hide');
    try { const p = v.play(); if (p && p.catch) p.catch(() => {}); } catch (_) {}
  });
  /* 频道帖子媒体拼贴（Telegram 风格）：图片+视频在前，按数量选布局，非简单九宫格 */
  const TG_IMG_RE = /\.(png|jpe?g|gif|webp|bmp|avif)$/i;
  const TG_MAX_CELLS = 9;   // 一屏可能叠好几条帖子：超过这个数只渲染前 8 张 + “+N”角标，其余点开大图翻页看
  function isImgFile(f) { return f.file_type === 3 || TG_IMG_RE.test(f.filename || f.file_url || ''); }
  function isVidFile(f) { return f.file_type === 2 || isVideo(f.filename || '') || isVideo(f.file_url || ''); }
  function mediaMosaicHtml(files) {
    const media = (files || []).filter(f => isImgFile(f) || isVidFile(f));
    const n = media.length;
    if (!n) return '';
    const shown = media.slice(0, TG_MAX_CELLS);
    const more = n - shown.length;
    // 单图会铺到 380×440，给一档更清晰的（900 长边足够覆盖 2x 屏）；多图网格只占 118px 方格，400 档就够
    const cellSize = n === 1 ? 900 : 400;
    const cell = (f, extra, badge) => {
      const url = esc(f.file_url);
      if (isVidFile(f)) {
        return '<div class="tg-cell' + (extra || '') + '" data-tgvid="' + url + '"><video src="' + url + '" preload="metadata" muted playsinline></video>' +
          '<span class="tg-play"><svg viewBox="0 0 24 24" width="26" height="26"><path d="M8 5v14l11-7z" fill="#fff"/></svg></span></div>';
      }
      return '<div class="tg-cell' + (extra || '') + '" data-tgimg="' + url + '">' +
        mediaImgHtml(f.file_url, cellSize, '') + (badge || '') + '</div>';
    };
    let cls, cells;
    const nc = Math.min(n, TG_MAX_CELLS);   // 布局按实际渲染格数选，不被总数带偏
    if (nc === 1) { cls = 'tg-1'; cells = cell(shown[0]); }
    else if (nc === 2) { cls = 'tg-2'; cells = shown.map(f => cell(f)).join(''); }
    else if (nc === 3) { cls = 'tg-3'; cells = cell(shown[0], ' big') + shown.slice(1, 3).map(f => cell(f)).join(''); }
    else if (nc === 4) { cls = 'tg-4'; cells = shown.map(f => cell(f)).join(''); }
    else { cls = 'tg-grid'; cells = shown.map((f, i) => cell(f, '', i === shown.length - 1 && more > 0 ? '<span class="tg-more">+' + more + '</span>' : '')).join(''); } // 5–9：网格填充，末行可留空
    // 图集原始地址列表挂在容器上：被“+N”隐掉的那几张也能在大图查看器里翻到
    const allImgs = JSON.stringify(media.filter(isImgFile).map(f => f.file_url));
    return '<div class="tg-media ' + cls + '" data-tgall="' + esc(allImgs) + '">' + cells + '</div>';
  }

  /* 生成一条消息气泡；m: {id, sender_id, self, content, msg_type, created_at, sender_name, filename, file_size} */
  function buildBubble(m, chatType) {
    const row = document.createElement('div');
    row.className = 'msg-row ' + (m.self ? 'out' : 'in');
    const bubble = document.createElement('div');
    bubble.className = 'bubble';
    bubble.dataset.id = m.id || ('tmp' + Date.now() + Math.random().toString(36).slice(2, 6));
    const d = parseDate(m.created_at);
    bubble.dataset.day = d ? d.toDateString() : '';
    bubble.dataset.created = m.created_at || '';   // 供“2 分钟内可撤回”判定

    let inner = '';
    let isMedia = false;
    let isFile = false;
    if (chatType === 'group' && !m.self && m.sender_name) inner += '<div class="sender"' + (m.sender_id ? ' data-pu="' + m.sender_id + '"' : '') + '>' + esc(dispName(m.sender_id, m.sender_name)) + '</div>';
    // 引用回复块：显示被引用者昵称 + 原文摘要，整块可点跳转定位到原消息
    if (m.reply_to && m.reply_to_content != null) {
      inner += '<div class="msg-quote" data-qid="' + m.reply_to + '">' +
        '<span class="mq-bar"></span><div class="mq-body">' +
        (m.reply_to_nickname ? '<div class="mq-nick">' + esc(m.reply_to_nickname) + '</div>' : '') +
        '<div class="mq-txt">' + esc(m.reply_to_content) + '</div></div></div>';
    }
    const mt = m.msg_type || 1;
    bubble.dataset.mt = mt;   // 供右键“转发”判定消息类型（文件/相册/卡片等）
    if (mt === 2) {
      inner += mediaImgHtml(m.content, 400, 'media');
      isMedia = true;
    } else if (mt === 3) {
      const fname = m.filename || nameFromUrl(m.content);
      if (isVideo(fname) || isVideo(m.content)) {
        // 视频文件：内联在线预览 + 原始文件下载链接
        inner += videoHtml(m.content, fname);
        isMedia = true;
      } else {
        // download 属性指定保存文件名：优先用消息携带的原始文件名，避免下载后变成时间戳名
        inner += '<a href="' + esc(m.content) + '" download="' + esc(fname) + '" style="color:inherit;text-decoration:none">' + fileCardHtml(m.content, m.filename, m.file_size) + '</a>';
        isFile = true;
      }
    } else if (mt === 4) {
      // 混排媒体消息（相册）：content 是 JSON 数组，一个气泡内渲染图/视频/文件卡片
      let items = [];
      try { items = JSON.parse(m.content) || []; } catch (e) { items = []; }
      if (items.length) {
        isMedia = true;
        inner += '<div class="media-grid">' + items.map((it) => {
          const u = it.url || '', n = it.name || nameFromUrl(u);
          if (it.type === 3) return mediaImgHtml(u, 400, 'media');
          if (it.type === 2) return videoHtml(u, n);
          return '<a class="grid-file" href="' + esc(u) + '" download="' + esc(n) + '">' + fileCardHtml(u, it.name, it.size) + '</a>';
        }).join('') + '</div>';
      } else {
        // 相册内容为空/解析失败：不留空气泡，给出占位
        inner += '<div class="txt">[消息已失效]</div>';
      }
    } else if (mt === 5) {
      // 转发频道帖子卡片：content 是 JSON，点击跳转源频道并打开该帖
      let card = {};
      try { card = JSON.parse(m.content) || {}; } catch (e) { card = {}; }
      inner += forwardPostCardHtml(card);
      isMedia = true;
    } else {
      inner += '<div class="txt">' + esc(m.content) + '</div>';
    }
    inner += '<span class="time">' + fmtTime(m.created_at) + '</span>';
    bubble.innerHTML = inner;
    if (isMedia) bubble.classList.add('media-bubble');
    if (isFile) bubble.classList.add('file-bubble');
    // 被@我：气泡加描边高亮
    if (m.mentions && m.mentions.indexOf && m.mentions.indexOf(S.me.id) >= 0) bubble.classList.add('mention-me');
    // 右击气泡弹出上下文菜单（复制/撤回/删除），不再用悬停叉号
    bubble.dataset.self = m.self ? '1' : '0';
    bubble.dataset.chatType = chatType || (S.current && S.current.type) || '';
    bubble.addEventListener('contextmenu', (e) => openMsgCtxMenu(e, bubble));
    // 对方消息：左侧带发送者头像；自己发的贴边不显头像（私聊/群聊一致）
    if (!m.self) {
      const ava = document.createElement('img');
      ava.className = 'msg-ava';
      ava.alt = '';
      ava.dataset.full = m.avatar || '';
      ava.src = m.avatar ? thumbUrl(m.avatar, 400) : letterAvatar(m.sender_name || '?');
      watchAvatar(ava, m.sender_name || '');
      row.appendChild(ava);
    }
    row.appendChild(bubble);
    return row;
  }

  /* 转发频道帖子 / 分享频道 的卡片 HTML（消息气泡内）：标题/摘要 + 来源频道，整卡可点跳转 */
  function forwardPostCardHtml(c) {
    if (!c) return '<div class="txt">[分享已失效]</div>';
    if (c.kind === 'channel') {
      const cover = c.channel_avatar ? '<div class="fwd-cover">' + mediaImgHtml(c.channel_avatar, 400, '') + '</div>' : '';
      const desc = c.description ? '<div class="fwd-sum">' + esc(c.description) + '</div>' : '';
      return '<div class="fwd-card fwd-share-card" data-fwd-ch="' + esc(c.channel_id || '') + '" data-ch-name="' + esc(c.channel_name || '') + '" data-ch-avatar="' + esc(c.channel_avatar || '') + '" data-ch-desc="' + esc(c.description || '') + '">' +
        '<div class="fwd-body"><div class="fwd-title">' + esc(c.channel_name || '频道') + '</div>' + desc +
        '<div class="fwd-src"><i class="ic ic-channel"></i><span>频道</span></div></div>' + cover + '</div>';
    }
    if (!c.post_id) return '<div class="txt">[转发已失效]</div>';
    const cover = c.cover ? '<div class="fwd-cover">' + mediaImgHtml(c.cover, 400, '') + '</div>' : '';
    const title = c.title ? '<div class="fwd-title">' + esc(c.title) + '</div>' : '';
    const sum = c.summary ? '<div class="fwd-sum">' + esc(c.summary) + '</div>' : '';
    return '<div class="fwd-card" data-fwd-ch="' + esc(c.channel_id || '') + '" data-fwd-post="' + esc(c.post_id) + '">' +
      '<div class="fwd-body">' + title + sum +
      '<div class="fwd-src"><i class="ic ic-channel"></i><span>' + esc(c.channel_name || '频道') + '</span></div></div>' +
      cover + '</div>';
  }

  /* 从气泡 DOM 归纳一段引用摘要（用于回复横幅展示）：优先文本，其次媒体/文件/转发占位 */
  function quoteFromBubble(bubble) {
    const t = $('.txt', bubble);
    if (t && t.textContent) return t.textContent.slice(0, 60);
    if ($('video', bubble)) return '[视频]';
    if ($('.file-card', bubble)) return '[文件]';
    if ($('img.media', bubble) || $('.media-grid', bubble)) return '[图片]';
    if ($('.fwd-card', bubble)) return '[转发]';
    return '';
  }

  function isNearBottom() {
    return msgScrollEl.scrollHeight - msgScrollEl.scrollTop - msgScrollEl.clientHeight < 140;
  }
  function scrollBottom(smooth) {
    msgScrollEl.scrollTo({ top: msgScrollEl.scrollHeight, behavior: smooth ? 'smooth' : 'auto' });
  }

  function appendMessage(m, chatType, opts) {
    opts = opts || {};
    // 去重
    if (m.id && msgListEl.querySelector('.bubble[data-id="' + m.id + '"]')) return;
    const curDay = (() => { const d = parseDate(m.created_at); return d ? d.toDateString() : null; })();
    const lastDay = (() => { const all = msgListEl.querySelectorAll('[data-day]'); return all.length ? all[all.length - 1].dataset.day : null; })();
    if (curDay && curDay !== lastDay && !opts.history) msgListEl.appendChild(daySepEl(fmtDay(m.created_at)));
    const near = opts.forceBottom || isNearBottom();
    msgListEl.appendChild(buildBubble(m, chatType || (S.current && S.current.type)));
    if (opts.pop) {
      const b = msgListEl.lastElementChild.querySelector('.bubble');
      b.classList.add('pop');
      setTimeout(() => b.classList.remove('pop'), 1700);
    }
    if (near) scrollBottom(opts.smooth !== false);
  }

  function renderHistory(messages, chatType) {
    msgListEl.innerHTML = '';
    let lastDay = null;
    messages.forEach(m => {
      const d = parseDate(m.created_at);
      const day = d ? d.toDateString() : null;
      if (day && day !== lastDay) { msgListEl.appendChild(daySepEl(fmtDay(m.created_at))); lastDay = day; }
      const isSelf = m.is_self !== undefined ? m.is_self : m.sender_id === S.me.id;
      msgListEl.appendChild(buildBubble({
        id: m.msg_id, sender_id: m.sender_id, self: isSelf,
        content: m.content, msg_type: m.msg_type, created_at: m.created_at,
        sender_name: m.sender_name, filename: m.filename, file_size: m.file_size,
        mentions: m.mentions,
        reply_to: m.reply_to, reply_to_nickname: m.reply_to_nickname, reply_to_content: m.reply_to_content,
        // 群聊用接口返回的 sender_avatar；私聊对方即会话对象，直接用当前会话头像
        avatar: isSelf ? null : (m.sender_avatar || (chatType === 'private' && S.current ? S.current.avatar : null))
      }, chatType));
    });
    scrollBottom(false);
  }

  /* ---------------- 历史加载 ---------------- */
  async function loadPrivate(peerId) {
    msgListEl.innerHTML = '<div class="loading-hint">加载中…</div>';
    try {
      const r = await api('/chat/messages?to_user_id=' + peerId + '&per_page=30');
      if (r.code !== 0) { msgListEl.innerHTML = ''; toast(r.msg || '加载失败'); return; }
      S.hasMore = r.data.has_more;
      const msgs = r.data.messages || [];
      S.oldestId = msgs.length ? msgs[0].msg_id : null;
      setChatSubPresence(r.data.peer_online, r.data.peer_presence);
      msgListEl.innerHTML = '';
      renderHistory(msgs, 'private');
    } catch (e) { msgListEl.innerHTML = ''; }
  }

  async function loadOlderPrivate() {
    const r = await api('/chat/messages?to_user_id=' + S.current.id + '&per_page=30&before_id=' + S.oldestId);
    if (r.code !== 0) return;
    S.hasMore = r.data.has_more;
    const msgs = r.data.messages || [];
    if (msgs.length) S.oldestId = msgs[0].msg_id;
    const prevH = msgScrollEl.scrollHeight;
    const frag = document.createDocumentFragment();
    let lastDay = (() => { const all = msgListEl.querySelectorAll('[data-day]'); return all.length ? all[0].dataset.day : null; })();
    msgs.forEach(m => {
      const d = parseDate(m.created_at); const day = d ? d.toDateString() : null;
      if (day && day !== lastDay) { const sep = daySepEl(fmtDay(m.created_at)); sep.dataset.prepend = '1'; frag.appendChild(sep); lastDay = day; }
      frag.appendChild(buildBubble({ id: m.msg_id, self: m.is_self, content: m.content, msg_type: m.msg_type, created_at: m.created_at, sender_name: m.sender_name, filename: m.filename, file_size: m.file_size, reply_to: m.reply_to, reply_to_nickname: m.reply_to_nickname, reply_to_content: m.reply_to_content }, 'private'));
    });
    msgListEl.prepend(frag);
    msgScrollEl.scrollTop = msgScrollEl.scrollHeight - prevH;
  }

  async function loadGroup(groupId) {
    msgListEl.innerHTML = '<div class="loading-hint">加载中…</div>';
    try {
      const r = await api('/group/' + groupId + '/messages?per_page=30');
      if (r.code !== 0) { msgListEl.innerHTML = ''; toast(r.msg || '你不是群成员'); return; }
      const msgs = r.data.messages || [];
      S.hasMore = r.data.has_more;
      S.oldestId = msgs.length ? msgs[0].msg_id : null;
      msgListEl.innerHTML = '';
      renderHistory(msgs, 'group');
      // 在线成员数 + 缓存成员供 @选择器/群管理使用
      try { const mr = await api('/group/' + groupId + '/members'); if (mr.code === 0) { S.groupMembers = mr.data.members || []; $('#chatSub').textContent = mr.data.total + ' 名成员'; } } catch (e) {}
    } catch (e) { msgListEl.innerHTML = ''; }
  }

  async function loadOlderGroup() {
    const r = await api('/group/' + S.current.id + '/messages?per_page=30&before_id=' + S.oldestId);
    if (r.code !== 0) return;
    S.hasMore = r.data.has_more;
    const msgs = r.data.messages || [];
    if (msgs.length) S.oldestId = msgs[0].msg_id;
    const prevH = msgScrollEl.scrollHeight;
    const frag = document.createDocumentFragment();
    msgs.forEach(m => frag.appendChild(buildBubble({ id: m.msg_id, self: m.sender_id === S.me.id, content: m.content, msg_type: m.msg_type, created_at: m.created_at, sender_name: m.sender_name, filename: m.filename, file_size: m.file_size, reply_to: m.reply_to, reply_to_nickname: m.reply_to_nickname, reply_to_content: m.reply_to_content }, 'group')));
    msgListEl.prepend(frag);
    msgScrollEl.scrollTop = msgScrollEl.scrollHeight - prevH;
  }

  /* ---------------- 频道：帖子流 ---------------- */
  function buildPostCard(p) {
    const el = document.createElement('div');
    el.className = 'post-card';
    const pid = p.post_id || p.id;
    el.dataset.id = pid;
    const mosaic = mediaMosaicHtml(p.files);
    const cc = (p.comment_count == null ? 0 : p.comment_count);
    el.innerHTML =
      '<div class="post-head">' +
      '<span class="post-author">' + esc((S.current && S.current.name) || '') + '</span>' +
      '<span class="post-time">' + esc(fmtRelTime(p.created_at)) + '</span></div>' +
      mosaic +
      (p.title ? '<div class="post-title">' + esc(p.title) + '</div>' : '') +
      '<div class="post-content">' + esc((p.content || '').length > 240 ? p.content.slice(0, 240) + '…' : p.content || '') + '</div>' +
      '<div class="post-foot"><button class="post-comment-link" data-post="' + pid + '">' + cc + ' 评论 <span class="pcl-arrow">›</span></button><span class="post-views">' + (p.view_count == null ? 0 : p.view_count) + ' 次浏览</span><button class="post-fwd" data-fwd="' + pid + '" title="转发"><i class="ic ic-forward"></i></button></div>';
    el.dataset.chatType = 'channel';
    el.dataset.self = (p.author_id && S.me && p.author_id === S.me.id) ? '1' : '0';
    // 仅“N 评论”入口进入详情，点卡片其它区域不跳转（参考图2）
    // 位于频道详情页（媒体视图）“我的帖子”内时：弹出“帖子+评论”弹窗，不跳到会话里的详情界面
    const link = $('.post-comment-link', el);
    if (link) link.addEventListener('click', (e) => { e.stopPropagation(); if (mediaViewEl && mediaViewEl.contains(el)) openPostCommentModal(pid); else openPostDetail(pid); });
    const fwdBtn = $('.post-fwd', el);
    if (fwdBtn) fwdBtn.addEventListener('click', (e) => { e.stopPropagation(); openForwardPicker(postMeta(p, pid)); });
    el.addEventListener('contextmenu', (e) => openMsgCtxMenu(e, el));
    return el;
  }

  /* 从流/详情数据组装转发卡片所需元信息（封面取首张图片） */
  function postMeta(p, pid) {
    const files = p.files || p.channel_file || [];
    const coverFile = files.find(f => isImgFile(f));
    return {
      post_id: pid,
      channel_id: (S.current && S.current.type === 'channel') ? S.current.id : (p.channel_id || null),
      channel_name: (S.current && S.current.name) || p.channel_name || '',
      title: p.title || '',
      summary: (p.content || '').slice(0, 120),
      cover: coverFile ? coverFile.file_url : null,
    };
  }

  async function loadChannel(channelId) {
    msgListEl.innerHTML = '<div class="loading-hint">加载中…</div>';
    try {
      const r = await api('/channel/post/get', { channel_id: channelId });
      msgListEl.innerHTML = '';
      if (r.code !== 0) { toast(r.msg || '加载频道失败'); return; }
      const posts = ((r.data && r.data.channel_post) || []).slice().sort((a, b) => b.post_id - a.post_id).reverse();
      const files = (r.data && r.data.channel_file) || [];
      if (!posts.length) { renderChannelEmpty(); return; }
      posts.forEach(p => {
        p.files = files.filter(f => f.post_id === p.post_id);
        msgListEl.appendChild(buildPostCard(p));
      });
      scrollBottom(false);
    } catch (e) { msgListEl.innerHTML = ''; }
  }

  /* 频道无帖子时的空状态：复用“未选中会话”那只到处飘的吉娃娃，点击冒爱心+头顶气泡，文案引导发帖 */
  function renderChannelEmpty() {
    msgListEl.innerHTML =
      '<div class="feed-empty">' +
        '<div class="ph-roam">' +
          '<div class="ph-hearts"></div>' +
          '<div class="ph-bubble">快来发布第一条帖子吧~</div>' +
          '<img class="ph-pet" src="/static/ico/little_jww.png" alt="小吉娃娃">' +
        '</div>' +
        '<div class="ph-text">快来发布第一条帖子吧</div>' +
      '</div>';
    initFeedPet($('.feed-empty', msgListEl));
  }
  function initFeedPet(ph) {
    if (!ph) return;
    const roam = $('.ph-roam', ph), hearts = $('.ph-hearts', ph), bubble = $('.ph-bubble', ph);
    if (!roam) return;
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const PET = 158;
    const PHRASES = ['快来发布第一条帖子吧~', '写点内容和大家聊聊叭~', '汪汪！点我干嘛~', '我是这个频道的看门狗~', '来都来了，发一条嘛~'];
    let x = 0, y = 0, tx = 0, ty = 0, placed = false, paused = false, idleUntil = 0, bubbleTimer = null;
    const bounds = () => ({ maxX: Math.max(0, ph.clientWidth - PET), maxY: Math.max(0, ph.clientHeight - PET) });
    const pick = () => { const b = bounds(); tx = Math.random() * b.maxX; ty = Math.random() * b.maxY; };
    const center = () => { const b = bounds(); x = b.maxX / 2; y = b.maxY / 2; tx = x; ty = y; roam.style.transform = 'translate(' + x + 'px,' + y + 'px)'; roam.classList.add('placed'); };
    // 元素被移除（切到有帖子的频道/别的会话）时自动停循环，避免残留泄漏
    function loop() {
      if (!ph.isConnected) return;
      requestAnimationFrame(loop);
      if (reduce || paused || !placed) return;
      const b = bounds();
      const dx = tx - x, dy = ty - y;
      if (Math.hypot(dx, dy) < 5) {
        const now = performance.now();
        if (now > idleUntil) { pick(); idleUntil = now + 1400 + Math.random() * 2800; }
      } else {
        x = Math.max(0, Math.min(b.maxX, x + dx * 0.012));
        y = Math.max(0, Math.min(b.maxY, y + dy * 0.012));
        roam.style.transform = 'translate(' + x.toFixed(1) + 'px,' + y.toFixed(1) + 'px)';
      }
    }
    (function ensure() {
      if (!ph.isConnected) return;
      if (placed) { loop(); return; }
      if (ph.clientWidth > PET) { center(); pick(); placed = true; loop(); return; }
      requestAnimationFrame(ensure);
    })();
    roam.addEventListener('mouseenter', () => paused = true);
    roam.addEventListener('mouseleave', () => paused = false);
    roam.addEventListener('click', () => {
      if (hearts) {
        const n = 5 + Math.floor(Math.random() * 4);
        for (let i = 0; i < n; i++) {
          const s = document.createElement('span');
          s.className = 'ph-heart';
          s.innerHTML = '<i class="ic ic-heart"></i>';
          s.style.setProperty('--dx', (Math.random() * 100 - 50).toFixed(0) + 'px');
          s.style.setProperty('--sz', (16 + Math.random() * 14).toFixed(0) + 'px');
          s.style.animationDelay = (Math.random() * 0.18).toFixed(2) + 's';
          hearts.appendChild(s);
          setTimeout(() => s.remove(), 1500);
        }
      }
      if (bubble) {
        bubble.textContent = PHRASES[Math.floor(Math.random() * PHRASES.length)];
        roam.classList.add('show-bubble');
        clearTimeout(bubbleTimer);
        bubbleTimer = setTimeout(() => roam.classList.remove('show-bubble'), 1700);
      }
    });
  }

  /* ---------------- 右击上下文菜单（Telegram 风格） ---------------- */
  let ctxEl = null;
  function closeCtxMenu() {
    if (!ctxEl) return;
    const el = ctxEl; ctxEl = null;
    el.classList.remove('show');
    setTimeout(() => el.remove(), 220);
  }
  function openCtxMenu(x, y, items) {
    closeCtxMenu();
    const el = document.createElement('div');
    el.className = 'ctx-menu';
    items.forEach(it => {
      const b = document.createElement('button');
      b.className = 'ctx-item' + (it.danger ? ' danger' : '');
      b.textContent = it.label;
      b.addEventListener('click', () => { closeCtxMenu(); it.fn(); });
      el.appendChild(b);
    });
    document.body.appendChild(el);
    const r = el.getBoundingClientRect();
    el.style.left = Math.max(8, Math.min(x, window.innerWidth - r.width - 8)) + 'px';
    el.style.top = Math.max(8, Math.min(y, window.innerHeight - r.height - 8)) + 'px';
    requestAnimationFrame(() => el.classList.add('show'));
    ctxEl = el;
  }

  /* ---------------- 图片/视频：放大 + 保存（电脑右击、手机长按同一套菜单） ---------------- */
  /* 从命中元素反推媒体信息：优先用拼贴格/放大按钮上的 data-*（原始地址），否则回退到元素自身 src */
  function mediaFromTarget(t) {
    if (!t || !t.closest) return null;
    if (t.closest('.avatar-viewer')) return null;      // 大图查看器里不需要再套一层菜单
    const exp = t.closest('.vid-expand');
    if (exp) return { kind: 'video', url: exp.dataset.vv, name: exp.dataset.vn || nameFromUrl(exp.dataset.vv), el: exp };
    const cell = t.closest('.tg-cell');
    if (cell && cell.dataset.tgvid) { const v = cell.querySelector('video'); return { kind: 'video', url: cell.dataset.tgvid, name: nameFromUrl(cell.dataset.tgvid), el: v || cell }; }
    if (cell && cell.dataset.tgimg) { const i = cell.querySelector('img'); return { kind: 'image', url: cell.dataset.tgimg, name: nameFromUrl(cell.dataset.tgimg), el: i || cell }; }
    const vc = t.closest('.vid-click');
    if (vc) { const v = vc.querySelector('video'); const u = v && (v.getAttribute('src') || v.currentSrc); if (u) return { kind: 'video', url: u, name: nameFromUrl(u), el: v }; }
    if (t.tagName === 'VIDEO') { const u = t.getAttribute('src') || t.currentSrc; if (u) return { kind: 'video', url: u, name: nameFromUrl(u), el: t }; }
    if (t.tagName === 'IMG') {
      const isIn = t.matches('img.media') || t.closest('.moment-media') || t.closest('.post-files') || t.closest('.media-grid') || t.closest('.fb-imgs') || t.closest('.ann-media') || t.closest('.mv-cell') || t.closest('.fwd-cover') || t.closest('.up-thumbs');
      if (!isIn) return null;                          // 头像/文件图标不参与
      const u = fullSrc(t);                            // 屏上是缩略图，另存为必须拿原图
      return { kind: 'image', url: u, name: nameFromUrl(u), el: t };
    }
    return null;
  }
  /* 保存媒体：远端对象存储的 download 属性会被浏览器忽略（跨源），必须 fetch 成 blob 再落盘；
     若服务端未回 CORS 头导致 fetch 失败，则退回新窗口打开，由用户长按/右击另存。
     过程中不弹“正在保存”提示，完成才报一次“保存成功” */
  async function saveMediaByUrl(url, name) {
    if (!url) return;
    const finalName = name || (String(url).split('/').pop().split('?')[0] || 'file');
    try {
      const res = await fetch(url, { mode: 'cors', credentials: 'omit' });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const blob = await res.blob();
      const href = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = href; a.download = finalName;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => { try { URL.revokeObjectURL(href); } catch (e) {} }, 10000);
      toast('保存成功');
    } catch (e) {
      try { window.open(url, '_blank'); } catch (_) {}
      toast('已在新窗口打开，可长按/右击另存');
    }
  }
  function zoomMedia(m) {
    if (!m || !m.url) return;
    const el = (m.el && document.contains(m.el)) ? m.el : null;
    if (m.kind === 'image') openAvatarViewer(m.url, el ? el.getBoundingClientRect() : null, false, '');
    else openVideoViewer(m.url);
  }
  /* ---- 另存为：优先弹系统“另存为”对话框（File System Access API，可选保存位置），
     浏览器不支持/用户环境受限时退回普通 blob 下载 ---- */
  const SAVE_AS_MIME = {
    pdf: 'application/pdf', doc: 'application/msword',
    docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    xls: 'application/vnd.ms-excel',
    xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    ppt: 'application/vnd.ms-powerpoint',
    pptx: 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    txt: 'text/plain', md: 'text/plain', csv: 'text/csv',
    jpg: 'image/jpeg', jpeg: 'image/jpeg', png: 'image/png', gif: 'image/gif', webp: 'image/webp',
    mp4: 'video/mp4', mp3: 'audio/mpeg', zip: 'application/zip', rar: 'application/vnd.rar',
  };
  async function saveAsFile(url, name) {
    if (!url) return;
    const finalName = name || nameFromUrl(url) || 'file';
    if (window.showSaveFilePicker) {
      try {
        const res = await fetch(url, { mode: 'cors', credentials: 'omit' });
        if (!res.ok) throw new Error('HTTP ' + res.status);
        const blob = await res.blob();
        const ext = (finalName.indexOf('.') >= 0 ? finalName.split('.').pop() : '').toLowerCase();
        const types = ext ? [{ description: finalName, accept: { [SAVE_AS_MIME[ext] || 'application/octet-stream']: ['.' + ext] } }] : undefined;
        const handle = await window.showSaveFilePicker({ suggestedName: finalName, types });
        const w = await handle.createWritable();
        await w.write(blob);
        await w.close();
        toast('保存成功');
        return;
      } catch (err) {
        if (err && err.name === 'AbortError') return;   // 用户在对话框点了取消，不降级
        // 其余异常（扩展名映射失败等）继续往下走普通下载
      }
    }
    saveMediaByUrl(url, finalName);
  }
  /* 菜单项：查看大图/放大播放 + 保存（放在最前面，与会话菜单里的回复/转发不互影响） */
  function mediaMenuItems(m) {
    if (!m || !m.url) return [];
    return [
      { label: m.kind === 'image' ? '查看大图' : '放大播放', fn: () => { if (m.kind === 'image') { const el = (m.el && document.contains(m.el)) ? m.el : null; openAvatarViewer(m.url, el ? el.getBoundingClientRect() : null, false, ''); } else openVideoViewer(m.url); } },
      { label: m.kind === 'image' ? '保存图片' : '保存视频', fn: () => saveMediaByUrl(m.url, m.name) },
    ];
  }
  /* ---- 文件卡片：另存为（在线预览暂时下线，逻辑保留待启用） ---- */
  /* 从命中元素反推文件信息：.file-card 外层 a 的 href 是原始地址，download 属性是原始文件名 */
  function fileFromTarget(t) {
    if (!t || !t.closest) return null;
    const card = t.closest('.file-card');
    if (!card) return null;
    const a = card.closest('a[href]');
    if (!a || !a.href) return null;
    const b = card.querySelector('b');
    const name = a.getAttribute('download') || (b ? b.getAttribute('title') : '') || nameFromUrl(a.href);
    return { url: a.href, name: name || '' };
  }
  /* 在线预览：PDF 用浏览器原生渲染（桌面弹层内嵌 iframe，手机新窗口）；
     Word/Excel/PPT 借 Microsoft Office 在线查看器，要求文件外链可公开访问（对象存储桶已公开读） */
  const OFFICE_PREVIEW_RE = /\.(docx?|pptx?|xlsx?)$/i;
  function previewFileByUrl(url, name) {
    const n = name || url;
    if (/\.pdf$/i.test(n)) {
      // 手机浏览器（含 DevTools 设备模拟）没有内置 PDF 查看器，直链新窗口只会触发下载；
      // 改走 Office 在线查看器，把 PDF 当网页渲染，任何手机浏览器都能看
      if (isMobile()) {
        toast('正在打开在线预览…');
        try { window.open('https://view.officeapps.live.com/op/view.aspx?src=' + encodeURIComponent(url), '_blank'); } catch (_) {}
        return;
      }
      openModal(name || 'PDF 预览', '<iframe src="' + esc(url) + '" style="display:block;width:100%;height:70vh;border:none;border-radius:12px;background:#fff"></iframe>', null);
      $('#modal').classList.add('wide');
      return;
    }
    if (OFFICE_PREVIEW_RE.test(n)) {
      toast('正在打开在线预览…');
      try { window.open('https://view.officeapps.live.com/op/view.aspx?src=' + encodeURIComponent(url), '_blank'); } catch (_) {}
      return;
    }
    toast('该类型暂不支持在线预览，请下载后查看');
  }
  function fileMenuItems(f) {
    if (!f || !f.url) return [];
    if (isMobile()) return [];   // 手机端不提供另存为（点卡片直接下载即可，系统也没有选路径的交互）
    return [
      // 在线预览暂时下线（预览逻辑 previewFileByUrl 保留，后续想开回来恢复此行即可）：
      // { label: '在线预览', fn: () => previewFileByUrl(f.url, f.name) },
      { label: '另存为', fn: () => saveAsFile(f.url, f.name) },
    ];
  }
  /* 点击文件卡片：统一改走 blob 保存还原原始文件名——跨源直链上 a[download] 属性会被浏览器忽略，
     不拦截的话保存下来的是对象存储里的 UUID 名 */
  document.addEventListener('click', (e) => {
    const a = e.target && e.target.closest ? e.target.closest('a[download]') : null;
    if (!a || !a.querySelector('.file-card')) return;   // 只接管文件卡片，其他带 download 的链接不管
    e.preventDefault();
    saveMediaByUrl(a.href, a.getAttribute('download') || nameFromUrl(a.href));
  });
  /* 视频右上角放大按钮：用捕获阶段拦截，避免同时触发“.vid-click 点击即播放”的委托 */
  document.addEventListener('click', (e) => {
    const btn = e.target && e.target.closest ? e.target.closest('.vid-expand') : null;
    if (!btn) return;
    e.preventDefault(); e.stopPropagation();
    zoomMedia(mediaFromTarget(btn));
  }, true);
  /* 右击（桌面）：会话气泡内的媒体已在气泡菜单里带保存项（会先 preventDefault），
     这里只接管动态/频道拼贴/资料页等气泡外的媒体 */
  document.addEventListener('contextmenu', (e) => {
    if (e.defaultPrevented) return;
    const m = mediaFromTarget(e.target);
    if (m) {
      e.preventDefault();
      openCtxMenu(e.clientX, e.clientY, mediaMenuItems(m));
      return;
    }
    // 气泡外的文件卡片（群文件列表等）：同款“另存为”菜单
    const f = fileFromTarget(e.target);
    if (f) {
      e.preventDefault();
      openCtxMenu(e.clientX, e.clientY, fileMenuItems(f));
    }
  });
  /* 长按（手机/触控笔，也支持鼠标左键长按）：同上，会话区已有专属长按实现，不重复接管 */
  (function bindMediaLongPress() {
    let timer = null, sx = 0, sy = 0, tgt = null, pid = null;
    const clear = () => { if (timer) { clearTimeout(timer); timer = null; } tgt = null; pid = null; };
    const swallow = (ev) => { ev.stopPropagation(); ev.preventDefault(); };
    document.addEventListener('pointerdown', (e) => {
      if (e.pointerType === 'mouse' && e.button !== 0) return;
      if (e.target.closest && (e.target.closest('.bubble') || e.target.closest('.ctx-menu') || e.target.closest('input, textarea, button:not(.vid-expand)'))) return;
      const m = mediaFromTarget(e.target);
      if (!m) return;
      clear();
      tgt = e.target; pid = e.pointerId; sx = e.clientX; sy = e.clientY;
      timer = setTimeout(() => {
        timer = null;
        if (!tgt || !document.contains(tgt)) return;
        if (navigator.vibrate) { try { navigator.vibrate(8); } catch (_) {} }
        // 吞掉长按后紧跟的 click，避免菜单弹出同时又开了大图
        document.addEventListener('click', swallow, { capture: true, once: true });
        setTimeout(() => document.removeEventListener('click', swallow, { capture: true }), 600);
        openCtxMenu(sx, sy, mediaMenuItems(m));
      }, 480);
    });
    document.addEventListener('pointermove', (e) => {
      if (!timer || e.pointerId !== pid) return;
      if (Math.abs(e.clientX - sx) > 12 || Math.abs(e.clientY - sy) > 12) clear();
    });
    document.addEventListener('pointerup', (e) => { if (e.pointerId === pid) clear(); });
    document.addEventListener('pointercancel', clear);
  })();

  function openMsgCtxMenu(e, el) {
    if (el.classList.contains('withdrawn')) return;
    e.preventDefault(); e.stopPropagation();
    const type = el.dataset.chatType || (S.current && S.current.type);
    const rawId = el.dataset.id;
    const real = rawId && !String(rawId).startsWith('tmp');
    const items = [];
    // 点在图片/视频上：置顶“查看大图 + 保存”（电脑右击与手机长按共用本菜单）
    const hitMedia = mediaFromTarget(e.target);
    if (hitMedia) items.push(...mediaMenuItems(hitMedia));
    // 点在文件卡片上：置顶“另存为”
    const hitFile = fileFromTarget(e.target);
    if (hitFile) items.push(...fileMenuItems(hitFile));
    const txt = $('.txt', el) || $('.post-content', el);
    // 引用回复（仅私聊/群聊、已落库的真实消息）：取被回复消息的昵称与摘要，进入回复态
    if (real && (type === 'private' || type === 'group')) {
      items.push({ label: '回复', fn: () => {
        let nick;
        if (el.dataset.self === '1') nick = '我';
        else if (type === 'group') { const sn = $('.sender', el); nick = sn ? sn.textContent : (S.current && S.current.name); }
        else nick = (S.current && S.current.name) || '';
        const q = quoteFromBubble(el);
        enterMsgReply(rawId, nick, q);
      }});
    }
    if (txt && txt.textContent) items.push({ label: '复制文本', fn: () => {
      if (navigator.clipboard) navigator.clipboard.writeText(txt.textContent).then(() => toast('已复制')).catch(() => toast('复制失败'));
    }});
    // 转发（仅私聊/群聊真实消息，转发/分享卡片 msg_type=5 不可再次转发）
    if (real && (type === 'private' || type === 'group') && el.dataset.mt !== '5') {
      items.push({ label: '转发', fn: () => forwardMessage(el, type) });
    }
    if (el.dataset.self === '1' && real && type !== 'channel') {
      const cd = parseDate(el.dataset.created);
      const within = !cd || (Date.now() - cd.getTime()) <= 120000; // 超 2 分钟不再提供撤回（后端仍会兼校）
      if (within) items.push({ label: '撤回', fn: () => withdrawMessage(el, type) });
    }
    if (real) {
      // 频道帖子：仅频道主(role===1)可删任意帖，副频道主(role===2)仅可删自己发的帖；其余会话保持原逻辑
      if (type === 'channel') {
        const role = S.current && S.current.role;
        if (role === 1 || (role === 2 && el.dataset.self === '1')) {
          items.push({ label: '删除', danger: true, fn: () => deleteMessage(el, type) });
        }
      } else {
        items.push({ label: '删除', danger: true, fn: () => deleteMessage(el, type) });
      }
    }
    if (!items.length) return;
    openCtxMenu(e.clientX, e.clientY, items);
  }
  /* 频道帖子评论气泡：右键菜单（复制 / 回复他人 / 自己：撤回+删除） */
  function openCommentCtxMenu(e, el) {
    const items = [];
    const self = el.dataset.self === '1';
    const txt = $('.txt', el);
    if (txt && txt.textContent) items.push({ label: '复制文本', fn: () => {
      if (navigator.clipboard) navigator.clipboard.writeText(txt.textContent).then(() => toast('已复制')).catch(() => toast('复制失败'));
    }});
    if (!self && el.dataset.cid) items.push({ label: '回复', fn: () => {
      const ta = $('#postText'); if (!ta || ta.hidden) { toast('加入频道后才能回复'); return; }
      enterCmtReply(el.dataset.cid, el.dataset.nick, el.dataset.content); ta.focus();
    }});
    if (self && el.dataset.cid) {
      const cd = parseDate(el.dataset.created);
      const within = !cd || (Date.now() - cd.getTime()) <= 120000; // 超 2 分钟不再提供撤回（后端仍会兼校）
      if (within) items.push({ label: '撤回', fn: () => withdrawComment(el) });
      items.push({ label: '删除', danger: true, fn: () => deleteChannelComment(el) });
    }
    if (!items.length) return;
    openCtxMenu(e.clientX, e.clientY, items);
  }
  async function withdrawComment(el) {
    const cid = +el.dataset.cid;
    if (!cid) { toast('无法撤回该评论'); return; }
    try {
      const r = await api('/channel/comment/withdraw', { comment_id: cid });
      if (r.code === 0) {
        (el.closest('.msg-row') || el).remove();
        const cnt = $('#pdCommentCount'); if (cnt) cnt.textContent = Math.max(0, ((+cnt.textContent) || 0) - 1);
        toast('已撤回');
      } else toast(r.msg || '撤回失败');
    } catch (e) { toast('撤回失败'); }
  }
  // 删除自己发的频道评论
  async function deleteChannelComment(el) {
    const cid = +el.dataset.cid;
    if (!cid) { toast('无法删除该评论'); return; }
    try {
      const r = await api('/channel/comment/delete', { comment_id: cid });
      if (r.code === 0) {
        (el.closest('.msg-row') || el).remove();
        const cnt = $('#pdCommentCount'); if (cnt) cnt.textContent = Math.max(0, ((+cnt.textContent) || 0) - 1);
        toast('已删除');
      } else toast(r.msg || '删除失败');
    } catch (e) { toast('删除失败'); }
  }
  // 评论气泡经 innerHTML 插入，用事件委托绑定右键（不干扰消息/帖子气泡自身的 contextmenu）
  if (msgListEl) msgListEl.addEventListener('contextmenu', (e) => {
    const b = e.target.closest('.cmt-bubble');
    if (!b) return;
    e.preventDefault();
    openCommentCtxMenu(e, b);
  });
  // 点击气泡内引用块：平滑滚动定位到原消息并闪烁高亮（原消息在当前已加载范围内才可跳转）
  if (msgListEl) msgListEl.addEventListener('click', (e) => {
    const q = e.target.closest('.msg-quote');
    if (!q) return;
    const qid = q.dataset.qid;
    const target = msgListEl.querySelector('.bubble[data-id="' + qid + '"]');
    if (!target) { toast('原始消息不在当前加载范围'); return; }
    target.scrollIntoView({ behavior: 'smooth', block: 'center' });
    target.classList.add('quote-flash');
    setTimeout(() => target.classList.remove('quote-flash'), 1400);
  });
  /* 移动端长按唤出与右键相同的上下文菜单：改用 Pointer Events（与“动态”tab 长按同款，兼容性最好，
     部分手机浏览器在禁选择/禁 callout 下不再派发 touch 计时事件），仅对 touch/pen 生效，桌面仍走右键；
     480ms 内移动超阈值/提前抬起则取消；触发后用一次性捕获 click 吞掉随之而来的点击，避免误开大图/误跳转 */
  if (msgListEl) (function bindLongPress(root) {
    let timer = null, sx = 0, sy = 0, tgt = null, pid = null;
    const clear = () => { if (timer) { clearTimeout(timer); timer = null; } tgt = null; pid = null; };
    const swallow = (e) => { e.stopPropagation(); e.preventDefault(); };
    root.addEventListener('pointerdown', (e) => {
      // 触屏/触控笔长按，以及鼠标左键长按都支持；鼠标右键本就走原生 contextmenu，忽略避免重复
      if (e.pointerType === 'mouse' && e.button !== 0) return;
      clear();
      tgt = e.target; pid = e.pointerId;
      if (tgt.closest && tgt.closest('input, textarea, [contenteditable="true"], button')) return;
      sx = e.clientX; sy = e.clientY;
      timer = setTimeout(() => {
        timer = null;
        if (!tgt || !root.contains(tgt)) return;
        if (navigator.vibrate) { try { navigator.vibrate(8); } catch (_) {} }
        // 一次性吞掉长按后紧跟的 click（菜单在 body 内不受影响），600ms 后自动失效避免误吞下次正常点击
        root.addEventListener('click', swallow, { capture: true, once: true });
        setTimeout(() => root.removeEventListener('click', swallow, { capture: true }), 600);
        tgt.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: sx, clientY: sy }));
      }, 480);
    });
    root.addEventListener('pointermove', (e) => {
      if (!timer || e.pointerId !== pid) return;
      if (Math.abs(e.clientX - sx) > 12 || Math.abs(e.clientY - sy) > 12) clear();
    });
    root.addEventListener('pointerup', (e) => { if (e.pointerId === pid) clear(); });
    root.addEventListener('pointercancel', clear);
  })(msgListEl);
  document.addEventListener('click', (e) => { if (ctxEl && !e.target.closest('.ctx-menu')) closeCtxMenu(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeCtxMenu(); });
  window.addEventListener('blur', closeCtxMenu);
  if (msgScrollEl) msgScrollEl.addEventListener('scroll', closeCtxMenu);

  // 媒体图片加载失败（文件已被删除/失效）：不留只剩时间的空气泡，替换为占位文案
  if (msgListEl) msgListEl.addEventListener('error', (e) => {
    const t = e.target;
    if (t && t.tagName === 'IMG' && t.classList.contains('media')) {
      const ph = document.createElement('div');
      ph.className = 'txt';
      ph.textContent = '[图片已失效]';
      t.replaceWith(ph);
    }
  }, true);

  /* ---------------- 撤回消息（硬性规则：后端直接从库硬删，界面整条移除） ---------------- */
  async function withdrawMessage(bubbleEl, chatType) {
    const id = +bubbleEl.dataset.id;
    const url = chatType === 'private' ? '/chat/message/withdraw' : '/group/message/withdraw';
    try {
      const r = await api(url, { message_id: id });
      if (r.code === 0) {
        if (window.SFX) SFX.delete && SFX.delete();
        dissolveRow(bubbleEl.closest('.msg-row') || bubbleEl);   // 粒子化移除整行，并清理孤立日期条
        reloadConversations();  // 后端已重算会话摘要，刷新列表避免残留 [图片]/[文件]
        // 文本消息：原文回填输入框，方便重新编辑发送（媒体消息后端不返回文本）
        const back = r.data && r.data.original_content;
        if (back) {
          const input = $('#msgInput');
          input.value = back;
          input.focus();
          autoresize(input);
        }
        toast('已撤回');
      } else toast(r.msg || '撤回失败');
    } catch (e) { toast('撤回失败'); }
  }

  /* ---------------- 删除消息（粒子分散特效） ---------------- */
  async function deleteMessage(bubbleEl, chatType) {
    const id = bubbleEl.dataset.id;
    if (!id || String(id).startsWith('tmp')) { bubbleEl.remove(); return; }
    let url, body;
    if (chatType === 'private') { url = '/chat/message/delete'; body = { message_id: +id }; }
    else if (chatType === 'group') { url = '/group/message/delete'; body = { message_id: +id }; }
    else { url = '/channel/post/delete'; body = { post_id: +id }; }
    try {
      const r = await api(url, body);
      if (r.code === 0) {
        if (window.SFX) SFX.delete();
        dissolveRow(bubbleEl.closest('.msg-row') || bubbleEl);
        reloadConversations();  // 单向删除后本侧会话摘要可能回退，刷新列表
      } else {
        toast(r.msg || '无权删除');
      }
    } catch (e) { toast('删除失败'); }
  }

  /* ---------------- 发送 ---------------- */
  function sendText() {
    const input = $('#msgInput');
    const text = input.value.trim();
    if (!text || !S.current) return;
    const rep = S.msgReply;  // 待发送的引用回复（可为空）
    if (S.current.type === 'private') {
      S.sock.emit('send_private_msg', { to_user_id: S.current.id, msg: text, reply_to: rep ? rep.id : null }, (ack) => {
        if (ack && ack.code === 200) {
          appendMessage({ id: ack.msg_id, self: true, content: text, msg_type: 1, created_at: ack.created_at, reply_to: ack.reply_to, reply_to_nickname: ack.reply_to_nickname, reply_to_content: ack.reply_to_content }, 'private', { forceBottom: true });
          if (window.SFX) SFX.msgOut();
          bumpConversation('private', S.current.id, text);
          input.value = ''; autoresize(input); exitMsgReply();
        } else toast(ack && ack.msg ? ack.msg : '发送失败');
      });
    } else if (S.current.type === 'group') {
      const mentions = (S.pendingMentions || []).slice();
      S.sock.emit('send_group_msg', { group_id: S.current.id, msg: text, mentions: mentions, reply_to: rep ? rep.id : null }, (ack) => {
        if (ack && ack.code === 200) { if (window.SFX) SFX.msgOut(); input.value = ''; autoresize(input); S.pendingMentions = []; hideMentionPop(); exitMsgReply(); /* 气泡由房间广播回显 */ }
        else toast(ack && ack.msg ? ack.msg : '发送失败');
      });
    }
  }

  /* 私聊/群聊回复态：输入框上方横幅显示“回复 @X · 原文”，发送时带 reply_to */
  function enterMsgReply(id, nick, quote) {
    if (!id) return;
    S.msgReply = { id: +id, nick: nick || '' };
    const bar = $('#msgReplyBar');
    if (bar) {
      bar.hidden = false;
      bar.innerHTML =
        '<span class="mrb-bar"></span><div class="mrb-body">' +
        '<div class="mrb-head"><span>回复 @' + esc(nick || '') + '</span><button class="mrb-x" type="button" title="取消回复">✕</button></div>' +
        (quote ? '<div class="mrb-quote">' + esc(quote) + '</div>' : '') + '</div>';
    }
    const input = $('#msgInput'); if (input) input.focus();
  }
  function exitMsgReply() {
    S.msgReply = null;
    const bar = $('#msgReplyBar'); if (bar) { bar.hidden = true; bar.innerHTML = ''; }
  }
  // 回复横幅 ✕ 取消；切换会话时清除残留回复态
  document.addEventListener('click', (e) => { if (e.target.closest('.mrb-x')) exitMsgReply(); });

  // 底部发送按钮：评论详情模式下发送评论（帖子发布已改为“发帖”按钮 + 弹窗）
  function sendComment() {
    const ta = $('#postText');
    const text = ta.value.trim();
    if (!text || !S.openPostRoom) return;
    const payload = { channel_id: S.openPostRoom.channel_id, post_id: S.openPostRoom.post_id, comment: text };
    if (S.commentReply) payload.reply_to = S.commentReply.id;
    S.sock.emit('send_channel_comment', payload, (ack) => {
      if (ack && ack.code === 200) { ta.value = ''; autoresize(ta); exitCmtReply(); if (window.SFX) SFX.msgOut(); }
      else toast(ack && ack.msg ? ack.msg : '评论失败');
    });
  }
  /* 频道帖子评论回复态：底部横幅显示“回复 @X”，发送时带 reply_to */
  function enterCmtReply(cid, nick, quote) {
    S.commentReply = { id: +cid, nick: nick || '' };
    const bar = $('#commentReplyBar');
    if (bar) {
      bar.hidden = false;
      bar.innerHTML =
        (quote ? '<div class="crr-quote">' + esc(quote) + '</div>' : '') +
        '<div class="crr-row"><span>回复 @' + esc(nick || '') + '</span><button class="crr-x" type="button" title="取消回复">✕</button></div>';
    }
    const ta = $('#postText'); if (ta) ta.placeholder = '回复 @' + (nick || '') + '…';
  }
  function exitCmtReply() {
    S.commentReply = null;
    const bar = $('#commentReplyBar'); if (bar) { bar.hidden = true; bar.innerHTML = ''; }
    const ta = $('#postText'); if (ta) ta.placeholder = '发布评论…';
  }
  // 评论回复横幅 ✕ 取消（回复入口已改为评论气泡右击菜单）
  document.addEventListener('click', (e) => {
    if (e.target.closest('.crr-x')) exitCmtReply();
  });

  /* 发帖弹窗：标题 + 正文 + 附件，走 REST /channel/publish/post（multipart） */
  function openPublishModal() {
    if (!S.current || S.current.type !== 'channel' || S.current.role === 3) return;
    openModal('发布帖子',
      '<div class="field"><input id="pubTitle" class="modal-input" placeholder=" " autocomplete="off"></div><div class="modal-hint" style="margin:-14px 0 6px">标题（必填）</div>' +
      '<div class="field"><textarea id="pubContent" class="modal-input pub-textarea" rows="5" placeholder=" "></textarea></div><div class="modal-hint" style="margin:-14px 0 6px">正文（必填）</div>' +
      '<div class="pub-files"><input type="file" id="pubFiles" multiple hidden><button class="mini-btn blue" id="pubFilesBtn" type="button">选择附件（可选）</button><span class="pub-file-names" id="pubFileNames"></span></div>',
      publishSubmit, '发布');
    $('#pubFilesBtn').addEventListener('click', () => $('#pubFiles').click());
    $('#pubFiles').addEventListener('change', () => { const fs = $('#pubFiles').files; $('#pubFileNames').textContent = fs.length ? ('已选 ' + fs.length + ' 个文件') : ''; });
  }

  async function publishSubmit() {
    const title = $('#pubTitle').value.trim();
    const content = $('#pubContent').value.trim();
    if (!title || !content) { toast('标题和正文不能为空'); return; }
    const channelId = S.current.id;
    // 关窗前捕获附件 File 引用与弹窗位置（模态 DOM 销毁后 input.files 会失效）
    const files = $('#pubFiles') ? Array.from($('#pubFiles').files || []) : [];
    const modalEl = $('#modal');
    const rect = modalEl.getBoundingClientRect();
    const bg = getComputedStyle(modalEl).backgroundColor;

    // 文字与全部附件一次性提交：后端原子建帖并带文件，广播 receive_post 时图文一起出现，不再“先文字后补图”
    const fd = new FormData();
    fd.append('channel_id', channelId);
    fd.append('title', title);
    fd.append('content', content);
    files.forEach(f => fd.append('file', f));

    // 弹窗“框线”化作圆球飞向右上角，落地后成为上传进度环；关闭真实弹窗由圆球无缝接管
    const orb = startPublishOrb(rect, bg);
    closeModal();

    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/channel/publish/post', true);
    xhr.withCredentials = true;
    // 上传进度驱动圆环（预留 8% 给服务端落库处理，完成时由 succeed 补满）
    xhr.upload.onprogress = (e) => { if (e.lengthComputable && orb) orb.setProgress((e.loaded / e.total) * 0.92); };
    xhr.onload = () => {
      let r = null; try { r = JSON.parse(xhr.responseText); } catch (e) {}
      if (r && r.code === 0) {
        if (orb) orb.succeed();
        if (window.SFX) SFX.msgOut();
        toast('发布成功');
        if (S.channelView !== 'detail' && S.current && S.current.type === 'channel' && S.current.id === channelId) loadChannel(channelId);
      } else {
        if (orb) orb.fail();
        toast((r && r.msg) || '发布失败');
      }
    };
    xhr.onerror = () => { if (orb) orb.fail(); toast('发布失败'); };
    xhr.send(fd);
    // 无附件时上传几乎瞬间完成，给圆环一个起步进度避免长时间空转
    if (!files.length && orb) orb.setProgress(0.5);
  }

  /* 发布圆球：从弹窗位置化作圆形飞向右上角，成为上传进度环；完成→打勾淡出，失败→红色感叹号淡出 */
  function startPublishOrb(rect, bg) {
    const old = document.getElementById('pubOrb'); if (old) old.remove();
    const SIZE = 54, FLY = 560;
    const orb = document.createElement('div');
    orb.id = 'pubOrb';
    orb.className = 'pub-orb';
    orb.style.left = rect.left + 'px';
    orb.style.top = rect.top + 'px';
    orb.style.width = rect.width + 'px';
    orb.style.height = rect.height + 'px';
    if (bg) orb.style.background = bg;
    orb.innerHTML =
      '<svg class="pub-orb-ring" viewBox="0 0 48 48" aria-hidden="true">' +
        '<circle class="por-track" cx="24" cy="24" r="20"></circle>' +
        '<circle class="por-prog" cx="24" cy="24" r="20"></circle>' +
      '</svg>' +
      '<span class="pub-orb-mark"></span>';
    document.body.appendChild(orb);
    const prog = orb.querySelector('.por-prog');
    const C = 2 * Math.PI * 20;
    prog.style.strokeDasharray = C;
    prog.style.strokeDashoffset = C;
    let landed = false, pending = 0, finished = false, wantFinish = null;
    const apply = (p) => { prog.style.strokeDashoffset = C * (1 - Math.max(0, Math.min(1, p))); };
    const doFinish = () => {
      finished = true;
      apply(1);
      orb.classList.add(wantFinish === 'ok' ? 'succeeded' : 'failed');
      const hold = wantFinish === 'ok' ? 560 : 1000;
      setTimeout(() => { orb.classList.add('vanish'); setTimeout(() => orb.remove(), 460); }, hold);
    };
    const tLeft = Math.max(12, window.innerWidth - SIZE - 22);
    const tTop = 176;
    requestAnimationFrame(() => {
      orb.classList.add('flying');
      orb.style.left = tLeft + 'px';
      orb.style.top = tTop + 'px';
      orb.style.width = SIZE + 'px';
      orb.style.height = SIZE + 'px';
    });
    setTimeout(() => { landed = true; orb.classList.add('landed'); if (pending) apply(pending); if (wantFinish && !finished) doFinish(); }, FLY);
    return {
      setProgress(p) { if (finished) return; if (landed) apply(p); else pending = Math.max(pending, p); },
      succeed() { if (finished || wantFinish) return; wantFinish = 'ok'; if (landed) doFinish(); },
      fail() { if (finished || wantFinish) return; wantFinish = 'fail'; if (landed) doFinish(); },
    };
  }

  /* ---------------- 乐观上传：选文件即渲染带进度气泡，上传期间不阻塞其它操作 ---------------- */
  /* 在途上传计数（按会话）：群聊自回声抑制用；>0 表示该会话有本地乐观气泡正负责呈现，回声不再重复渲染 */
  S.pendingUploads = {};
  function bumpPending(key, delta) {
    const n = (S.pendingUploads[key] || 0) + delta;
    if (n > 0) S.pendingUploads[key] = n; else delete S.pendingUploads[key];
  }
  const UP_IMG_RE = /\.(png|jpe?g|gif|webp|bmp|avif)$/i;
  function fileKind(f) {
    const n = (f && f.name) || '';
    if ((f && f.type && f.type.indexOf('image/') === 0) || UP_IMG_RE.test(n)) return 'image';
    if ((f && f.type && f.type.indexOf('video/') === 0) || isVideo(n)) return 'video';
    return 'file';
  }
  /* 依据预览项生成临时上传气泡：单个→图/视频/文件卡片；多个→相册网格（与后端合并策略一致）；叠加进度条+百分比 */
  function buildUploadBubble(items) {
    const row = document.createElement('div');
    row.className = 'msg-row out';
    const bubble = document.createElement('div');
    bubble.className = 'bubble uploading';
    bubble.dataset.uptemp = '1';
    const cellHtml = (it) => {
      if (it.kind === 'image') return '<img class="media" src="' + esc(it.src) + '" alt="">';
      if (it.kind === 'video') return videoHtml(it.src, it.name);
      return fileCardHtml(it.src, it.name, it.size);
    };
    if (items.length === 1) {
      bubble.classList.add(items[0].kind === 'file' ? 'file-bubble' : 'media-bubble');
      bubble.innerHTML = cellHtml(items[0]);
    } else {
      bubble.classList.add('media-bubble');
      bubble.innerHTML = '<div class="media-grid">' + items.map((it) =>
        it.kind === 'file' ? '<div class="grid-file" style="pointer-events:none">' + cellHtml(it) + '</div>' : cellHtml(it)
      ).join('') + '</div>';
    }
    const bar = document.createElement('div'); bar.className = 'up-progress';
    const fill = document.createElement('div'); fill.className = 'up-progress__fill';
    bar.appendChild(fill);
    const pct = document.createElement('div'); pct.className = 'up-pct'; pct.textContent = '0%';
    bubble.appendChild(bar); bubble.appendChild(pct);
    row.appendChild(bubble);
    return { row, bubble, fill, pct };
  }
  /* 发起一次乐观上传：先渲染临时气泡，send(progressCb) 返回 Promise<{ok, real?, msg?}>；成功就地替换为真实气泡，失败给重试/删除 */
  function startOptimisticUpload(opt) {
    const { row, bubble, fill, pct } = buildUploadBubble(opt.items);
    msgListEl.appendChild(row);
    scrollBottom(true);
    const blobUrls = opt.items.map((it) => it.src).filter((s) => s && s.indexOf('blob:') === 0);
    function clearActions() { const a = $('.up-fail-actions', bubble); if (a) a.remove(); fill.style.background = ''; }
    function failMark(msg) {
      bubble.classList.remove('uploading'); bubble.classList.add('upload-failed');
      fill.style.width = '100%'; fill.style.background = '#e53935';
      pct.textContent = '✕'; pct.classList.add('fail');
      const acts = document.createElement('div'); acts.className = 'up-fail-actions';
      const rb = document.createElement('button'); rb.type = 'button'; rb.className = 'up-retry'; rb.textContent = '重试';
      const db = document.createElement('button'); db.type = 'button'; db.className = 'up-remove'; db.textContent = '删除';
      rb.addEventListener('click', (e) => { e.stopPropagation(); attempt(); });
      db.addEventListener('click', (e) => { e.stopPropagation(); blobUrls.forEach((u) => { try { URL.revokeObjectURL(u); } catch (err) {} }); row.remove(); });
      acts.appendChild(rb); acts.appendChild(db); bubble.appendChild(acts);
      if (msg) toast(msg);
    }
    function attempt() {
      bumpPending(opt.key, 1);
      bubble.classList.remove('upload-failed'); bubble.classList.add('uploading');
      clearActions();
      fill.style.width = '0%'; pct.textContent = '0%'; pct.classList.remove('fail');
      return opt.send((p) => {
        const v = Math.max(0, Math.min(1, p));
        fill.style.width = (v * 100).toFixed(0) + '%';
        pct.textContent = Math.round(v * 100) + '%';
      }).then((res) => {
        bumpPending(opt.key, -1);
        if (res && res.ok) {
          const real = buildBubble(res.real, opt.chatType);
          // 临时气泡被就地替换、不触发滚动，而缩略图是“进了视口才入池”；刚上传完的图直接插队加载，避免只剩时间的空气泡
          $$('img.media', real).forEach((im) => { loadImgNow(im); });
          // 上传完成直接替换内容，不播放任何入场/弹出动效
          const rb = real.querySelector('.bubble');
          if (rb) rb.style.animation = 'none';
          if (row.isConnected) row.replaceWith(real);
          blobUrls.forEach((u) => { try { URL.revokeObjectURL(u); } catch (e) {} });
          if (window.SFX) SFX.msgOut();
          bumpConversation(opt.chatType, opt.targetId, opt.preview(res.real));
        } else {
          failMark(res && res.msg);
        }
      }, () => { bumpPending(opt.key, -1); failMark('上传失败'); });
    }
    return attempt();
  }
  /* 发送器：私聊单文件 / 群聊多文件合并为一条消息，均用 XHR 取真实上传进度（fetch 无 upload.onprogress） */
  function makeUploader(chatType, targetId, files) {
    if (chatType === 'private') {
      return (progress) => new Promise((resolve) => {
        const fd = new FormData(); fd.append('to_user_id', targetId); fd.append('file', files[0]);
        const xhr = new XMLHttpRequest();
        xhr.open('POST', '/chat/message/file', true); xhr.withCredentials = true;
        xhr.upload.onprogress = (e) => { if (e.lengthComputable && progress) progress(e.loaded / e.total); };
        xhr.onload = () => {
          let r = null; try { r = JSON.parse(xhr.responseText); } catch (e) {}
          if (r && r.code === 0) resolve({ ok: true, real: { id: r.data.msg_id, self: true, content: r.data.file_url, msg_type: r.data.msg_type, created_at: new Date().toISOString(), filename: r.data.filename, file_size: r.data.file_size } });
          else resolve({ ok: false, msg: (r && r.msg) || '发送失败' });
        };
        xhr.onerror = () => resolve({ ok: false, msg: '网络异常，发送失败' });
        xhr.send(fd);
      });
    }
    return (progress) => new Promise((resolve) => {
      const fd = new FormData(); fd.append('group_id', targetId); files.forEach((f) => fd.append('file', f));
      const xhr = new XMLHttpRequest();
      xhr.open('POST', '/group/publish/file', true); xhr.withCredentials = true;
      xhr.upload.onprogress = (e) => { if (e.lengthComputable && progress) progress(e.loaded / e.total); };
      xhr.onload = () => {
        let r = null; try { r = JSON.parse(xhr.responseText); } catch (e) {}
        if (r && r.code === 0) {
          const fs = r.data.files || [];
          let mt, content;
          if (fs.length === 1) { mt = fs[0].file_type === 3 ? 2 : 3; content = fs[0].file_url; }
          else { mt = 4; content = JSON.stringify(fs.map((f) => ({ url: f.file_url, type: f.file_type, name: f.filename, size: f.file_size }))); }
          resolve({ ok: true, real: { id: r.data.msg_id, self: true, sender_id: S.me.id, content: content, msg_type: mt, created_at: new Date().toISOString(), filename: fs.length === 1 ? fs[0].filename : undefined, file_size: fs.length === 1 ? fs[0].file_size : undefined } });
        } else resolve({ ok: false, msg: (r && r.msg) || '上传失败' });
      };
      xhr.onerror = () => resolve({ ok: false, msg: '网络异常，上传失败' });
      xhr.send(fd);
    });
  }

  /* 附件入口：支持多选。私聊每个文件一条独立上传气泡+独立进度（互不阻塞）；群聊一次请求合并为一条消息（多文件→相册），一条总进度 */
  async function uploadFiles(files) {
    if (!S.current || !files.length) return;
    if (S.current.type === 'private') {
      const targetId = S.current.id, key = convKey('private', targetId);
      files.forEach((f) => {
        const kind = fileKind(f);
        startOptimisticUpload({
          chatType: 'private', targetId, key,
          items: [{ kind: kind, src: URL.createObjectURL(f), name: f.name, size: f.size }],
          send: makeUploader('private', targetId, [f]),
          preview: () => (kind === 'image' ? '[图片]' : '[文件] ' + f.name),
        });
      });
      return;
    }
    if (S.current.type === 'group') {
      const targetId = S.current.id, key = convKey('group', targetId);
      const items = files.map((f) => ({ kind: fileKind(f), src: URL.createObjectURL(f), name: f.name, size: f.size }));
      startOptimisticUpload({
        chatType: 'group', targetId, key, items,
        send: makeUploader('group', targetId, files),
        preview: (real) => (files.length > 1 ? '[相册]' : (real.msg_type === 2 ? '[图片]' : '[文件]')),
      });
    }
  }

  /* textarea 自动增高 */
  function autoresize(ta) { ta.style.height = 'auto'; ta.style.height = Math.min(ta.scrollHeight, 140) + 'px'; }

  /* ---------------- @成员选择器（仅群聊） ---------------- */
  let mentionPop = null, mentionIndex = 0, mentionList = [];
  function ensureMentionPop() {
    if (mentionPop) return mentionPop;
    mentionPop = document.createElement('div');
    mentionPop.className = 'mention-pop';
    $('.input-wrap').appendChild(mentionPop);
    return mentionPop;
  }
  function hideMentionPop() { if (mentionPop) mentionPop.classList.remove('show'); }
  function mentionQuery() {
    const input = $('#msgInput');
    const val = input.value.slice(0, input.selectionStart == null ? input.value.length : input.selectionStart);
    const m = val.match(/@([^\s@]*)$/);
    return m ? { token: m[0], q: m[1] } : null;
  }
  function updateMentionPop() {
    if (!S.current || S.current.type !== 'group') { hideMentionPop(); return; }
    const mq = mentionQuery();
    if (!mq) { hideMentionPop(); return; }
    const members = (S.groupMembers || []).filter(x => x.user_id !== S.me.id);
    const q = mq.q.toLowerCase();
    mentionList = members.filter(x => !q || (x.nickname || '').toLowerCase().includes(q)).slice(0, 12);
    if (!mentionList.length) { hideMentionPop(); return; }
    mentionIndex = 0;
    const pop = ensureMentionPop();
    pop.innerHTML = mentionList.map((x, i) =>
      '<div class="mention-item' + (i === 0 ? ' sel' : '') + '" data-i="' + i + '"><img src="' + esc(x.avatar_url || letterAvatar(x.nickname)) + '"><span class="mi-name">' + esc(dispName(x.user_id, x.nickname)) + '</span></div>').join('');
    pop.classList.add('show');
    $$('.mention-item', pop).forEach(el => el.addEventListener('mousedown', (e) => { e.preventDefault(); pickMention(+el.dataset.i); }));
  }
  function moveMention(delta) {
    if (!mentionPop || !mentionPop.classList.contains('show') || !mentionList.length) return;
    mentionIndex = (mentionIndex + delta + mentionList.length) % mentionList.length;
    $$('.mention-item', mentionPop).forEach((el, i) => el.classList.toggle('sel', i === mentionIndex));
  }
  function pickMention(i) {
    const x = mentionList[i]; if (!x) return;
    const input = $('#msgInput');
    const mq = mentionQuery(); if (!mq) return;
    const caret = input.selectionStart == null ? input.value.length : input.selectionStart;
    const start = caret - mq.token.length;
    const label = (x.nickname || ('用户' + x.user_id)).replace(/\s+/g, '');
    input.value = input.value.slice(0, start) + '@' + label + ' ' + input.value.slice(caret);
    const np = start + label.length + 2;
    try { input.setSelectionRange(np, np); } catch (e) {}
    S.pendingMentions = S.pendingMentions || [];
    if (S.pendingMentions.indexOf(x.user_id) < 0) S.pendingMentions.push(x.user_id);
    hideMentionPop();
    autoresize(input); input.focus();
  }

  /* ---------------- Socket 连接与实时事件 ---------------- */
  function initSocket() {
    const sock = io({ transports: ['websocket', 'polling'] });
    S.sock = sock;

    sock.on('connect_error', () => { /* 自动重连 */ });

    // 私聊消息：{from_user, msg} 文本 或 {from_user, msg_type, msg_id, content, filename, file_size} 文件
    sock.on('message', (data) => {
      const from = data.from_user;
      if (isBlocked('user', from)) return;  // 已拉黑：忽略推送，不重新顶回会话列表
      const isFile = data.msg_type && data.msg_type !== 1;
      let preview;
      if (data.msg_type === 5) { let c = {}; try { c = JSON.parse(data.content) || {}; } catch (e) {} preview = '[转发] ' + (c.title || c.summary || c.channel_name || ''); }
      else preview = isFile ? (data.msg_type === 2 ? '[图片]' : '[文件] ' + (data.filename || '文件')) : data.msg;
      const isActive = S.current && S.current.type === 'private' && S.current.id === from;
      const pmuted = (() => { const c = S.convs.get(convKey('private', from)); return c && c.notify_level === 3; })();  // 静音私聊：任何情况下都不响来音
      if (isActive) {
        appendMessage({ id: data.msg_id || ('tmp' + Date.now()), self: false, content: isFile ? data.content : data.msg, msg_type: data.msg_type || 1, created_at: new Date().toISOString(), filename: data.filename, file_size: data.file_size, avatar: S.current && S.current.avatar, reply_to: data.reply_to, reply_to_nickname: data.reply_to_nickname, reply_to_content: data.reply_to_content }, 'private', { pop: true, forceBottom: true });
        if (window.SFX && !pmuted) SFX.msgIn();
        setChatSubPresence(true, '在线');  // 收到对方实时消息 = 当前在线
        bumpConversation('private', from, preview);
      } else {
        if (window.SFX && !pmuted) SFX.msgIn();
        bumpConversation('private', from, preview);
      }
    });

    // 群消息：{id, sender_id, sender_name, group_id, content, msg_type, created_at}
    sock.on('receive_message', (data) => {
      const gid = data.group_id;
      if (isBlocked('group', gid)) return;  // 已拉黑群聊：忽略
      // 自己发的图/文件/相册：本地乐观气泡正在呈现（在途上传计数>0），抑制自回声避免重复气泡
      if (data.sender_id === S.me.id && (data.msg_type === 2 || data.msg_type === 3 || data.msg_type === 4) && S.pendingUploads[convKey('group', gid)] > 0) return;
      const isActive = S.current && S.current.type === 'group' && S.current.id === gid;
      if (isActive) {
        appendMessage({ id: data.id, self: data.sender_id === S.me.id, content: data.content, msg_type: data.msg_type || 1, created_at: data.created_at, sender_name: data.sender_name, filename: data.filename, file_size: data.file_size, mentions: data.mentions, avatar: data.sender_avatar, reply_to: data.reply_to, reply_to_nickname: data.reply_to_nickname, reply_to_content: data.reply_to_content }, 'group', { pop: true, forceBottom: true });
        const gmuted = (() => { const c = S.convs.get(convKey('group', gid)); return c && c.notify_level === 3; })();  // 静音群聊：任何情况下都不响来音
        if (data.sender_id !== S.me.id && !gmuted && window.SFX) SFX.msgIn();
      }
      bumpConversation('group', gid, (data.sender_name ? data.sender_name + ': ' : '') + (data.msg_type === 2 ? '[图片]' : data.msg_type === 3 ? '[文件]' : data.msg_type === 5 ? (() => { let c = {}; try { c = JSON.parse(data.content) || {}; } catch (e) {} return '[转发] ' + (c.title || c.summary || c.channel_name || ''); })() : data.content));
    });

    // 机器人系统消息（欢迎/警告/回复等，不入库，仅实时展示为居中提示）
    sock.on('bot_message', (data) => {
      if (!data || !data.content) return;
      if (data.group_id && S.current && S.current.type === 'group' && S.current.id === data.group_id && !isBlocked('group', data.group_id)) {
        const el = document.createElement('div');
        el.className = 'sys-tip';
        el.textContent = '🤖 ' + (data.bot_name || '机器人') + '：' + data.content;
        const near = isNearBottom();
        msgListEl.appendChild(el);
        if (near) scrollBottom(true);
      }
    });

    // 频道帖子：{id, author_id, author_nickname, channel_id, content, created_at}
    sock.on('receive_post', (data) => {
      const cid = data.channel_id;
      if (isBlocked('channel', cid)) return;  // 已拉黑频道：不再接收更新
      const isActive = S.current && S.current.type === 'channel' && S.current.id === cid;
      const chConv = S.convs.get(convKey('channel', cid));
      const muted = chConv && chConv.notify_level === 3;  // 静音频道：任何情况下都不响来音
      // 仅帖子流视图追加卡片；若正处于某帖评论详情，不污染当前内容区
      if (isActive && S.channelView !== 'detail') {
        const hint = $('.loading-hint', msgListEl); if (hint) hint.remove();
        const pid = data.post_id || data.id;
        // 去重：发帖后本地会 reload 一次，广播可能再推一条，避免重复卡片
        if (pid && msgListEl.querySelector('.post-card[data-id="' + pid + '"]')) { /* 已存在 */ }
        else {
          const near = isNearBottom();
          msgListEl.appendChild(buildPostCard(data));
          if (near) scrollBottom(true);
          if (data.author_id !== S.me.id && !muted && window.SFX) SFX.msgIn();
        }
      }
      bumpConversation('channel', cid, data.content);
    });

    // 帖子评论（评论详情视图打开时）
    sock.on('receive_comment', (data) => {
      if (S.openPostRoom && S.openPostRoom.post_id === data.post_id) {
        const box = ensureCommentsContainer();
        if (box) {
          box.insertAdjacentHTML('beforeend', commentItemHtml({ comment_id: data.id, user_id: data.user_id, user_nickname: data.user_nickname, user_avatar: data.user_avatar, content: data.content, reply_to: data.reply_to, reply_to_nickname: data.reply_to_nickname, reply_to_content: data.reply_to_content, created_at: data.created_at }));
          const cnt = $('#pdCommentCount'); if (cnt) cnt.textContent = ((+cnt.textContent) || 0) + 1;
          scrollBottom(true);
          if (data.user_id !== S.me.id && window.SFX) SFX.msgIn();
        }
      }
    });

    // 帖子附件后台上传完成：若正停留在该频道帖子流，刷新让新附件补到卡片上
    sock.on('post_file_added', (data) => {
      if (!data || !data.channel_id) return;
      if (S.current && S.current.type === 'channel' && S.current.id === data.channel_id && S.channelView !== 'detail') {
        loadChannel(data.channel_id);
      }
    });

    // 消息被撤回/删除：界面同步移除对应气泡（后端硬删后广播，群房间/私聊双方都会收到）
    sock.on('message_revoked', (data) => {
      if (!data || !data.message_id) return;
      const b = msgListEl.querySelector('.bubble[data-id="' + data.message_id + '"]');
      if (b) dissolveRow(b.closest('.msg-row') || b);
      reloadConversations();  // 对方撤回/双方删除后，同步刷新会话列表摘要
    });

    sock.on('error', (data) => { if (data && data.msg) toast(data.msg); });

    // 统一站内通知（好友申请/邀请/被@/点赞/评论）：toast + 徒章 + 按需刷新
    sock.on('notification', (data) => {
      if (!data) return;
      toast((data.title ? data.title + '：' : '') + (data.content || ''));
      if (data.type === 'friend' || data.type === 'group_invite' || data.type === 'group_owner_transfer' || data.type === 'group_join_request') {
        refreshReqBadge();
        if (S.tab === 'contacts') renderContacts();
      }
      // 入群申请通过：申请人需刷新会话列表让新群出现
      if (data.type === 'group_join_request' && data.group_id) { reloadConversations(); }
      refreshNotifBadge();
    });

    // 全员禁言开关变更：当前群同步提示并刷新群信息（操作者本人已由 HTTP 响应提示过，广播不再重复弹）
    sock.on('group_mute_all', (data) => {
      if (S.current && data && S.current.type === 'group' && S.current.id === data.group_id) {
        if (!data.operator_id || data.operator_id !== S.me.id) toast(data.mute_all ? '群已开启全员禁言' : '群已关闭全员禁言');
        if (panelEl.classList.contains('info-open')) showGroupInfo(groupInfoViewMode);
      }
    });

    // 管理员发布全站公告：实时放出送信小狗（与 pending 去重，不重复计数）
    sock.on('site_announcement', (data) => {
      if (!data || !data.id) return;
      if (!annState.list.some(x => x.id === data.id)) annState.list.unshift(data);
      annRender(true);
      toast('管理员发布了一条新公告，点左下角的小吉娃娃看看');
    });

    // 管理员回复了我的想法：清抽屉角标提示
    sock.on('feedback_replied', () => { refreshFbBadge(); });
  }

  /* ---------------- 通用弹层 ---------------- */
  const modalMask = $('#modalMask');
  let modalOkHandler = null;
  let modalCleanup = null;

  function openModal(title, bodyHtml, onOk, okText, danger) {
    if (modalCleanup) { try { modalCleanup(); } catch (e) {} modalCleanup = null; }
    // 标题传空则整个隐藏，不留空白占位（如公告弹窗：正文自带标题）
    const titleEl = $('#modalTitle');
    titleEl.textContent = title || '';
    titleEl.hidden = !title;
    $('#modalBody').innerHTML = bodyHtml;
    $('#modalOk').textContent = okText || '确定';
    $('#modalOk').style.display = onOk ? '' : 'none';
    $('#modalOk').classList.toggle('danger', !!danger);
    // 统一交互：所有弹层的"取消"改为右上角叉号关闭；无确认按钮时隐藏整条底部
    const modalEl = $('#modal');
    modalEl.classList.remove('wide');   // 宽体仅限 PDF 预览等场景，用完复位
    const xBtn = $('#modalCloseX'); if (xBtn) xBtn.hidden = false;
    modalEl.classList.toggle('no-foot', !onOk);
    modalOkHandler = onOk || null;
    modalMask.hidden = false;
    requestAnimationFrame(() => modalMask.classList.add('show'));
  }
  function closeModal() {
    modalMask.classList.remove('show');
    setTimeout(() => { modalMask.hidden = true; $('#modalBody').innerHTML = ''; }, 260);
    try { closePeek(); } catch (e) {}
    if (modalCleanup) { try { modalCleanup(); } catch (e) {} modalCleanup = null; }
    if (S.openPostRoom) { S.sock && S.sock.emit('leave_channel_post', S.openPostRoom); S.openPostRoom = null; }
  }
  $('#modalCloseX').addEventListener('click', closeModal);
  // 故意为之：表单类弹层点遮罩不关闭，防止误触丢失已填内容，只能用右上角叉号或 Esc 关闭
  $('#modalOk').addEventListener('click', async () => {
    const btn = $('#modalOk');
    btn.disabled = true;
    try { if (modalOkHandler) await modalOkHandler(); } finally { btn.disabled = false; }
  });

  /* ---------------- 抽屉菜单 ---------------- */
  const drawer = $('#drawer'), drawerMask = $('#drawerMask');
  function showDrawer() { drawerMask.hidden = false; requestAnimationFrame(() => { drawer.classList.add('show'); drawerMask.classList.add('show'); }); }
  function hideDrawer() { drawer.classList.remove('show'); drawerMask.classList.remove('show'); setTimeout(() => drawerMask.hidden = true, 300); }
  /* 汉堡按钮：单击开抽屉；长按（touch/鼠标左键 500ms，与“动态”tab 长按同款实现）切换深浅色主题，
     触发后用 suppress 标记吞掉随之而来的 click，避免误开抽屉 */
  const menuBtn = $('#menuBtn');
  let menuLpTimer = null;
  menuBtn.addEventListener('pointerdown', (e) => {
    if (e.pointerType === 'mouse' && e.button !== 0) return;
    clearTimeout(menuLpTimer);
    menuLpTimer = setTimeout(() => {
      menuLpTimer = null;
      menuBtn.dataset.suppress = '1';
      if (navigator.vibrate) { try { navigator.vibrate(8); } catch (_) {} }
      toggleThemeFromBtn(menuBtn);
    }, 500);
  });
  ['pointerup', 'pointerleave', 'pointercancel'].forEach(ev => menuBtn.addEventListener(ev, () => clearTimeout(menuLpTimer)));
  menuBtn.addEventListener('click', () => {
    if (menuBtn.dataset.suppress) { menuBtn.dataset.suppress = ''; return; }
    showDrawer();
  });
  drawerMask.addEventListener('click', hideDrawer);

  $$('.drawer-item').forEach(btn => btn.addEventListener('click', () => {
    const act = btn.dataset.act;
    hideDrawer();
    setTimeout(() => actions[act] && actions[act](), 220);
  }));

  // 点击外部：收起动态的互动条；评论列表常驻，只收起展开中的输入框（无评论时整区收起）
  document.addEventListener('click', (e) => {
    // 群聊/评论区气泡上的昵称 → 打开该用户的资料卡
    const pu = e.target.closest('.sender[data-pu]');
    if (pu) { openUserProfile(+pu.dataset.pu, pu); return; }
    if (!e.target.closest('.moment-foot')) closeAllMf();
    if (!e.target.closest('.moment-comments') && !e.target.closest('.moment-foot')) {
      $$('.mc-input:not([hidden])').forEach(wrap => {
        wrap.hidden = true;
        exitMcReply(wrap);
        const box = wrap.closest('.moment-comments');
        const list = $('[data-mclist]', box);
        if (!list || !list.children.length) box.hidden = true;
      });
    }
  });

  // 动态评论：右击弹菜单（别人的→回复；自己的→删除）；点横幅 ✕ 取消回复（列表会重渲染，故用全局委托）
  document.addEventListener('click', (e) => {
    const cx = e.target.closest('.mcr-x');
    if (cx) { const wrap = cx.closest('.mc-input'); if (wrap) exitMcReply(wrap); }
  });
  document.addEventListener('contextmenu', (e) => {
    const item = e.target.closest('.mc-item');
    if (!item) return;
    const self = item.dataset.self === '1';
    const cid = item.dataset.mcc, nick = item.dataset.mcnick;
    const menu = [];
    if (!self) menu.push({ label: '回复', fn: () => {
      const card = item.closest('.moment-card'); const box = card && $('.moment-comments', card);
      if (box) { box.hidden = false; const wrap = $('.mc-input', box); if (wrap) { wrap.hidden = false; enterMcReply(wrap, cid, nick); const ip = $('input', wrap); if (ip) ip.focus(); } }
    }});
    if (self) menu.push({ label: '删除', danger: true, fn: () => deleteMcComment(cid, item) });
    if (!menu.length) return;
    e.preventDefault();
    openCtxMenu(e.clientX, e.clientY, menu);
  });

  /* ---------------- 侧栏 Tabbar（聊天/通讯录/动态/资料；设置已移至汉堡抽屉入口） ---------------- */
  function switchTab(name) {
    S.tab = name;
    $$('#tabbar .tab').forEach(b => b.classList.toggle('active', b.dataset.tab === name));
    convListEl.hidden = name !== 'chats';
    $('#panelContacts').hidden = name !== 'contacts';
    $('#panelMoments').hidden = name !== 'moments';
    $('#panelProfile').hidden = name !== 'profile';
    $('.sidebar').classList.toggle('no-head', name !== 'chats');   // 顶部汉堡+搜索栏仅在会话 tab 显示
    if (ctIndexEl) ctIndexEl.classList.toggle('ct-index-show', name === 'contacts');  // A-Z 索引仅通讯录显示
    closeContactsSub();  // 切换 tab 时关闭子视图
    if (name === 'contacts') renderContacts();
    if (name === 'moments') renderMoments();
    if (name === 'profile') renderProfile();
    annSyncVisible();  // 送信小狗仅在会话列表 tab 露脸
  }
  // tab 切换；"动态"tab 额外支持 双击 / 长按 打开发布动态弹窗（发布按钮已从此页归位于此）
  $$('#tabbar .tab').forEach(b => {
    let lastTap = 0, lpTimer = null;
    const pub = () => { if (S.tab !== 'moments') switchTab('moments'); openMomentPublish(); };
    b.addEventListener('click', () => {
      if (b.dataset.suppress) { b.dataset.suppress = ''; return; } // 长按已触发，吞掉随后的 click
      const now = Date.now();
      if (b.dataset.tab === 'moments' && now - lastTap < 350) { lastTap = 0; pub(); return; }
      lastTap = now;
      switchTab(b.dataset.tab);
    });
    b.addEventListener('pointerdown', () => {
      if (b.dataset.tab !== 'moments') return;
      lpTimer = setTimeout(() => { b.dataset.suppress = '1'; pub(); }, 600);
    });
    ['pointerup', 'pointerleave', 'pointercancel'].forEach(ev => b.addEventListener(ev, () => clearTimeout(lpTimer)));
  });

  function contactRowHtml(type, id, name, avatar, sub) {
    return '<div class="list-row" data-t="' + type + '" data-id="' + id + '" data-name="' + esc(name) + '" data-avatar="' + esc(avatar || '') + '" style="cursor:pointer;border-radius:12px;margin-bottom:2px">' +
      '<img src="' + esc(avatar || ('https://robohash.org/' + type + id)) + '" alt="">' +
      '<div class="lr-main"><div class="lr-t">' + esc(name) + '</div><div class="lr-s">' + esc(sub) + '</div></div></div>';
  }
  function friendRowHtml(f) {
    return '<div class="list-row fr-friend" data-t="private" data-id="' + f.user_id + '" data-name="' + esc(f.name) + '" data-remark="' + esc(f.remark || '') + '" data-nick="' + esc(f.nickname || f.name) + '" data-avatar="' + esc(f.avatar || '') + '" style="cursor:pointer;border-radius:12px;margin-bottom:2px">' +
      '<img src="' + esc(f.avatar || ('https://robohash.org/u' + f.user_id)) + '" alt="">' +
      '<div class="lr-main"><div class="lr-t">' + esc(f.name) + '</div></div>' +
      '</div>';
  }

  /* ===== 通讯录：微信风格——群聊/频道卡片入口 + 好友 A-Z 直接展示 ===== */
  function getFirstLetter(name) {
    const s = (name || '').trim();
    if (!s) return '#';
    const ch = s.charAt(0);
    if (/[a-zA-Z]/.test(ch)) return ch.toUpperCase();
    if (!/[\u4e00-\u9fa5]/.test(ch)) return '#';
    let coll; try { coll = new Intl.Collator('zh-Hans-CN'); } catch (e) { return '#'; }
    const marks = [['A','阿'],['B','芭'],['C','擦'],['D','搭'],['E','蛾'],['F','发'],['G','噶'],['H','哈'],['J','击'],['K','喀'],['L','垃'],['M','妈'],['N','拿'],['O','哦'],['P','啪'],['Q','期'],['R','然'],['S','撒'],['T','塌'],['W','挖'],['X','昔'],['Y','压'],['Z','匝']];
    for (let i = 0; i < marks.length; i++) {
      if (coll.compare(ch, marks[i][1]) < 0) return i === 0 ? '#' : marks[i - 1][0];
    }
    return 'Z';
  }
  function ctCardHtml(key, title, count) {
    return '<div class="ct-card" data-sub="' + key + '">' +
      '<span class="ct-card-title">' + esc(title) + '</span>' +
      '<span class="ct-card-count">' + count + '</span>' +
      '<span class="ct-card-arrow">\u203A</span></div>';
  }
  function contactsSectionsHtml(d) {
    let coll; try { coll = new Intl.Collator('zh-Hans-CN'); } catch (e) { coll = null; }
    const cmp = (a, b) => coll ? coll.compare(a.name, b.name) : String(a.name).localeCompare(String(b.name));
    const friends = (d.friends || []).slice().sort(cmp);
    const buckets = new Map();
    friends.forEach(f => { const L = getFirstLetter(f.name); if (!buckets.has(L)) buckets.set(L, []); buckets.get(L).push(f); });
    const letterKeys = [...buckets.keys()].sort((a, b) => a === b ? 0 : (a === '#' ? 1 : b === '#' ? -1 : (a < b ? -1 : 1)));
    // 卡片入口：群聊 + 频道
    let html = '<div class="ct-cards">';
    html += ctCardHtml('groups', '群聊', (d.groups || []).length);
    html += ctCardHtml('channels', '频道', (d.channels || []).length);
    html += '</div>';
    // 好友列表（A-Z 直接展示，无外层折叠）
    html += '<div class="ct-friends" id="ctFriends">';
    if (!friends.length) html += '<div class="empty-hint">暂无好友</div>';
    else letterKeys.forEach(L => { html += '<div class="ct-letter">' + esc(L) + '</div>' + buckets.get(L).map(f => friendRowHtml(f)).join(''); });
    html += '</div>';
    return html;
  }
  /* 子页面：点击卡片后滑入显示群聊/频道详情，带返回按钮 */
  function openContactsSub(title, itemsHtml) {
    const sidebar = $('.sidebar');
    // 确保主视图包裹元素存在
    const panel = $('#panelContacts');
    let main = panel.querySelector('.ct-wrap');
    if (!main) { main = document.createElement('div'); main.className = 'ct-wrap'; while (panel.firstChild) main.appendChild(panel.firstChild); panel.appendChild(main); }
    const sub = document.createElement('div');
    sub.className = 'ct-sub';
    sub.innerHTML = '<div class="ct-sub-bar"><button class="ct-sub-back" type="button"><span class="chev"></span></button><span class="ct-sub-title">' + esc(title) + '</span></div>' +
      '<div class="ct-sub-body">' + itemsHtml + '</div>';
    sidebar.appendChild(sub);
    requestAnimationFrame(() => sub.classList.add('ct-sub-show'));
    // 隐藏 A-Z 索引
    if (ctIndexEl) ctIndexEl.classList.remove('ct-index-show');
    // 返回按钮
    $('.ct-sub-back', sub).addEventListener('click', () => closeContactsSub());
    // 为子视图内的列表行绑定点击打开会话
    $$('.ct-sub-body .list-row', sub).forEach(row => {
      const img = $('img', row); if (img) watchAvatar(img, row.dataset.name);
      row.addEventListener('click', async () => {
        const t = row.dataset.t, id = +row.dataset.id;
        closeModal();
        await reloadConversations();
        const stored = S.convs.get(convKey(t, id));
        openConversation(stored || { conv_type: t, target_id: id, name: row.dataset.name, avatar: row.dataset.avatar || '', updated_at: new Date().toISOString() });
      });
    });
  }
  function closeContactsSub() {
    const sub = $('.sidebar .ct-sub');
    if (!sub) return;
    // 左进右出：入场由 -100% 滑到 0；退场切到 +100% 向右移出（与入场不同方向，故用独立 out 类）
    sub.classList.remove('ct-sub-show');
    sub.classList.add('ct-sub-out');
    setTimeout(() => { sub.remove(); }, 320);
    // 恢复 A-Z 索引
    if (ctIndexEl && S.tab === 'contacts') ctIndexEl.classList.add('ct-index-show');
  }

  /* 右侧 A-Z 索引条：点击平滑滚动到对应字母分区，支持拖拽连续滑动 */
  let ctIndexEl = null;
  function buildContactsIndex(panel) {
    // 移除旧索引
    if (ctIndexEl) { ctIndexEl.remove(); ctIndexEl = null; }
    const letters = panel.querySelectorAll('.ct-letter');
    if (!letters.length) return;
    const present = new Set();
    letters.forEach(el => present.add(el.textContent.trim()));
    const ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'.split('');
    const keys = [...ALPHABET, '#'];
    const bar = document.createElement('div');
    bar.className = 'ct-index';
    bar.innerHTML = keys.map(ch => '<span class="ct-idx-item' + (present.has(ch) ? '' : ' ct-idx-dim') + '" data-letter="' + ch + '">' + ch + '</span>').join('');
    $('.sidebar').appendChild(bar);
    ctIndexEl = bar;
    // 当前已在 contacts tab 则立即显示
    if (S.tab === 'contacts') bar.classList.add('ct-index-show');

    // 滚动到对应字母分区
    const scrollTo = (letter) => {
      const target = panel.querySelector('.ct-letter');
      if (!target) return;
      // 找到对应字母元素
      let el = null;
      panel.querySelectorAll('.ct-letter').forEach(l => { if (l.textContent.trim() === letter) el = l; });
      if (!el) return;
      // 确保好友分组展开
      const sec = el.closest('.ct-sec');
      if (sec && sec.classList.contains('collapsed')) sec.classList.remove('collapsed');
      el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    };

    // 点击事件
    bar.addEventListener('pointerdown', (e) => {
      const item = e.target.closest('.ct-idx-item');
      if (!item) return;
      e.preventDefault();
      const letter = item.dataset.letter;
      scrollTo(letter);
      // 拖拽连续滚动
      const onMove = (ev) => {
        const y = ev.clientY;
        const rect = bar.getBoundingClientRect();
        const idx = Math.max(0, Math.min(keys.length - 1, Math.floor((y - rect.top) / (rect.height / keys.length))));
        scrollTo(keys[idx]);
        // 视觉反馈：高亮当前字母
        bar.querySelectorAll('.ct-idx-item').forEach((s, i) => s.classList.toggle('ct-idx-active', i === idx));
      };
      const onUp = () => {
        document.removeEventListener('pointermove', onMove);
        document.removeEventListener('pointerup', onUp);
        bar.querySelectorAll('.ct-idx-item').forEach(s => s.classList.remove('ct-idx-active'));
      };
      document.addEventListener('pointermove', onMove);
      document.addEventListener('pointerup', onUp);
    });
  }

  function setTabBadge(tab, n) {
    const btn = $('#tabbar .tab[data-tab="' + tab + '"]'); if (!btn) return;
    let b = $('.tab-badge', btn);
    if (n > 0) { if (!b) { b = document.createElement('span'); b.className = 'tab-badge'; btn.appendChild(b); } b.textContent = n > 99 ? '99+' : n; }
    else if (b) b.remove();
  }
  // 聊天 tab 未读红点：任一会话有未读即在“聊天”图标上显示一个小红点（不显数字）
  function updateChatTabDot() {
    const btn = $('#tabbar .tab[data-tab="chats"]'); if (!btn) return;
    let has = false;
    S.convs.forEach(c => { if ((c.unread_count || 0) > 0) has = true; });
    let dot = $('.tab-dot', btn);
    if (has) { if (!dot) { dot = document.createElement('span'); dot.className = 'tab-dot'; btn.appendChild(dot); } }
    else if (dot) dot.remove();
  }
  // “资料”tab：用当前用户头像替代小人图标（无头像时字母头像兑底）
  function paintProfileTab() {
    const ic = $('#tabbar .tab[data-tab="profile"] .tab-ic'); if (!ic) return;
    const src = (S.me && S.me.avatar) || letterAvatar((S.me && S.me.nickname) || '我');
    ic.innerHTML = '<img class="tab-avatar" src="' + esc(src) + '" alt="">';
  }
  async function refreshReqBadge() {
    let n = 0;
    try { const r = await api('/friend/requests'); if (r.code === 0) n += (r.data.received || []).length; } catch (e) {}
    try { const r = await api('/group/invites'); if (r.code === 0) n += (r.data.invites || []).length; } catch (e) {}
    try { const r = await api('/channel/invites'); if (r.code === 0) n += (r.data.invites || []).length; } catch (e) {}
    try { const r = await api('/group/owner/transfers'); if (r.code === 0) n += (r.data.transfers || []).length; } catch (e) {}
    try { const r = await api('/group/join/requests'); if (r.code === 0) n += (r.data.requests || []).length; } catch (e) {}
    setTabBadge('contacts', n);
  }
  // 系统通知未读红点：抽屉菜单项角标 + 汉堡按钮小红点
  async function refreshNotifBadge() {
    try {
      const r = await api('/notification/list?limit=1');
      const n = (r && r.code === 0) ? (r.data.unread || 0) : 0;
      const b = $('#drawerNotifBadge');
      if (b) { b.hidden = n <= 0; b.textContent = n > 99 ? '99+' : n; }
      const dot = $('#menuNotifDot');
      if (dot) dot.hidden = n <= 0;
    } catch (e) {}
  }
  async function renderContacts() {
    closeContactsSub();
    const panel = $('#panelContacts');
    panel.innerHTML = '<div class="loading-hint">加载中…</div>';
    const [r, fr, gi, ci, otf, jr] = await Promise.all([
      api('/chat/contacts').catch(() => null),
      api('/friend/requests').catch(() => null),
      api('/group/invites').catch(() => null),
      api('/channel/invites').catch(() => null),
      api('/group/owner/transfers').catch(() => null),
      api('/group/join/requests').catch(() => null),
    ]);
    if (!r || r.code !== 0) { panel.innerHTML = '<div class="empty-hint">' + esc((r && r.msg) || '加载失败') + '</div>'; return; }
    const d = r.data;
    let html = '';
    const recv = (fr && fr.code === 0) ? (fr.data.received || []) : [];
    if (recv.length) {
      html += '<div class="sec-title">好友申请 · ' + recv.length + '</div>';
      html += recv.map(x => '<div class="req-row"><img src="' + esc(x.sender_avatar || letterAvatar(x.sender_nickname)) + '"><div class="lr-main"><div class="lr-t">' + esc(x.sender_nickname) + '</div><div class="lr-s">' + esc(x.message || '请求添加你为好友') + '</div></div><div class="req-actions"><button class="mini-btn blue" data-fa="' + x.request_id + '">同意</button><button class="mini-btn" data-fr="' + x.request_id + '">拒绝</button></div></div>').join('');
    }
    const invs = (gi && gi.code === 0) ? (gi.data.invites || []) : [];
    if (invs.length) {
      html += '<div class="sec-title">群邀请 · ' + invs.length + '</div>';
      html += invs.map(x => '<div class="req-row"><img src="' + esc(x.group_avatar || letterAvatar(x.group_name)) + '"><div class="lr-main"><div class="lr-t">' + esc(x.group_name) + '</div><div class="lr-s">' + esc(x.inviter_nickname) + ' 邀请你加入</div></div><div class="req-actions"><button class="mini-btn blue" data-ga="' + x.invite_id + '">接受</button><button class="mini-btn" data-gr="' + x.invite_id + '">拒绝</button></div></div>').join('');
    }
    // 群主转让待确认：同意后才会变更角色，与群邀请同款横幅交互
    const otfList = (otf && otf.code === 0) ? (otf.data.transfers || []) : [];
    if (otfList.length) {
      html += '<div class="sec-title">群主转让 · ' + otfList.length + '</div>';
      html += otfList.map(x => '<div class="req-row"><img src="' + esc(x.group_avatar || letterAvatar(x.group_name)) + '"><div class="lr-main"><div class="lr-t">' + esc(x.group_name) + '</div><div class="lr-s">' + esc(x.from_nickname) + ' 请求转让群主给你</div></div><div class="req-actions"><button class="mini-btn blue" data-ota="' + x.transfer_id + '">接受</button><button class="mini-btn" data-otr="' + x.transfer_id + '">拒绝</button></div></div>').join('');
    }
    // 入群申请：仅群主/管理员会收到其管理群的待审申请
    const jreqs = (jr && jr.code === 0) ? (jr.data.requests || []) : [];
    if (jreqs.length) {
      html += '<div class="sec-title">入群申请 · ' + jreqs.length + '</div>';
      html += jreqs.map(x => '<div class="req-row"><img src="' + esc(x.user_avatar || letterAvatar(x.user_nickname)) + '"><div class="lr-main"><div class="lr-t">' + esc(x.user_nickname) + '</div><div class="lr-s">申请加入「' + esc(x.group_name) + '」' + (x.message ? ' · ' + esc(x.message) : '') + '</div></div><div class="req-actions"><button class="mini-btn blue" data-jra="' + x.request_id + '">同意</button><button class="mini-btn" data-jrr="' + x.request_id + '">拒绝</button></div></div>').join('');
    }
    const cinvs = (ci && ci.code === 0) ? (ci.data.invites || []) : [];
    if (cinvs.length) {
      html += '<div class="sec-title">频道邀请 · ' + cinvs.length + '</div>';
      html += cinvs.map(x => '<div class="req-row"><img src="' + esc(x.channel_avatar || letterAvatar(x.channel_name)) + '"><div class="lr-main"><div class="lr-t">' + esc(x.channel_name) + '</div><div class="lr-s">' + esc(x.inviter_nickname) + ' 邀请你订阅</div></div><div class="req-actions"><button class="mini-btn blue" data-ca="' + x.invite_id + '">接受</button><button class="mini-btn" data-cr="' + x.invite_id + '">拒绝</button></div></div>').join('');
    }
    // 卡片入口 + 好友 A-Z 列表
    html += contactsSectionsHtml(d);
    panel.innerHTML = html;
    // 右侧 A-Z 索引条
    buildContactsIndex(panel);
    // 卡片点击→打开子视图
    $$('.ct-card', panel).forEach(card => card.addEventListener('click', () => {
      const key = card.dataset.sub;
      if (key === 'groups') {
        const inner = (d.groups || []).length ? d.groups.map(g => contactRowHtml('group', g.group_id, g.name, g.avatar, g.number)).join('') : '<div class="empty-hint">还没加入任何群，去搜索群编号加入吧</div>';
        openContactsSub('群聊', inner);
      } else if (key === 'channels') {
        const inner = (d.channels || []).length ? d.channels.map(c => contactRowHtml('channel', c.channel_id, c.name, c.avatar, c.number)).join('') : '<div class="empty-hint">还没订阅任何频道</div>';
        openContactsSub('频道', inner);
      }
    }));
    $$('.list-row', panel).forEach(row => {
      watchAvatar($('img', row), row.dataset.name);
      row.addEventListener('click', async (e) => {
        if (e.target.closest('[data-delfriend]')) return;
        const t = row.dataset.t, id = +row.dataset.id;
        closeModal();
        await reloadConversations();
        const stored = S.convs.get(convKey(t, id));
        openConversation(stored || { conv_type: t, target_id: id, name: row.dataset.name, avatar: row.dataset.avatar || '', updated_at: new Date().toISOString() });
      });
    });
    // 好友行右键：设置/修改备注（仅自己可见）
    $$('.fr-friend', panel).forEach(row => row.addEventListener('contextmenu', (e) => {
      e.preventDefault(); e.stopPropagation();
      const uid = +row.dataset.id, cur = row.dataset.remark || '';
      openCtxMenu(e.clientX, e.clientY, [
        { label: cur ? '修改备注' : '设置备注', fn: () => openRemarkEditor(uid, cur) },
      ]);
    }));
    $$('[data-fa]', panel).forEach(b => b.addEventListener('click', async () => {
      const r = await api('/friend/accept', { request_id: +b.dataset.fa });
      if (r.code === 0) { toast('已添加为好友'); renderContacts(); refreshReqBadge(); reloadConversations(); } else toast(r.msg || '操作失败');
    }));
    $$('[data-fr]', panel).forEach(b => b.addEventListener('click', async () => {
      const r = await api('/friend/reject', { request_id: +b.dataset.fr });
      if (r.code === 0) { renderContacts(); refreshReqBadge(); } else toast(r.msg || '操作失败');
    }));
    $$('[data-ga]', panel).forEach(b => b.addEventListener('click', async () => {
      const r = await api('/group/invite/accept', { invite_id: +b.dataset.ga });
      if (r.code === 0) { toast('已加入群聊'); renderContacts(); refreshReqBadge(); await reloadConversations(); if (r.data && r.data.group_id) { const c = S.convs.get(convKey('group', r.data.group_id)); if (c) openConversation(c); } } else toast(r.msg || '操作失败');
    }));
    $$('[data-gr]', panel).forEach(b => b.addEventListener('click', async () => {
      const r = await api('/group/invite/reject', { invite_id: +b.dataset.gr });
      if (r.code === 0) { renderContacts(); refreshReqBadge(); } else toast(r.msg || '操作失败');
    }));
    $$('[data-ota]', panel).forEach(b => b.addEventListener('click', async () => {
      const r = await api('/group/owner/transfer/accept', { transfer_id: +b.dataset.ota });
      if (r.code === 0) { toast(r.msg || '已成为群主'); renderContacts(); refreshReqBadge(); await reloadConversations(); if (S.current && S.current.type === 'group') showGroupInfo(); } else toast(r.msg || '操作失败');
    }));
    $$('[data-otr]', panel).forEach(b => b.addEventListener('click', async () => {
      const r = await api('/group/owner/transfer/reject', { transfer_id: +b.dataset.otr });
      if (r.code === 0) { renderContacts(); refreshReqBadge(); } else toast(r.msg || '操作失败');
    }));
    $$('[data-jra]', panel).forEach(b => b.addEventListener('click', async () => {
      const r = await api('/group/join/approve', { request_id: +b.dataset.jra });
      if (r.code === 0) { toast('已通过入群申请'); renderContacts(); refreshReqBadge(); } else toast(r.msg || '操作失败');
    }));
    $$('[data-jrr]', panel).forEach(b => b.addEventListener('click', async () => {
      const r = await api('/group/join/reject', { request_id: +b.dataset.jrr });
      if (r.code === 0) { toast('已拒绝申请'); renderContacts(); refreshReqBadge(); } else toast(r.msg || '操作失败');
    }));
    $$('[data-ca]', panel).forEach(b => b.addEventListener('click', async () => {
      const r = await api('/channel/invite/accept', { invite_id: +b.dataset.ca });
      if (r.code === 0) { toast('已订阅频道'); renderContacts(); refreshReqBadge(); await reloadConversations(); if (r.data && r.data.channel_id) { const c = S.convs.get(convKey('channel', r.data.channel_id)); if (c) openConversation(c); } } else toast(r.msg || '操作失败');
    }));
    $$('[data-cr]', panel).forEach(b => b.addEventListener('click', async () => {
      const r = await api('/channel/invite/reject', { invite_id: +b.dataset.cr });
      if (r.code === 0) { renderContacts(); refreshReqBadge(); } else toast(r.msg || '操作失败');
    }));
  }
  /* 背景选择弹窗：设置背景(上传)/恢复默认 + 历史网格（最多 6 张，点选切回） */
  function afterBgChange(kind) {
    applyAppearance();
    if (kind === 'profile') { renderProfile(); paintDrawerHead(); }
    refreshSettings();
  }
  async function openBgPicker(kind) {
    const label = kind === 'global' ? '全局背景' : '资料页封面';
    let r;
    try { r = await api('/user/bg/history?kind=' + kind); }
    catch (e) { return; }
    if (r.code !== 0) { toast(r.msg || '加载失败'); return; }
    const cur = r.data.current || '';
    const items = r.data.items || [];
    const tile = (url) => '<div class="bgp-tile' + (url === cur ? ' active' : '') + '" data-url="' + esc(url) + '" style="background-image:url(&quot;' + esc(url) + '&quot;)"></div>';
    const grid = items.length ? items.map(it => tile(it.url)).join('')
      : '<div class="modal-hint">还没有历史背景，点“设置背景”上传第一张</div>';
    const body =
      '<div class="bgp-actions">' +
        '<button class="btn bgp-upload" id="bgpUpload"><i class="ic ic-image"></i><span>设置背景</span></button>' +
        (cur ? '<button class="btn bgp-default" id="bgpDefault"><i class="ic ic-trash"></i><span>恢复默认</span></button>' : '') +
      '</div>' +
      '<div class="modal-hint" style="margin:14px 0 6px">历史背景（最多 6 张，超出自动删最旧）· 点选可切回</div>' +
      '<div class="bgp-grid" id="bgpGrid">' + grid + '</div>';
    openModal(label, body, null);
    const doSet = async (url) => {
      const rr = await api('/user/bg/set', { kind, url });
      if (rr.code === 0) { S.me = rr.data; afterBgChange(kind); openBgPicker(kind); }
      else toast(rr.msg || '设置失败');
    };
    const up = $('#bgpUpload');
    if (up) up.addEventListener('click', () => {
      const inp = document.createElement('input');
      inp.type = 'file'; inp.accept = 'image/*';
      inp.onchange = async () => {
        const f = inp.files[0]; if (!f) return;
        const fd = new FormData(); fd.append('file', f);
        try {
          const res = await fetch('/upload/image', { method: 'POST', body: fd, credentials: 'same-origin' });
          const upj = await res.json();
          if (upj.code !== 0) { toast(upj.msg || '上传失败'); return; }
          await doSet(upj.data.avatar_url);
        } catch (e) { toast('上传失败'); }
      };
      inp.click();
    });
    const df = $('#bgpDefault');
    if (df) df.addEventListener('click', async () => {
      const rr = await api('/user/bg/clear', { kind });
      if (rr.code === 0) { S.me = rr.data; afterBgChange(kind); closeModal(); toast('已恢复默认'); }
      else toast(rr.msg || '操作失败');
    });
    $$('#bgpGrid .bgp-tile').forEach(t => t.addEventListener('click', () => { if (t.dataset.url !== cur) doSet(t.dataset.url); }));
  }
  function renderSettings() {
    const host = $('#drawerSettings');
    if (!host) return;
    const me = S.me || {};
    const isDark = me.theme !== 'light';
    const check = '<i class="ic ic-check" style="margin-left:auto;color:var(--tg-blue)"></i>';
    const chev = '<i class="ic ic-down chev"></i>';
    // 展开状态存 S.settingsOpen（settings=顶层，appearance/account=子组），重渲染后保持不变
    if (!S.settingsOpen) S.settingsOpen = new Set();
    const open = k => S.settingsOpen.has(k) ? ' open' : '';
    const appearanceItems =
      '<button class="settings-item" data-act="themeLight"><i class="ic ic-sun"></i><span>浅色模式</span>' + (isDark ? '' : check) + '</button>' +
      '<button class="settings-item" data-act="themeDark"><i class="ic ic-moon"></i><span>深色模式</span>' + (isDark ? check : '') + '</button>' +
      '<button class="settings-item" data-act="bgGlobal"><i class="ic ic-image"></i><span>' + (me.global_bg ? '更换全局背景' : '设置全局背景') + '</span></button>' +
      (me.global_bg ? '<div class="sec-range"><span class="sr-label">磨砂不透明度</span><input type="range" min="10" max="90" step="1" data-opacity value="' + (me.bg_opacity == null ? 55 : +me.bg_opacity) + '"><b class="sr-val">' + (me.bg_opacity == null ? 55 : me.bg_opacity) + '%</b></div>' : '') +
      '<button class="settings-item" data-act="bg"><i class="ic ic-image"></i><span>' + (me.background ? '更换资料页封面' : '设置资料页封面') + '</span></button>';
    const pvLabel = me.profile_visibility === 3 ? '仅自己' : (me.profile_visibility === 2 ? '仅好友' : '所有人');
    const accountItems =
      '<button class="settings-item" data-act="profile"><i class="ic ic-user"></i><span>编辑个人资料</span></button>' +
      '<button class="settings-item" data-act="profileVis"><i class="ic ic-lock"></i><span>个人资料展示</span><span class="si-hint">' + pvLabel + '</span></button>' +
      '<button class="settings-item" data-act="password"><i class="ic ic-lock"></i><span>修改密码</span></button>';
    const group = (key, icon, title, sub, inner) =>
      '<div class="sec-group' + open(key) + '" data-g="' + key + '">' +
      '<button class="sec-group-head ds-toggle"><i class="ic ' + icon + '"></i><span>' + title + '</span>' +
      (sub ? '<span class="sg-sub">' + esc(sub) + '</span>' : '') + chev + '</button>' +
      '<div class="sec-group-body"><div class="sec-group-inner">' + inner + '</div></div></div>';
    host.innerHTML =
      '<div class="sec-group' + open('settings') + '" data-g="settings">' +
      '<button class="sec-group-head ds-toggle"><i class="ic ic-tab-settings"></i><span>设置</span>' + chev + '</button>' +
      '<div class="sec-group-body"><div class="sec-group-inner">' +
      group('appearance', 'ic-sun', '外观', isDark ? '深色' : '浅色', appearanceItems) +
      group('account', 'ic-user', '账号', '', accountItems) +
      '</div></div></div>';
    // 分组头（设置/外观/账号）：就地展开/收起，不关闭抽屉
    $$('.ds-toggle', host).forEach(h => h.addEventListener('click', () => {
      const box = h.closest('.sec-group');
      const isOpen = box.classList.toggle('open');
      if (isOpen) S.settingsOpen.add(box.dataset.g); else S.settingsOpen.delete(box.dataset.g);
    }));
    // 叶子项：先收起抽屉再执行动作（与顶部抽屉项一致）
    $$('.settings-item', host).forEach(b => b.addEventListener('click', () => {
      const a = b.dataset.act; hideDrawer(); setTimeout(() => actions[a] && actions[a](), 220);
    }));
    // 背景磨砂不透明度：拖动实时预览，停手 500ms 后防抖保存
    const op = $('[data-opacity]', host);
    if (op) {
      let timer = null;
      op.addEventListener('input', () => {
        document.documentElement.style.setProperty('--bg-alpha', op.value / 100);
        $('.sr-val', op.parentElement).textContent = op.value + '%';
        clearTimeout(timer);
        timer = setTimeout(() => api('/user/update/personal_info', { bg_opacity: +op.value }).then(r => { if (r.code === 0) S.me = r.data; }), 500);
      });
    }
  }
  async function renderProfile() {
    const panel = $('#panelProfile');
    const me = S.me;
    // 头部封面：有背景图时铺满（加暗角渐变保证文字可读），头像贴右下角、昵称/Ping号贴左下角
    // 注意：background-image 必须用 DOM API 赋值——拼进 style="..." 属性时 url 的引号会截断属性
    const hasBg = !!me.background;
    panel.innerHTML = '<div class="profile-hero' + (hasBg ? ' has-bg' : '') + '">' +
      '<div class="profile-idbox"><div class="profile-name">' + esc(me.nickname || '未命名') + '</div>' +
      '<div class="profile-num">Ping号: ' + esc(me.number || '') + '</div></div>' +
      '<img class="profile-avatar" id="pfAvatar" src="' + esc(me.avatar || ('https://robohash.org/me' + me.id)) + '" alt="">' +
      '</div>' +
      '<div class="profile-card">' +
      '<div class="p-row"><span class="k">邮箱</span><span class="v">' + esc(me.email || '') + '</span></div>' +
      '<div class="p-row"><span class="k">签名</span><span class="v">' + esc(me.signature || '未设置') + '</span></div></div>' +
      '<div class="pf-moments"><div class="pf-moments-head"><i class="ic ic-tab-moments"></i><span>我的动态</span></div>' +
      '<div id="pfMomentsFeed"><div class="loading-hint">加载中…</div></div></div>';
    const ava = $('#pfAvatar');
    if (hasBg) {
      panel.querySelector('.profile-hero').style.backgroundImage =
        'linear-gradient(180deg,rgba(10,20,30,.08),rgba(10,20,30,.42)),url("' + me.background + '")';
    }
    watchAvatar(ava, me.nickname);
    ava.addEventListener('click', () => openAvatarViewer(fullSrc(ava), ava.getBoundingClientRect(), true, me.nickname));
    // 名片下方：只展示当前用户自己发布的动态（后端 /moment/user/<uid> 已按 user_id 过滤，本人含“仅自己”全部可见）
    const feed = $('#pfMomentsFeed');
    const r = await api('/moment/user/' + me.id).catch(() => null);
    if (!feed) return; // 面板已被切换/重渲染
    if (!r || r.code !== 0) { feed.innerHTML = '<div class="empty-hint">动态加载失败</div>'; return; }
    const list = (r.data && r.data.moments) || [];
    list.sort((a, b) => b.moment_id - a.moment_id); // 时间降序兑底：最新在上（后端已 id desc，此处再保证一次）
    feed.innerHTML = '';
    if (!list.length) { feed.innerHTML = '<div class="empty-hint">还没有发布过动态</div>'; return; }
    list.forEach(m => feed.appendChild(momentCardHtml(m)));
    bindMomentEvents(feed);
  }

  /* ---------------- 弹层动作 ---------------- */
  /* 弹层内的头像选择器（新建群组/频道共用）：选文件只做本地预览，不碰服务器；
     点“创建”时才真正上传，取消/校验失败不会在服务器留下孤儿文件 */
  function avatarPickerHtml() {
    return '<div class="avatar-picker"><img id="avaPickImg" alt=""><input type="file" id="avaPickFile" accept="image/*" hidden></div>' +
      '<div class="modal-hint" style="margin:0 0 12px">点击圆形选择头像（可选，10MB 以内，随创建一起上传）</div>';
  }
  function bindAvatarPicker() {
    const state = { file: null };
    const img = $('#avaPickImg'), input = $('#avaPickFile');
    if (!img) return state;
    img.src = letterAvatar('\u70b9\u51fb\u4e0a\u4f20');
    img.parentElement.addEventListener('click', () => input.click());
    input.addEventListener('change', () => {
      const f = input.files[0];
      if (!f) return;
      if (f.size > 10 * 1024 * 1024) { toast('图片大小限制为 10MB'); input.value = ''; return; }
      state.file = f;
      img.src = URL.createObjectURL(f); // 本地预览，零网络请求
    });
    return state;
  }
  /* 提交时上传已选头像；成功返回路径，失败 toast 后返回 null */
  async function uploadPickedAvatar(state) {
    const fd = new FormData();
    fd.append('file', state.file);
    try {
      const res = await fetch('/upload/image', { method: 'POST', body: fd });
      const r = await res.json();
      if (r.code === 0) return r.data.avatar_url;
      toast(r.msg || '头像上传失败');
    } catch (e) { toast('头像上传失败，请重试'); }
    return null;
  }

  /* 群/频道“编辑信息”弹窗（共用）：头像 + 名称 + 简介。
     头像先做本地预览，点“保存”时才上传 /upload/image，再回调 onSave；取消不会留下孤儿文件 */
  function openInfoEditor(opts) {
    let newFile = null;
    const body =
      '<div class="avatar-picker" id="ecAvaPick"><img id="ecAvaImg" alt=""><input type="file" id="ecAvaFile" accept="image/*" hidden></div>' +
      '<div class="modal-hint" style="margin:0 0 12px">点击头像更换（10MB 以内）</div>' +
      '<div class="field"><input id="ecName" class="modal-input" placeholder=" " autocomplete="off" value="' + esc(opts.name || '') + '"></div>' +
      '<div class="modal-hint" style="margin:-14px 0 6px">' + esc(opts.nameLabel || '名称') + '（必填）</div>' +
      '<div class="field"><textarea id="ecDesc" class="modal-input" rows="3" placeholder=" " style="resize:vertical">' + esc(opts.desc || '') + '</textarea></div>' +
      '<div class="modal-hint" style="margin:-14px 0 6px">简介（可选，200 字内）</div>';
    openModal(opts.title || '编辑信息', body, async () => {
      const name = $('#ecName').value.trim();
      if (!name) { toast('名称不能为空'); return; }
      const description = $('#ecDesc').value.trim();
      let avatarUrl = null;
      if (newFile) {
        const fd = new FormData(); fd.append('file', newFile);
        let r;
        try { const res = await fetch('/upload/image', { method: 'POST', body: fd, credentials: 'same-origin' }); r = await res.json(); }
        catch (e) { toast('头像上传失败，请重试'); return; }
        if (!r || r.code !== 0) { toast((r && r.msg) || '头像上传失败'); return; }
        avatarUrl = r.data.avatar_url;
      }
      await opts.onSave({ name, description, avatarUrl });
    }, '保存');
    const img = $('#ecAvaImg'), input = $('#ecAvaFile');
    img.src = opts.avatar || letterAvatar(opts.name || '头像');
    $('#ecAvaPick').addEventListener('click', () => input.click());
    input.addEventListener('change', () => {
      const f = input.files && input.files[0]; if (!f) return;
      if (f.size > 10 * 1024 * 1024) { toast('图片大小限制为 10MB'); input.value = ''; return; }
      newFile = f; img.src = URL.createObjectURL(f);
    });
  }

  const actions = {
    async newgroup() {
      const tr = await api('/bot/templates?scope=group');
      const tpls = (tr && tr.code === 0 && tr.data.templates) ? tr.data.templates : [];
      const botHtml = tpls.length ? ('<div class="comments-title" style="margin:12px 0 6px">配置机器人（可选，多选）</div><div class="bot-pick">' +
        tpls.map(t => '<label class="bot-pick-item"><input type="checkbox" value="' + t.id + '" title="' + esc(t.description || '') + '"><span>' + esc(t.name) + '</span></label>').join('') + '</div>') : '';
      openModal('新建群组',
        avatarPickerHtml() +
        '<div class="field"><input id="mGName" class="modal-input" placeholder=" " autocomplete="off"></div><div class="modal-hint" style="margin:-14px 0 6px">群名称（必填）</div>' +
        '<div class="field"><input id="mGDesc" class="modal-input" placeholder=" " autocomplete="off"></div><div class="modal-hint" style="margin:-14px 0 6px">群简介（可选）</div>' +
        '<div class="field"><input id="mGMax" class="modal-input" placeholder=" " type="number" value="200"></div><div class="modal-hint" style="margin:-14px 0 6px">人数上限</div>' +
        botHtml,
        async () => {
          const group_name = $('#mGName').value.trim();
          if (!group_name) { toast('群名称不能为空'); return; }
          let avatarUrl = '';
          if (ava.file) { avatarUrl = await uploadPickedAvatar(ava); if (!avatarUrl) return; }
          const payload = { group_name, group_desc: $('#mGDesc').value.trim(), max_members: +$('#mGMax').value || 200 };
          if (avatarUrl) payload.avatar = avatarUrl;
          payload.bot_template_ids = Array.from(document.querySelectorAll('#modalBody .bot-pick input:checked')).map(c => +c.value);
          const r = await api('/group/create', payload);
          if (r.code === 200 || r.code === 0) {
            closeModal(); toast('群组创建成功');
            await reloadConversations();
            openConversation({ conv_type: 'group', target_id: r.data.id, name: r.data.name, avatar: r.data.avatar, updated_at: new Date().toISOString() });
          } else toast(r.msg || '创建失败');
        }, '创建');
      const ava = bindAvatarPicker();
    },
    async newchannel() {
      const tr = await api('/bot/templates?scope=channel');
      const tpls = (tr && tr.code === 0 && tr.data.templates) ? tr.data.templates : [];
      const botHtml = tpls.length ? ('<div class="comments-title" style="margin:12px 0 6px">配置机器人（可选，多选）</div><div class="bot-pick">' +
        tpls.map(t => '<label class="bot-pick-item"><input type="checkbox" value="' + t.id + '" title="' + esc(t.description || '') + '"><span>' + esc(t.name) + '</span></label>').join('') + '</div>') : '';
      openModal('新建频道',
        avatarPickerHtml() +
        '<div class="field"><input id="mCName" class="modal-input" placeholder=" " autocomplete="off"></div><div class="modal-hint" style="margin:-14px 0 6px">频道名称（必填）</div>' +
        '<div class="field"><input id="mCDesc" class="modal-input" placeholder=" " autocomplete="off"></div><div class="modal-hint" style="margin:-14px 0 6px">频道简介（可选）</div>' +
        botHtml,
        async () => {
          const channel_name = $('#mCName').value.trim();
          if (!channel_name) { toast('频道名称不能为空'); return; }
          let avatarUrl = '';
          if (ava.file) { avatarUrl = await uploadPickedAvatar(ava); if (!avatarUrl) return; }
          const payload = { channel_name, channel_desc: $('#mCDesc').value.trim() };
          if (avatarUrl) payload.avatar = avatarUrl;
          payload.bot_template_ids = Array.from(document.querySelectorAll('#modalBody .bot-pick input:checked')).map(c => +c.value);
          const r = await api('/channel/create', payload);
          if (r.code === 0) {
            closeModal(); toast('频道创建成功');
            await reloadConversations();
            openConversation({ conv_type: 'channel', target_id: r.data.id, name: r.data.name, avatar: r.data.avatar, updated_at: new Date().toISOString() });
          } else toast(r.msg || '创建失败');
        }, '创建');
      const ava = bindAvatarPicker();
    },
    join() {
      openModal('添加 · 搜索',
        '<div class="join-field" id="mJoinField">' +
          '<button type="button" class="join-type" id="mJoinType"><span id="mJoinTypeLabel">全部</span><svg class="jt-caret" width="12" height="12" viewBox="0 0 24 24"><path d="M6 9l6 6 6-6" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg></button>' +
          '<input id="mJoinNum" class="jf-input" placeholder="搜索昵称 / 名称 / 编号" autocomplete="off">' +
        '</div>' +
        '<div class="modal-hint" id="mJoinHint"></div>' +
        '<div id="mJoinResult"></div>',
        null);
      const input = $('#mJoinNum'), box = $('#mJoinResult'), hint = $('#mJoinHint');
      const typeBtn = $('#mJoinType'), typeLabel = $('#mJoinTypeLabel');
      const LABEL = { all: '全部', user: '用户', group: '群聊', channel: '频道' };
      const TIP = { all: '在所有用户 / 群聊 / 频道中搜索，回车开始。', user: '仅搜索用户，可输入昵称或编号（无需 user_ 前缀）。', group: '仅搜索群聊，可输入群名或编号（无需 group_ 前缀）。', channel: '仅搜索公开频道，可输入频道名或编号（无需 channel_ 前缀）。' };
      let stype = 'all';
      // 悬浮菜单：挂到 body 用 fixed 定位，跳出弹窗 overflow 裁切，深色/浅色主题自适应
      const menu = document.createElement('div');
      menu.className = 'join-menu';
      menu.innerHTML = ['all', 'user', 'group', 'channel'].map(k => '<div class="jm-item' + (k === 'all' ? ' on' : '') + '" data-st="' + k + '">' + LABEL[k] + '<svg class="jm-check" width="14" height="14" viewBox="0 0 24 24"><path d="M5 12l5 5 9-10" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/></svg></div>').join('');
      document.body.appendChild(menu);
      let opened = false;
      function place() {
        const r = typeBtn.getBoundingClientRect();
        menu.style.left = r.left + 'px';
        menu.style.minWidth = Math.max(r.width, 120) + 'px';
        if (window.innerHeight - r.bottom < 190 && r.top > 200) { menu.style.top = 'auto'; menu.style.bottom = (window.innerHeight - r.top + 6) + 'px'; }
        else { menu.style.bottom = 'auto'; menu.style.top = (r.bottom + 6) + 'px'; }
      }
      function showMenu() { place(); menu.classList.add('show'); typeBtn.classList.add('open'); opened = true; }
      function hideMenu() { menu.classList.remove('show'); typeBtn.classList.remove('open'); opened = false; }
      function applyType() {
        typeLabel.textContent = LABEL[stype];
        $$('.jm-item', menu).forEach(m => m.classList.toggle('on', m.dataset.st === stype));
        hint.textContent = TIP[stype];
      }
      typeBtn.addEventListener('click', (e) => { e.stopPropagation(); opened ? hideMenu() : showMenu(); });
      $$('.jm-item', menu).forEach(m => m.addEventListener('click', (e) => {
        e.stopPropagation(); stype = m.dataset.st; hideMenu(); applyType(); input.focus();
      }));
      function onDocDown(e) { if (opened && !e.target.closest('.join-menu') && !e.target.closest('#mJoinType')) hideMenu(); }
      function onReposition() { if (opened) place(); }
      document.addEventListener('mousedown', onDocDown, true);
      window.addEventListener('resize', onReposition);
      window.addEventListener('scroll', onReposition, true);
      modalCleanup = () => {
        document.removeEventListener('mousedown', onDocDown, true);
        window.removeEventListener('resize', onReposition);
        window.removeEventListener('scroll', onReposition, true);
        menu.remove();
      };
      applyType();
      const rowHtml = (kind, x) => {
        const id = x.user_id || x.group_id || x.channel_id;
        const name = kind === 'user' ? dispName(id, x.nickname || x.name) : (x.nickname || x.name);
        const avatar = esc(x.avatar || 'https://robohash.org/' + id);
        const num = stripNum(x.number);
        let sub, btns;
        if (kind === 'user') {
          sub = num;
          const self = id === S.me.id;
          btns = (self ? '' : (x.is_friend ? '<span class="mini-btn" style="opacity:.6">已是好友</span>' : '<button class="mini-btn" data-act="friend" data-id="' + id + '">加好友</button>')) +
            (self ? '' : '<button class="mini-btn blue" data-act="open" data-kind="private" data-id="' + id + '" data-name="' + esc(name) + '" data-avatar="' + avatar + '">发消息</button>');
        } else if (kind === 'group') {
          sub = (x.member_count || 0) + ' 名成员' + (num ? ' · ' + num : '');
          btns = x.joined
            ? '<button class="mini-btn blue" data-act="open" data-kind="group" data-id="' + id + '" data-name="' + esc(name) + '" data-avatar="' + avatar + '" data-joined="1">进入</button>'
            : (x.pending
              ? '<span class="mini-btn" style="opacity:.6">申请中</span>'
              : '<button class="mini-btn blue" data-act="request" data-kind="group" data-id="' + id + '" data-name="' + esc(name) + '" data-avatar="' + avatar + '">申请加入</button>');
        } else {
          sub = (x.subscriber_count || 0) + ' 订阅者' + (num ? ' · ' + num : '');
          btns = '<button class="mini-btn blue" data-act="open" data-kind="channel" data-id="' + id + '" data-name="' + esc(name) + '" data-avatar="' + avatar + '" data-joined="' + (x.joined ? 1 : 0) + '">进入</button>';
        }
        return '<div class="list-row"><img src="' + avatar + '"><div class="lr-main js-peek" data-peek="' + kind + '" data-id="' + id + '" style="cursor:pointer"><div class="lr-t">' + esc(name) + '</div><div class="lr-s">' + esc(sub) + '</div></div>' + btns + '</div>';
      };
      function bindResults() {
        $$('.js-peek', box).forEach(el => el.addEventListener('click', (e) => {
          e.stopPropagation();
          const kind = el.dataset.peek, id = +el.dataset.id;
          if (kind === 'user') openUserProfile(id, el);
          else openPeek(kind, id, el);
        }));
        $$('[data-act="friend"]', box).forEach(b => b.addEventListener('click', async () => {
          const r = await api('/friend/request', { to_user_id: +b.dataset.id, message: '我是 ' + (S.me.nickname || '') });
          if (r.code === 0) { toast('好友申请已发送'); b.outerHTML = '<span class="mini-btn" style="opacity:.6">已申请</span>'; refreshReqBadge(); }
          else toast(r.msg || '发送失败');
        }));
        $$('[data-act="request"]', box).forEach(b => b.addEventListener('click', async () => {
          const id = +b.dataset.id;
          b.disabled = true;
          const j = await api('/group/join', { group_id: id });
          if (j.code !== 0 && j.code !== 200) { b.disabled = false; toast(j.msg || '申请失败'); return; }
          if (j.data && j.data.already) { closeModal(); await reloadConversations(); const c = S.convs.get(convKey('group', id)); openConversation(c || { conv_type: 'group', target_id: id, name: b.dataset.name, avatar: b.dataset.avatar, updated_at: new Date().toISOString() }); return; }
          toast(j.msg || '入群申请已发送，等待审核');
          b.outerHTML = '<span class="mini-btn" style="opacity:.6">申请中</span>';
        }));
        $$('[data-act="open"]', box).forEach(b => b.addEventListener('click', async () => {
          const kind = b.dataset.kind, id = +b.dataset.id;
          // 频道：无需订阅即可直接进入查看帖子（是否“加入频道”由频道内底部按钮决定，不在这里强制订阅）
          closeModal();
          await reloadConversations();
          const stored = S.convs.get(convKey(kind, id));
          openConversation(stored || { conv_type: kind, target_id: id, name: b.dataset.name, avatar: b.dataset.avatar, updated_at: new Date().toISOString() });
        }));
      }
      async function doSearch() {
        const raw = input.value.trim();
        if (!raw) { box.innerHTML = ''; return; }
        box.innerHTML = '<div class="loading-hint">搜索中…</div>';
        // 前端不拼前缀，由后端根据 type 自动匹配 user_/group_/channel_
        const r = await api('/search/keyword', { keyword: raw, type: stype });
        if (!r || r.code !== 0) { box.innerHTML = '<div class="modal-hint">' + esc((r && r.msg) || '搜索失败') + '</div>'; return; }
        const res = r.data.results || {};
        const users = res.users || [], groups = res.groups || [], channels = res.channels || [];
        if (!users.length && !groups.length && !channels.length) { box.innerHTML = '<div class="modal-hint">没有匹配结果</div>'; return; }
        let html = '';
        if (users.length) html += '<div class="search-cat">用户</div>' + users.map(u => rowHtml('user', u)).join('');
        if (groups.length) html += '<div class="search-cat">群聊</div>' + groups.map(g => rowHtml('group', g)).join('');
        if (channels.length) html += '<div class="search-cat">频道</div>' + channels.map(c => rowHtml('channel', c)).join('');
        box.innerHTML = html;
        bindResults();
      }
      input.addEventListener('keydown', (e) => { if (e.key === 'Enter') doSearch(); });
      setTimeout(() => input.focus(), 300);
    },
    /* 我的想法：向管理员提交想法/建议/问题（文字 + 图片），并查看历史与管理员回复 */
    feedback() { openFeedbackModal(); },
    /* 外观：主题切换 / 全局背景（与资料接口共用 personal_info） */
    themeLight() { setTheme('light'); },
    themeDark() { setTheme('dark'); },
    /* 资料卡“我的动态”可见范围：弹窗选 所有人/仅好友/仅自己（基础信息始终展示，不受此项控制） */
    profileVis() {
      const cur = S.me.profile_visibility || 1;
      const opts = [
        { v: 1, t: '所有人', d: '你的资料卡动态对所有人可见' },
        { v: 2, t: '仅好友', d: '只有好友能在你的资料卡看到动态' },
        { v: 3, t: '仅自己', d: '动态仅自己可见，他人资料卡显示为锁定' },
      ];
      const body = '<div class="modal-hint" style="margin:0 0 12px;line-height:1.6">选择资料卡中“我的动态”的可见范围。<b>无论选择哪一项，头像、昵称、Ping号、签名等基础信息都会正常展示。</b></div>' +
        opts.map(o => '<button class="pv-opt' + (o.v === cur ? ' on' : '') + '" data-pvv="' + o.v + '" type="button">' +
          '<div class="pv-opt-l"><div class="pv-opt-t">' + esc(o.t) + '</div><div class="pv-opt-d">' + esc(o.d) + '</div></div>' +
          '<i class="ic ic-check pv-opt-c"></i></button>').join('');
      openModal('个人资料展示', body, null);
      $$('#modalBody .pv-opt').forEach(btn => btn.addEventListener('click', async () => {
        const v = +btn.dataset.pvv;
        if (v === cur) { closeModal(); return; }
        const r = await api('/user/update/personal_info', { profile_visibility: v });
        if (r.code === 0) { S.me = r.data; refreshSettings(); toast('已设置为：' + (opts.find(o => o.v === v) || {}).t); closeModal(); }
        else toast(r.msg || '设置失败');
      }));
    },
    bgGlobal() { openBgPicker('global'); },
    bg() { openBgPicker('profile'); },
    profile() {
      const cur = S.me.gender || 0;
      let selectedGender = cur;
      let avatarFile = null;
      openModal('编辑资料',
        '<div class="avatar-picker" id="pfAvaPick"><img id="pfAvaImg" alt=""><input type="file" id="pfAvaFile" accept="image/*" hidden></div>' +
        '<div class="modal-hint" style="margin:0 0 14px;text-align:center">点击头像更换（10MB以内）</div>' +
        '<div class="field"><input id="mNick" class="modal-input" placeholder=" " autocomplete="off" value="' + esc(S.me.nickname) + '"></div><div class="modal-hint" style="margin:-14px 0 6px">昵称</div>' +
        '<div class="field"><input id="mSig" class="modal-input" placeholder=" " autocomplete="off" value="' + esc(S.me.signature || '') + '"></div><div class="modal-hint" style="margin:-14px 0 6px">个性签名</div>' +
        '<div class="modal-hint" style="margin:14px 0 6px">性别</div>' +
        '<div class="pub-vis" id="mGender"><span class="chip' + (cur === 0 ? ' on' : '') + '" data-g="0">未知</span><span class="chip' + (cur === 1 ? ' on' : '') + '" data-g="1">男</span><span class="chip' + (cur === 2 ? ' on' : '') + '" data-g="2">女</span></div>',
        async () => {
          // 先上传头像（如果有选择新文件）
          if (avatarFile) {
            const fd = new FormData(); fd.append('file', avatarFile);
            try {
              const res = await fetch('/upload/avatar', { method: 'POST', body: fd, credentials: 'same-origin' });
              const ur = await res.json();
              if (ur.code !== 0) { toast(ur.msg || '头像上传失败'); return; }
            } catch (e) { toast('头像上传失败'); return; }
          }
          const r = await api('/user/update/personal_info', { nickname: $('#mNick').value.trim(), signature: $('#mSig').value.trim(), gender: selectedGender });
          if (r.code === 0) {
            // 头像可能已被 /upload/avatar 更新，重新拉取 /me 获取最新数据
            try { const me = await api('/user/me'); if (me && me.code === 0) S.me = me.data; } catch (e) { S.me = r.data; }
            fillDrawer(); closeModal(); toast('资料已更新');
          } else toast(r.msg || '保存失败');
        }, '保存');
      // 头像选择器绑定
      const avaImg = $('#pfAvaImg'), avaInput = $('#pfAvaFile');
      avaImg.src = S.me.avatar || letterAvatar(S.me.nickname || '我');
      $('#pfAvaPick').addEventListener('click', () => avaInput.click());
      avaInput.addEventListener('change', () => {
        const f = avaInput.files[0]; if (!f) return;
        if (f.size > 10 * 1024 * 1024) { toast('图片大小限制为10MB'); avaInput.value = ''; return; }
        avatarFile = f;
        avaImg.src = URL.createObjectURL(f);
      });
      $$('#mGender .chip').forEach(c => c.addEventListener('click', () => { selectedGender = +c.dataset.g; $$('#mGender .chip').forEach(x => x.classList.toggle('on', x === c)); }));
    },
    password() {
      openModal('修改密码',
        '<div class="field-inline"><input id="mPwdCode" class="modal-input" placeholder=" " autocomplete="off" style="flex:1"><button class="code-btn" id="mPwdSend">发送验证码</button></div>' +
        '<div class="modal-hint" style="margin:6px 0 2px">验证码将发送到绑定邮箱 ' + esc((S.me.email || '').replace(/(.)(.*)(.)/, '$1**$3')) + '</div>' +
        '<div class="field" style="margin-top:14px"><input id="mPwdNew" class="modal-input" placeholder=" " type="password" autocomplete="new-password"></div><div class="modal-hint" style="margin:-14px 0 6px">新密码</div>',
        async () => {
          const r = await api('/user/update/password', { eCode: $('#mPwdCode').value.trim(), new_password: $('#mPwdNew').value });
          if (r.code === 0) { closeModal(); toast('密码修改成功'); } else toast(r.msg || '修改失败');
        }, '确认修改');
      const btn = $('#mPwdSend');
      btn.addEventListener('click', async () => {
        const r = await api('/user/mail/update_passwd/captcha', {});
        if (r.code === 0) {
          toast('验证码已发送');
          let s = 60; btn.disabled = true;
          const timer = setInterval(() => { btn.textContent = s + ' 秒'; if (--s < 0) { clearInterval(timer); btn.disabled = false; btn.textContent = '重新发送'; } }, 1000);
        } else toast(r.msg || '发送失败');
      });
    },
    blacklist() { openBlacklist(); },
    notify() { openNotificationList(); },
    logout() {
      openModal('退出登录', '<div>确定要退出当前账号吗？</div>', async () => {
        await fetch('/user/logout', { credentials: 'same-origin' });
        location.href = '/user/login';
      }, '退出');
    }
  };

  /* ---------------- 黑名单：拉黑/取消拉黑 用户/群聊/频道 ---------------- */
  function blkKey(type, id) { return type + ':' + id; }
  function blkType(type) { return type === 'group' ? '群聊' : (type === 'channel' ? '频道' : '用户'); }
  function isBlocked(type, id) { return S.blocked.has(blkKey(type, id)); }
  function updateBlkBadge() { const b = $('#drawerBlkBadge'); if (b) { const n = S.blocked.size; b.textContent = n; b.hidden = !n; } }
  async function loadBlocked() {
    const r = await api('/friend/blacklist/list').catch(() => null);
    S.blocked = new Set();
    if (r && r.code === 0 && r.data && r.data.items) r.data.items.forEach(it => S.blocked.add(blkKey(it.block_type, it.target_id)));
    updateBlkBadge();
  }
  // 拉黑（带二次确认）；成功后刷新黑名单/会话列表，若正打开该会话则收起
  function doBlock(type, id, name, after) {
    if (isBlocked(type, id)) { toast('已在黑名单中'); return; }
    const tips = {
      user: '拉黑后无法互相私聊、互相看不到对方动态，对方也搜不到你。',
      group: '拉黑后将不再接收该群聊消息，会话从列表隐藏。',
      channel: '拉黑后将不再接收该频道更新，频道从列表隐藏。',
    };
    const body = '<div class="modal-hint" style="margin:0;line-height:1.6">确定拉黑' + blkType(type) + ' <b>' + esc(name || '') + '</b>？<br>' + (tips[type] || '') + '</div>';
    openModal('拉黑' + blkType(type), body, async () => {
      const r = await api('/friend/blacklist/add', { block_type: type, target_id: id });
      if (r.code === 0) {
        closeModal(); toast('已拉黑');
        S.blocked.add(blkKey(type, id));
        updateBlkBadge();
        if (S.current && S.current.type === (type === 'user' ? 'private' : type) && S.current.id === id) {
          panelEl.classList.remove('info-open'); S.current = null; panelHide();
        }
        await reloadConversations();
        if (after) after();
      } else toast(r.msg || '拉黑失败');
    }, '确认拉黑', true);
  }
  // 取消拉黑；成功后刷新，若列表可见会自动回来
  async function doUnblock(type, id, after) {
    const r = await api('/friend/blacklist/remove', { block_type: type, target_id: id });
    if (r.code === 0) {
      toast('已取消拉黑');
      S.blocked.delete(blkKey(type, id));
      updateBlkBadge();
      await reloadConversations();
      if (after) after();
    } else toast(r.msg || '操作失败');
  }
  // 黑名单管理弹层（抽屉“黑名单”入口）：列表 + 取消拉黑（带粒子消散动画）
  async function openBlacklist() {
    await loadBlocked();
    const r = await api('/friend/blacklist/list').catch(() => null);
    const items = (r && r.code === 0 && r.data && r.data.items) ? r.data.items : [];
    const rows = items.length ? items.map(it => {
      const ava = it.avatar || letterAvatar(it.name);
      return '<div class="bl-row" data-type="' + it.block_type + '" data-id="' + it.target_id + '">' +
        '<img class="bl-ava" src="' + esc(ava) + '" alt="">' +
        '<div class="lr-main"><div class="lr-t">' + esc(it.name) + '</div>' +
        '<div class="lr-s">' + esc(stripNum(it.number) || blkType(it.block_type)) + '</div></div>' +
        '<span class="bl-kind">' + blkType(it.block_type) + '</span>' +
        '<button class="mini-btn blue" data-unblk type="button">取消拉黑</button></div>';
    }).join('') : '<div class="bl-empty">黑名单为空</div>';
    openModal('黑名单', '<div class="bl-list">' + rows + '</div>', null);
    $$('#modalBody [data-unblk]').forEach(btn => btn.addEventListener('click', async () => {
      const row = btn.closest('.bl-row');
      const type = row.dataset.type, id = +row.dataset.id;
      btn.disabled = true;
      const r2 = await api('/friend/blacklist/remove', { block_type: type, target_id: id });
      if (r2.code === 0) {
        S.blocked.delete(blkKey(type, id));
        updateBlkBadge();
        if (window.PARTICLES) { SFX.delete && SFX.delete(); PARTICLES.dissolve(row); }
        else row.remove();
        toast('已取消拉黑');
        await reloadConversations();
        if (!$$('#modalBody .bl-row').length) $('#modalBody').innerHTML = '<div class="bl-empty">黑名单为空</div>';
      } else { btn.disabled = false; toast(r2.msg || '操作失败'); }
    }));
  }

  // 系统通知列表弹层：展示历史通知（含转让被拒/已接受等）
  // 每条通知独立维护已读态：未读行带红点，点击单条即标记该条已读；也可一键全部已读
  async function openNotificationList() {
    const r = await api('/notification/list?limit=50');
    if (!r || r.code !== 0) { toast((r && r.msg) || '加载失败'); return; }
    const list = r.data.notifications || [];
    const html = list.length
      ? list.map(x => {
          const nav = (x.related_type === 'moment' || x.related_type === 'channel_post') && x.related_id;
          return '<div class="notif-row' + (x.is_read ? '' : ' unread') + (nav ? ' nav' : '') + '" data-nid="' + x.id + '" data-rtype="' + esc(x.related_type || '') + '" data-rid="' + (x.related_id || 0) + '"><div class="notif-dot"></div><div class="notif-body"><div class="notif-t">' + esc(x.title || '通知') + '<span class="notif-tag">' + esc(x.type_label || '系统') + '</span></div><div class="notif-c">' + esc(x.content || '') + '</div><div class="notif-time">' + esc(fmtRelTime(x.created_at)) + '</div></div></div>';
        }).join('')
      : '<div class="empty-hint">暂无系统通知</div>';
    openModal('系统通知', html, async () => {
      if (!list.some(x => !x.is_read)) { toast('没有未读通知'); return; }
      const rr = await api('/notification/read', { all: 1 });
      if (rr.code === 0) { toast('已全部标为已读'); refreshNotifBadge(); closeModal(); }
      else toast(rr.msg || '操作失败');
    }, '全部已读');
    // 逐条点击：未读则标已读；若为动态/频道帖子类通知（评论/回复/点赞）则跳转到对应内容并定位高亮
    $$('#modalBody .notif-row').forEach(row => row.addEventListener('click', async () => {
      if (row.classList.contains('unread')) {
        const id = +row.dataset.nid;
        const rr = await api('/notification/read', { ids: [id] });
        if (rr.code === 0) { row.classList.remove('unread'); refreshNotifBadge(); }
        else toast(rr.msg || '操作失败');
      }
      if (row.dataset.rtype === 'moment' && +row.dataset.rid) jumpToMoment(+row.dataset.rid);
      else if (row.dataset.rtype === 'channel_post' && +row.dataset.rid) jumpToChannelPostNotice(+row.dataset.rid);
    }));
  }
  // 从通知跳转到具体动态：切到动态页，命中当前页则直接定位，否则取单条插到最前再定位
  async function jumpToMoment(mid) {
    closeModal();
    switchTab('moments');
    await renderMoments();
    const feed = $('#momFeed'); if (!feed) return;
    let el = $('.moment-card[data-mid="' + mid + '"]', feed);
    if (!el) {
      const r = await api('/moment/' + mid);
      if (!r || r.code !== 0) { toast((r && r.msg) || '该动态不可见'); return; }
      const hint = $('.empty-hint', feed); if (hint) hint.remove();
      el = momentCardHtml(r.data);
      feed.insertBefore(el, feed.firstChild);
      bindMomentEvents(el);
    }
    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    el.classList.remove('sr-flash'); void el.offsetWidth; el.classList.add('sr-flash');
  }
  /* ---------------- 频道底部栏：主理人=发帖+铃铛 / 订阅者=铃铛 / 未订阅=加入频道 / 评论详情=评论输入框 ---------------- */
  function setChannelBottom(mode) {  // 'feed' | 'comment'
    if (typeof exitCmtReply === 'function') exitCmtReply();  // 切换底部栏模式时清除评论回复态
    const bar = $('#channelPublish'), pub = $('#publishPostBtn'), ta = $('#postText'), fab = $('#postSendBtn');
    const join = $('#channelJoinBtn');
    const role = S.current ? S.current.role : null;
    const subscribed = role === 1 || role === 2 || role === 3;
    const canPost = role === 1 || role === 2;
    if (mode === 'comment') {
      bar.classList.add('show'); bar.classList.remove('feed');
      pub.hidden = true;
      if (subscribed) { ta.hidden = false; fab.hidden = false; ta.placeholder = '发布评论…'; join.hidden = true; }
      else { ta.hidden = true; fab.hidden = true; join.hidden = false; }  // 未加入：只能看评论，底部给“加入频道”
    } else { // feed：按订阅状态三选一；未订阅（含已退订的残留态）只给“加入频道”，绝不能再露出发帖按钮
      bar.classList.add('feed');
      bar.classList.toggle('show', true);
      pub.hidden = !canPost;
      join.hidden = subscribed;
      ta.hidden = true; fab.hidden = true;
    }
  }
  // 通知切换按钮外观：文案即下一步动作（正常时显示“静音消息”，静音时显示“正常接收”）
  function paintNotifyBtn() {
    const btn = $('#channelNotifyBtn'); if (!btn) return;
    const muted = S.current && S.current.notify === 3;
    btn.classList.toggle('muted', !!muted);
    btn.textContent = muted ? '正常接收' : '静音消息';
    btn.title = muted ? '当前已静音，点击恢复正常通知' : '点击后新消息不再响铃，未读只显示灰点';
  }
  // 静音/恢复：更新本地状态 + 铃铛 + 会话列表角标形态
  async function setChannelNotify(level) {
    if (!S.current || S.current.type !== 'channel') return;
    const r = await api('/channel/notify/set', { channel_id: S.current.id, notify_level: level });
    if (r.code === 0) {
      S.current.notify = level;
      const stored = S.convs.get(convKey('channel', S.current.id)); if (stored) stored.notify_level = level;
      paintNotifyBtn();
      const el = $('[data-key="' + convKey('channel', S.current.id) + '"]', convListEl);
      if (el) {
        let badge = $('.conv-badge', el);
        if (level === 3) { if (badge) { badge.classList.add('dot'); badge.textContent = ''; } }
        else {
          const n = stored ? (stored.unread_count || 0) : 0;
          if (badge) { badge.classList.remove('dot'); if (n > 0) badge.textContent = n > 99 ? '99+' : String(n); else badge.remove(); }
        }
      }
      toast(level === 3 ? '已静音：新消息不再响铃，未读只显示灰点' : '已恢复正常通知');
    } else toast(r.msg || '操作失败');
  }

  function commentItemHtml(c) {
    const self = c.user_id === S.me.id;
    const name = self ? '我' : dispName(c.user_id, c.user_nickname || ('用户 ' + c.user_id));
    // 与私聊/群聊气泡一致：他人左侧带头像，自己靠右不带头像
    const ava = self ? '' : '<img class="msg-ava" alt="" src="' + esc(c.user_avatar || letterAvatar(name)) + '">';
    const cid = c.comment_id || c.id || '';
    const nick = name;
    const quote = c.reply_to_content ? '<div class="cmt-quote">' + esc(c.reply_to_content) + '</div>' : '';
    const replyTo = c.reply_to_nickname ? '<span class="cmt-rto">回复 <b>' + esc(c.reply_to_nickname) + '</b>：</span>' : '';
    return '<div class="msg-row ' + (self ? 'out' : 'in') + '">' + ava +
      '<div class="bubble cmt-bubble" data-cid="' + esc(cid) + '" data-self="' + (self ? '1' : '0') + '" data-created="' + esc(c.created_at || '') + '" data-nick="' + esc(nick) + '" data-content="' + esc(c.content || '') + '">' +
        (self ? '' : '<div class="sender" data-pu="' + c.user_id + '">' + esc(name) + '</div>') +
        '<div class="txt">' + quote + replyTo + esc(c.content) + '</div>' +
        '<span class="time">' + esc(fmtTime(c.created_at)) + '</span></div></div>';
  }

  // 评论区容器：无评论时不渲染，首条评论到达时动态创建
  // 兼容两种宿主：会话面板（msgListEl）与频道详情页媒体视图（mediaViewEl）内的帖子详情
  function ensureCommentsContainer() {
    // 优先级：帖子评论弹窗 > 媒体视图内详情 > 会话面板详情（弹窗/遮罩在最上层）
    const wrap = $('.post-detail-wrap', $('#modalBody')) || (mediaViewEl ? $('.post-detail-wrap', mediaViewEl) : null) || $('.post-detail-wrap', msgListEl);
    if (!wrap) return null;
    let box = $('#commentList');
    if (!box) {
      const div = document.createElement('div');
      div.className = 'pd-comments';
      div.innerHTML = '<div id="commentList"></div>';
      wrap.appendChild(div);
      box = $('#commentList');
    }
    return box;
  }

  /* ---------------- 帖子详情 + 评论（全幅内嵌视图：上=帖子 下=评论 两个子容器） ---------------- */
  async function openPostDetail(postId, highlight) {
    try {
      // 进入详情前记录帖子流滚动位置：仅当当前正处于帖子流（非媒体视图、非已在详情），返回时还原到用户原处
      S.feedScrollTop = (S.channelView === 'feed' && !mediaViewEl) ? msgScrollEl.scrollTop : null;
      // 从频道详情页（全屏媒体视图）的“我的帖子”标签点入时：先记录来源标签，返回时可回到此处；再关遮罩避免详情被盖住
      if (mediaViewEl) {
        const onTab = $('#mvFilter .mv-tab.on', mediaViewEl);
        S.mediaReturn = (S.current && S.current.type === 'channel')
          ? { channelId: S.current.id, tab: onTab ? onTab.dataset.mtk : 'all' }
          : null;
        closeMediaView();
      } else {
        S.mediaReturn = null;
      }
      const r = await api('/channel/post/detail', { post_id: +postId });
      if (r.code !== 0 && r.code !== 200) { toast(r.msg || '无法打开帖子'); return; }
      const d = r.data;
      S.channelView = 'detail';
      panelEl.classList.add('post-detail');
      S.openPostRoom = { channel_id: d.channel_id, post_id: d.post_id };
      S.sock && S.sock.emit('join_channel_post', S.openPostRoom);
      const mosaic = mediaMosaicHtml(d.files);
      const others = (d.files || []).filter(f => !isImgFile(f) && !isVidFile(f))
        .map(f => '<div class="list-row">' + fileIcHtml(f.filename) + '<div class="lr-main"><div class="lr-t">' + esc(f.filename) + '</div><div class="lr-s">' + esc(fmtBytes(f.file_size)) + '</div></div><a class="mini-btn blue" href="' + esc(f.file_url) + '" download>下载</a></div>').join('');
      const cmts = (d.comments || []).map(c => commentItemHtml(c)).join('');
      msgListEl.innerHTML =
        '<div class="post-detail-wrap">' +
          '<div class="pd-post">' +
            '<div class="post-head"><span class="post-author">' + esc((S.current && S.current.name) || '') + '</span><span class="post-time">' + esc(fmtRelTime(d.created_at)) + '</span></div>' +
            mosaic +
            (d.title ? '<div class="post-title">' + esc(d.title) + '</div>' : '') +
            '<div class="post-content">' + esc(d.content || '') + '</div>' +
            others +
            '<div class="pd-meta"><span>' + (d.view_count || 0) + ' 次浏览</span><button class="post-fwd" id="pdFwdBtn" title="转发"><i class="ic ic-forward"></i></button></div>' +
          '</div>' +
          ((d.comments && d.comments.length) ? '<div class="pd-comments"><div id="commentList">' + cmts + '</div></div>' : '') +
        '</div>';
      setChannelBottom('comment');
      scrollBottom(false);
      const pfwd = $('#pdFwdBtn'); if (pfwd) pfwd.addEventListener('click', () => openForwardPicker(postMeta(d, d.post_id)));
      if (highlight) { const pd = $('.pd-post', msgListEl); if (pd) { pd.classList.remove('sr-flash'); void pd.offsetWidth; pd.classList.add('sr-flash'); } }
    } catch (e) { toast('打开帖子失败'); }
  }

  async function backToChannelFeed() {
    panelEl.classList.remove('post-detail');
    if (S.openPostRoom) { S.sock && S.sock.emit('leave_channel_post', S.openPostRoom); S.openPostRoom = null; }
    // 从媒体视图（如“我的帖子”标签）进入的详情：返回时重新打开媒体视图并定位到原标签
    if (S.mediaReturn && S.mediaReturn.tab && S.mediaReturn.tab !== 'all') {
      const ret = S.mediaReturn; S.mediaReturn = null;
      if (S.current && S.current.type === 'channel') { S.channelView = 'feed'; await loadChannel(S.current.id); setChannelBottom('feed'); }
      openMediaView('channel', ret.channelId, null, ret.tab);
      return;
    }
    if (S.current && S.current.type === 'channel') {
      S.channelView = 'feed';
      const restore = S.feedScrollTop; S.feedScrollTop = null;
      await loadChannel(S.current.id);
      setChannelBottom('feed');
      // 还原进入详情前的浏览位置（loadChannel 默认滚到最新一帖，这里回到用户原处）
      if (restore != null) { msgScrollEl.scrollTop = restore; requestAnimationFrame(() => { msgScrollEl.scrollTop = restore; }); }
    }
  }

  /* ---------------- 分享/转发目标选择器（频道帖子 / 频道本身共用）：最近会话列表 + 预览卡 + 发送 ---------------- */
  function openSharePicker(opts) {
    // opts: { title, preview:{cover,title,summary,srcLabel}, onSend: async(sel)=>void }
    const targets = [];
    S.convs.forEach(c => { if (c.conv_type === 'private' || c.conv_type === 'group') targets.push(c); });
    targets.sort((a, b) => ((b.updated_at || '') + '').localeCompare((a.updated_at || '') + ''));
    let sel = null; // {kind,id,name}
    const pv = opts.preview || {};
    const previewHtml = '<div class="fwd-pv"><div class="fwd-pv-card">' +
      (pv.cover ? '<img class="fwd-pv-cover" src="' + esc(pv.cover) + '" alt="">' : '') +
      (pv.title ? '<div class="fwd-pv-title">' + esc(pv.title) + '</div>' : '') +
      (pv.summary ? '<div class="fwd-pv-sum">' + esc(pv.summary) + '</div>' : '') +
      '<div class="fwd-src"><i class="ic ' + (pv.icon || 'ic-channel') + '"></i><span>' + esc(pv.srcLabel || '频道') + '</span></div></div></div>';
    const body = '<div class="fwd-sheet">' +
      '<div class="fwd-pane-l"><div class="fwd-search"><i class="ic ic-search"></i><input id="fwdSearch" placeholder="搜索" autocomplete="off"></div>' +
      '<div class="fwd-sec">最近聊天</div><div class="fwd-list" id="fwdList">' + fwdRowsHtml(targets) + '</div></div>' +
      '<div class="fwd-pane-r"><div class="fwd-to" id="fwdTo">发送给</div>' + previewHtml + '</div></div>';
    openModal(opts.title || '转发', body, async () => {
      if (!sel) { toast('请选择一个会话'); return; }
      await opts.onSend(sel);
    }, '发送');
    $('#modal').classList.add('fwd-sheet-modal');
    modalCleanup = () => { const m = $('#modal'); if (m) m.classList.remove('fwd-sheet-modal'); };
    const list = $('#fwdList'), search = $('#fwdSearch'), toEl = $('#fwdTo');
    const bind = () => {
      $$('#modalBody .fwd-row').forEach(r => r.addEventListener('click', () => {
        $$('#modalBody .fwd-row').forEach(x => x.classList.remove('sel'));
        r.classList.add('sel');
        sel = { kind: r.dataset.kind, id: +r.dataset.id, name: r.dataset.name };
        if (toEl) toEl.textContent = '发送给：' + sel.name;
      }));
    };
    if (search) search.addEventListener('input', () => {
      const q = search.value.trim().toLowerCase();
      list.innerHTML = fwdRowsHtml(targets.filter(c => (c.name || '').toLowerCase().includes(q)));
      sel = null; if (toEl) toEl.textContent = '发送给';
      bind();
    });
    bind();
  }
  function openForwardPicker(post) {
    if (!post || !post.post_id) { toast('无法转发该帖子'); return; }
    openSharePicker({
      title: '转发',
      preview: { cover: post.cover, title: post.title, summary: post.summary, srcLabel: post.channel_name || '频道' },
      onSend: async (sel) => {
        const res = await api('/channel/post/forward', { post_id: post.post_id, target_type: sel.kind, target_id: sel.id });
        if (res.code === 0) {
          closeModal(); toast('已转发');
          if (S.current && S.current.type === sel.kind && S.current.id === sel.id) {
            appendMessage({ id: res.data.msg_id, self: true, content: res.data.content, msg_type: 5, created_at: res.data.created_at }, sel.kind);
          }
          bumpConversation(sel.kind, sel.id, '[转发] ' + (post.title || post.summary || post.channel_name || ''));
        } else toast(res.msg || '转发失败');
      }
    });
  }
  // 分享频道本身（与转发帖子同一选择器，仅数据源/预览不同）
  function shareChannel(ch) {
    if (!ch || !ch.id) { toast('无法分享该频道'); return; }
    openSharePicker({
      title: '分享频道',
      preview: { cover: ch.avatar, title: ch.name, summary: ch.description || '', srcLabel: '频道' },
      onSend: async (sel) => {
        const res = await api('/channel/share', { channel_id: ch.id, target_type: sel.kind, target_id: sel.id });
        if (res.code === 0) {
          closeModal(); toast('已分享');
          if (S.current && S.current.type === sel.kind && S.current.id === sel.id) {
            appendMessage({ id: res.data.msg_id, self: true, content: res.data.content, msg_type: 5, created_at: res.data.created_at }, sel.kind);
          }
          bumpConversation(sel.kind, sel.id, '[频道] ' + (ch.name || ''));
        } else toast(res.msg || '分享失败');
      }
    });
  }
  // 转发单条消息（文本/图片/文件/相册）到其他会话，沿用同一转发选择器
  function forwardMessage(el, type) {
    const mid = +el.dataset.id; if (!mid) { toast('无法转发该消息'); return; }
    const mt = +el.dataset.mt || 1; let label;
    if (mt === 2) label = '图片';
    else if (mt === 3) { const fn = $('.file-meta b', el); label = '文件：' + (fn ? fn.textContent : ''); }
    else if (mt === 4) label = '相册';
    else { const t = $('.txt', el); label = t ? t.textContent.slice(0, 50) : '消息'; }
    openSharePicker({
      title: '转发',
      preview: { title: label, srcLabel: '消息', icon: 'ic-msg' },
      onSend: async (sel) => {
        const res = await api('/chat/message/forward', { source_type: type, message_id: mid, target_type: sel.kind, target_id: sel.id });
        if (res.code === 0) {
          closeModal(); toast('已转发');
          if (S.current && S.current.type === sel.kind && S.current.id === sel.id) {
            appendMessage({ id: res.data.msg_id, self: true, content: res.data.content, msg_type: res.data.msg_type, created_at: res.data.created_at, filename: res.data.filename, file_size: res.data.file_size }, sel.kind);
          }
          bumpConversation(sel.kind, sel.id, '[转发] ' + label);
        } else toast(res.msg || '转发失败');
      }
    });
  }
  function fwdRowsHtml(targets) {
    if (!targets.length) return '<div class="fwd-empty">还没有可转发的私聊或群聊会话</div>';
    return targets.map(c => '<div class="fwd-row" data-kind="' + c.conv_type + '" data-id="' + c.target_id + '" data-name="' + esc(c.name) + '">' +
      '<span class="fwd-radio"></span>' +
      '<img class="fwd-ava" src="' + esc(avatarOf(c) || letterAvatar(c.name)) + '" alt="">' +
      '<div class="lr-main"><div class="lr-t">' + esc(c.name) + '</div><div class="lr-s">' + (c.conv_type === 'group' ? '群聊' : '私聊') + '</div></div></div>').join('');
  }
  // 点击转发卡片：已订阅→打开频道并定位该帖；未订阅→只读预览并引导订阅
  async function jumpToChannelPost(channelId, postId, highlight) {
    channelId = +channelId; postId = +postId;
    if (!channelId || !postId) { toast('该转发已失效'); return; }
    const conv = S.convs.get(convKey('channel', channelId));
    if (conv) {
      openConversation(conv);
      await loadChannel(channelId);
      await openPostDetail(postId, highlight);
    } else {
      const r = await api('/channel/post/detail', { post_id: postId });
      if (!r || (r.code !== 0 && r.code !== 200)) { toast((r && r.msg) || '该帖子不可见'); return; }
      openForwardPreview(channelId, postId, r.data);
    }
  }
  // 从通知跳转到频道帖子：先取详情拿到 channel_id，再复用 jumpToChannelPost 打开详情+评论并高亮
  async function jumpToChannelPostNotice(postId) {
    const r = await api('/channel/post/detail', { post_id: postId });
    if (!r || (r.code !== 0 && r.code !== 200)) { toast((r && r.msg) || '该帖子不可见'); return; }
    closeModal();
    await jumpToChannelPost(r.data.channel_id, postId, true);
  }
  function openForwardPreview(channelId, postId, d) {
    const mosaic = mediaMosaicHtml(d.files);
    const body = '<div class="fwd-preview">' + (d.title ? '<div class="post-title">' + esc(d.title) + '</div>' : '') + mosaic +
      '<div class="post-content">' + esc(d.content || '') + '</div></div>' +
      '<div class="modal-hint" style="margin-top:8px">你尚未订阅该频道，订阅后可查看完整帖子流与评论。</div>';
    openModal('频道帖子', body, async () => {
      const r = await api('/channel/join', { channel_id: channelId });
      if (r.code === 0) {
        closeModal(); toast('已订阅频道');
        await reloadConversations();
        const conv = S.convs.get(convKey('channel', channelId));
        if (conv) { openConversation(conv); await loadChannel(channelId); await openPostDetail(postId); }
      } else toast(r.msg || '订阅失败');
    }, '订阅并查看');
  }

  /* 点击“分享频道”卡片：已订阅直接打开会话；未订阅则用卡片自带信息渲染预览并引导订阅后查看 */
  function openSharedChannel(ch) {
    if (!ch || !ch.id) { toast('频道不存在或已失效'); return; }
    const conv = S.convs.get(convKey('channel', ch.id));
    if (conv) { openConversation(conv); return; }
    const body = '<div class="fwd-card" style="cursor:default;margin:0">' +
      '<div class="fwd-body"><div class="fwd-title">' + esc(ch.name || '频道') + '</div>' +
      (ch.desc ? '<div class="fwd-sum">' + esc(ch.desc) + '</div>' : '') +
      '<div class="fwd-src"><i class="ic ic-channel"></i><span>频道</span></div></div>' +
      (ch.avatar ? '<div class="fwd-cover"><img src="' + esc(ch.avatar) + '" alt=""></div>' : '') + '</div>' +
      '<div class="modal-hint" style="margin-top:10px">你尚未订阅该频道，订阅后可查看其帖子流与媒体文件。</div>';
    openModal('分享频道', body, async () => {
      const r = await api('/channel/join', { channel_id: ch.id });
      if (r.code === 0) {
        closeModal(); toast('已订阅频道');
        await reloadConversations();
        const c2 = S.convs.get(convKey('channel', ch.id));
        if (c2) openConversation(c2);
      } else toast(r.msg || '订阅失败');
    }, '订阅并查看');
  }

  /* 右上角"⋮"下拉菜单（私聊）：静音 / 删除聊天记录 / 删除会话（Telegram 风格，从按钮右下弹出）
     - 静音：即时切换，影响来音与会话列表角标（灰点）
     - 删除聊天记录：二次确认后清空我这一侧的全部消息（对方不受影响）
     - 删除会话：从列表移除（与资料页"从列表移除会话"同源，消息保留，来新消息会重现） */
  let chatMoreEl = null;
  function closeChatMore() {
    if (!chatMoreEl) return;
    const el = chatMoreEl; chatMoreEl = null;
    el.classList.remove('show');
    setTimeout(() => el.remove(), 220);
  }
  function openPrivateMore(anchorEl) {
    if (!S.current || S.current.type !== 'private') return;
    if (chatMoreEl) { closeChatMore(); return; }  // 再次点击"⋮"收起
    const uid = S.current.id;
    const muted = S.current.notify === 3;
    const el = document.createElement('div');
    el.className = 'chat-more';
    el.innerHTML =
      '<button class="cm-item" data-cm="mute" type="button"><i class="ic ' + (muted ? 'ic-announce' : 'ic-notify-off') + '"></i><span>' + (muted ? '取消静音' : '静音') + '</span></button>' +
      '<button class="cm-item" data-cm="media" type="button"><i class="ic ic-image"></i><span>媒体文件</span></button>' +
      '<button class="cm-item" data-cm="report" type="button"><i class="ic ic-flag"></i><span>举报</span></button>' +
      '<button class="cm-item" data-cm="clear" type="button"><i class="ic ic-broom"></i><span>删除聊天记录</span></button>' +
      '<button class="cm-item danger" data-cm="del" type="button"><i class="ic ic-trash"></i><span>删除会话</span></button>';
    document.body.appendChild(el);
    const r = (anchorEl || $('#chatInfoBtn')).getBoundingClientRect();
    const w = el.offsetWidth, h = el.offsetHeight;
    el.style.left = Math.max(8, Math.min(r.right - w, window.innerWidth - w - 8)) + 'px';
    el.style.top = (r.bottom + 6) + 'px';
    if (r.bottom + 6 + h > window.innerHeight - 8) el.style.top = Math.max(8, r.top - h - 6) + 'px';  // 下方不够则上翻
    requestAnimationFrame(() => el.classList.add('show'));
    chatMoreEl = el;
    el.addEventListener('click', (e) => {
      const b = e.target.closest('.cm-item'); if (!b) return;
      const act = b.dataset.cm;
      closeChatMore();
      if (act === 'mute') togglePrivateMute(uid);
      else if (act === 'media') openMediaView('private', uid);
      else if (act === 'report') openReportModal('user', uid, dispName(uid, S.current && S.current.name));
      else if (act === 'clear') clearPrivateHistory(uid);
      else if (act === 'del') deletePrivateConversation(uid);
    });
  }
  async function togglePrivateMute(uid) {
    const conv = S.convs.get(convKey('private', uid));
    const cur = (conv && conv.notify_level) || (S.current && S.current.id === uid ? S.current.notify : 1) || 1;
    const next = cur === 3 ? 1 : 3;
    const r = await api('/chat/conversation/notify', { conv_type: 'private', target_id: uid, notify_level: next });
    if (r.code !== 0) { toast(r.msg || '操作失败'); return; }
    if (conv) conv.notify_level = next;
    if (S.current && S.current.type === 'private' && S.current.id === uid) S.current.notify = next;
    reloadConversations();  // 刷新角标：静音→灰点，取消→数字
    toast(next === 3 ? '已静音' : '已取消静音');
  }
  function clearPrivateHistory(uid) {
    openModal('删除聊天记录',
      '<div class="modal-hint" style="margin:0;line-height:1.6">确定清空与 <b>' + esc(dispName(uid, '')) + '</b> 的全部聊天记录？仅删除你这一侧的记录，对方不受影响，且无法恢复。</div>',
      async () => {
        const r = await api('/chat/conversation/history/clear', { to_user_id: uid });
        if (r.code === 0) {
          closeModal();
          if (window.SFX) SFX.delete && SFX.delete();
          toast('聊天记录已清空');
          if (S.current && S.current.type === 'private' && S.current.id === uid) loadPrivate(uid);
          reloadConversations();
        } else toast(r.msg || '清空失败');
      }, '确认清空', true);
  }
  async function deletePrivateConversation(uid) {
    const r = await api('/chat/conversation/delete', { to_user_id: uid });
    if (r.code !== 0) { toast(r.msg || '删除失败'); return; }
    const el = $('[data-key="' + convKey('private', uid) + '"]', convListEl);
    if (el && window.PARTICLES) { SFX.delete && SFX.delete(); PARTICLES.dissolve(el); }
    else if (el) el.remove();
    if (S.current && S.current.type === 'private' && S.current.id === uid) { S.current = null; panelHide(); }
    reloadConversations();
    toast('会话已删除');
  }

  /* 右上角"⋮"下拉菜单（频道）：静音 / 退订频道 / 退订并拉黑（与私聊 ⋮ 同一交互，从按钮右下弹出；
     其余管理操作【编辑/邀请/公告/机器人/分享/拉黑】已内嵌在全屏媒体视图的操作区）
     - 静音：即时切换，影响来音与会话列表角标（灰点）
     - 频道主显示为“删除/删除并拉黑”，订阅者与副频道主显示为“退订/退订并拉黑” */
  function openChannelMore(anchorEl) {
    if (!S.current || S.current.type !== 'channel') return;
    if (chatMoreEl) { closeChatMore(); return; }  // 再次点击"⋮"收起
    const id = S.current.id;
    const muted = S.current.notify === 3;
    const isBoss = S.current.role === 1;
    const el = document.createElement('div');
    el.className = 'chat-more';
    el.innerHTML =
      '<button class="cm-item" data-cm="mute" type="button"><i class="ic ' + (muted ? 'ic-announce' : 'ic-notify-off') + '"></i><span>' + (muted ? '取消静音' : '静音') + '</span></button>' +
      '<button class="cm-item" data-cm="report" type="button"><i class="ic ic-flag"></i><span>举报</span></button>' +
      '<button class="cm-item danger" data-cm="leave" type="button"><i class="ic ic-out"></i><span>' + (isBoss ? '删除频道' : '退订频道') + '</span></button>' +
      '<button class="cm-item danger" data-cm="leaveblock" type="button"><i class="ic ic-ban"></i><span>' + (isBoss ? '删除并拉黑' : '退订并拉黑') + '</span></button>';
    document.body.appendChild(el);
    const r = (anchorEl || $('#chatInfoBtn')).getBoundingClientRect();
    const w = el.offsetWidth, h = el.offsetHeight;
    el.style.left = Math.max(8, Math.min(r.right - w, window.innerWidth - w - 8)) + 'px';
    el.style.top = (r.bottom + 6) + 'px';
    if (r.bottom + 6 + h > window.innerHeight - 8) el.style.top = Math.max(8, r.top - h - 6) + 'px';  // 下方不够则上翻
    requestAnimationFrame(() => el.classList.add('show'));
    chatMoreEl = el;
    el.addEventListener('click', (e) => {
      const b = e.target.closest('.cm-item'); if (!b) return;
      const act = b.dataset.cm;
      closeChatMore();
      if (act === 'mute') setChannelNotify(muted ? 1 : 3);
      else if (act === 'report') openReportModal('channel', id, (S.current && S.current.name) || '');
      else if (act === 'leave') doChannelLeave(id);
      else if (act === 'leaveblock') doChannelLeaveBlock(id);
    });
  }
  function doChannelLeave(id) {
    const isBoss = S.current && S.current.role === 1;
    const name = (S.current && S.current.name) || '';
    openModal(isBoss ? '删除频道' : '退订频道',
      '<div class="modal-hint" style="margin:0;line-height:1.6">' + (isBoss
        ? '删除频道 <b>' + esc(name) + '</b> 将清空所有帖子与订阅关系，且<span style="color:#e53935;font-weight:700">不可恢复</span>，确定继续？'
        : '确定退订频道 <b>' + esc(name) + '</b>？退订后将不再接收其帖子。') + '</div>',
      async () => {
        const r = await api(isBoss ? '/channel/remove_channel' : '/channel/leave', { channel_id: id });
        if (r.code === 0) { closeModal(); toast(isBoss ? '频道已删除' : '已退订频道'); closeChat(); reloadConversations(); }
        else toast(r.msg || '操作失败');
      }, isBoss ? '确认删除' : '确认退订', true);
  }
  async function doChannelLeaveBlock(id) {
    const isBoss = S.current && S.current.role === 1;
    const name = (S.current && S.current.name) || '';
    const r = await api(isBoss ? '/channel/remove_channel' : '/channel/leave', { channel_id: id });
    if (r.code !== 0) { toast(r.msg || '操作失败'); return; }
    closeChat();
    await reloadConversations();
    toast(isBoss ? '频道已删除' : '已退订频道');
    doBlock('channel', id, name);
  }

  /* 右上角"⋮"下拉菜单（群聊）：静音 / 退出群聊 / 退出并拉黑（与私聊、频道同一交互，从按钮右下弹出；
     群资料页【卡片/成员/群文件/管理】改由点击头部进入。群主后端禁止直接退出，故显示为"解散/解散并拉黑"） */
  async function openGroupMore(anchorEl) {
    if (!S.current || S.current.type !== 'group') return;
    if (chatMoreEl) { closeChatMore(); return; }  // 再次点击"⋮"收起
    const id = S.current.id;
    const muted = S.current.notify === 3;
    // 会话列表未带群内角色，首次打开时拉一次成员接口取 my_role 并缓存到 S.current.role
    let role = S.current.role;
    if (!role) {
      const m = await api('/group/' + id + '/members').catch(() => null);
      if (m && m.code === 0) { role = m.data.my_role || 3; S.current.role = role; }
    }
    const isBoss = role === 1;
    const el = document.createElement('div');
    el.className = 'chat-more';
    el.innerHTML =
      '<button class="cm-item" data-cm="mute" type="button"><i class="ic ' + (muted ? 'ic-announce' : 'ic-notify-off') + '"></i><span>' + (muted ? '取消静音' : '静音') + '</span></button>' +
      '<button class="cm-item" data-cm="report" type="button"><i class="ic ic-flag"></i><span>举报</span></button>' +
      '<button class="cm-item danger" data-cm="leave" type="button"><i class="ic ic-out"></i><span>' + (isBoss ? '解散群聊' : '退出群聊') + '</span></button>' +
      '<button class="cm-item danger" data-cm="leaveblock" type="button"><i class="ic ic-ban"></i><span>' + (isBoss ? '解散并拉黑' : '退出并拉黑') + '</span></button>';
    document.body.appendChild(el);
    const r = (anchorEl || $('#chatInfoBtn')).getBoundingClientRect();
    const w = el.offsetWidth, h = el.offsetHeight;
    el.style.left = Math.max(8, Math.min(r.right - w, window.innerWidth - w - 8)) + 'px';
    el.style.top = (r.bottom + 6) + 'px';
    if (r.bottom + 6 + h > window.innerHeight - 8) el.style.top = Math.max(8, r.top - h - 6) + 'px';  // 下方不够则上翻
    requestAnimationFrame(() => el.classList.add('show'));
    chatMoreEl = el;
    el.addEventListener('click', (e) => {
      const b = e.target.closest('.cm-item'); if (!b) return;
      const act = b.dataset.cm;
      closeChatMore();
      if (act === 'mute') toggleGroupMute(id);
      else if (act === 'report') openReportModal('group', id, (S.current && S.current.name) || '');
      else if (act === 'leave') doGroupLeave(id, isBoss);
      else if (act === 'leaveblock') doGroupLeaveBlock(id, isBoss);
    });
  }
  async function toggleGroupMute(id) {
    const conv = S.convs.get(convKey('group', id));
    const cur = (conv && conv.notify_level) || (S.current && S.current.id === id ? S.current.notify : 1) || 1;
    const next = cur === 3 ? 1 : 3;
    const r = await api('/chat/conversation/notify', { conv_type: 'group', target_id: id, notify_level: next });
    if (r.code !== 0) { toast(r.msg || '操作失败'); return; }
    if (conv) conv.notify_level = next;
    if (S.current && S.current.type === 'group' && S.current.id === id) S.current.notify = next;
    reloadConversations();  // 刷新角标：静音→灰点，取消→数字
    toast(next === 3 ? '已静音' : '已取消静音');
  }
  function doGroupLeave(id, isBoss) {
    openModal(isBoss ? '解散群聊' : '退出群聊',
      '<div class="modal-hint" style="margin:0;line-height:1.6">' + (isBoss
        ? '解散后群聊将被永久删除，所有成员、消息、文件都将清除，且<span style="color:#e53935;font-weight:700">不可恢复</span>。'
        : '退出后将不再接收该群消息，需重新被邀请或申请加入才能再次进群。') + '</div>',
      async () => {
        const r = await api(isBoss ? '/group/owner/disband' : '/group/leave', { group_id: id });
        if (r.code === 0) { closeModal(); toast(isBoss ? '群聊已解散' : '已退出群聊'); closeChat(); reloadConversations(); }
        else toast(r.msg || '操作失败');
      }, isBoss ? '确认解散' : '确认退出', true);
  }
  async function doGroupLeaveBlock(id, isBoss) {
    const name = (S.current && S.current.name) || '';
    const r = await api(isBoss ? '/group/owner/disband' : '/group/leave', { group_id: id });
    if (r.code !== 0) { toast(r.msg || '操作失败'); return; }
    closeChat();
    await reloadConversations();
    toast(isBoss ? '群聊已解散' : '已退出群聊');
    doBlock('group', id, name);
  }

  /* 单张图片上传（带缩略图底部进度条）：resolve 成功 URL / 失败 null。XHR 以取真实上传进度 */
  function uploadImageWithProgress(file, thumbEl) {
    return new Promise((resolve) => {
      const fd = new FormData(); fd.append('file', file);
      let bar = thumbEl && $('.rp-prog', thumbEl);
      if (thumbEl && !bar) {
        bar = document.createElement('div'); bar.className = 'rp-prog';
        const fl = document.createElement('div'); fl.className = 'rp-prog__fill'; bar.appendChild(fl);
        thumbEl.appendChild(bar);
      }
      const fill = bar && bar.firstChild;
      const xhr = new XMLHttpRequest();
      xhr.open('POST', '/upload/image', true); xhr.withCredentials = true;
      xhr.upload.onprogress = (e) => { if (e.lengthComputable && fill) fill.style.width = (e.loaded / e.total * 100).toFixed(0) + '%'; };
      xhr.onload = () => {
        let r = null; try { r = JSON.parse(xhr.responseText); } catch (e) {}
        if (fill) fill.style.width = '100%';
        resolve((r && r.code === 0) ? r.data.avatar_url : null);
      };
      xhr.onerror = () => resolve(null);
      xhr.send(fd);
    });
  }

  /* ---------------- 举报弹窗（私聊用户 / 群聊 / 频道 ⋮ 菜单入口）----------------
     理由必填，最多附 9 张截图；提交前才把本地图片上传到 /upload/image，成功 URLs 随 POST /report/create 落库 */
  function openReportModal(type, id, name) {
    const TT = { user: '用户', group: '群聊', channel: '频道' };
    const MAX = 9;
    const picked = [];  // { file, obj }
    const body =
      '<div class="modal-hint" style="margin:0 0 10px;line-height:1.6">举报' + (TT[type] || '') + ' <b>' + esc(name || ('#' + id)) + '</b>，请说明违规原因（必填），最多可附 ' + MAX + ' 张截图。</div>' +
      '<textarea id="rpReason" class="modal-input" rows="4" placeholder="请详细描述违规内容…" style="resize:vertical"></textarea>' +
      '<div class="rp-thumbs" id="rpThumbs"></div>' +
      '<button type="button" class="mini-btn" id="rpAdd" style="margin-top:8px">＋ 添加图片</button>' +
      '<input type="file" id="rpFile" accept="image/*" multiple hidden>';
    openModal('举报', body, async () => {
      const reason = $('#rpReason').value.trim();
      if (!reason) { toast('请填写举报理由'); return; }
      const images = [];
      for (let i = 0; i < picked.length; i++) {
        const p = picked[i];
        const thumb = thumbs.children[i];
        const url = await uploadImageWithProgress(p.file, thumb);
        if (!url) { toast('图片上传失败，请重试'); return; }
        images.push(url);
      }
      const res = await api('/report/create', { target_type: type, target_id: id, reason, images });
      if (res && (res.code === 0 || res.code === 200)) { closeModal(); toast(res.msg || '举报已提交'); }
      else toast((res && res.msg) || '举报失败');
    }, '提交举报', true);
    const thumbs = $('#rpThumbs'), fileInput = $('#rpFile'), addBtn = $('#rpAdd');
    function render() {
      thumbs.innerHTML = picked.map((p, i) =>
        '<div class="rp-thumb"><img src="' + p.obj + '" alt=""><button type="button" class="rp-rm" data-i="' + i + '">✕</button></div>').join('');
      addBtn.style.display = picked.length >= MAX ? 'none' : '';
    }
    thumbs.addEventListener('click', (e) => {
      const rm = e.target.closest('.rp-rm'); if (!rm) return;
      const i = +rm.dataset.i; if (picked[i]) URL.revokeObjectURL(picked[i].obj);
      picked.splice(i, 1); render();
    });
    addBtn.addEventListener('click', () => fileInput.click());
    fileInput.addEventListener('change', () => {
      Array.from(fileInput.files || []).forEach(f => {
        if (picked.length >= MAX) return;
        picked.push({ file: f, obj: URL.createObjectURL(f) });
      });
      fileInput.value = ''; render();
    });
    render();
    modalCleanup = () => { picked.forEach(p => { try { URL.revokeObjectURL(p.obj); } catch (e) {} }); };
  }

  /* ---------------- 我的想法（抽屉入口）：文字 + 最多 9 张配图 → POST /feedback/submit
     图片逐张上传 /feedback/upload 后才随正文提交（与举报同款范式，避免选了不提交的图污服务器）；
     弹窗下半区展示“我的提交”历史与管理员回复；有新回复时抽屉项显红点（本地记录已读到的回复时间） ---------------- */
  const FB_CATS = [['idea', '想法'], ['suggestion', '建议'], ['bug', '问题'], ['other', '其他']];
  const FB_ST = { 0: '待处理', 1: '已回复', 2: '已归档' };
  const FB_SEEN_KEY = 'fb_seen_reply_at';
  function fbCatLabel(c) { const hit = FB_CATS.filter(x => x[0] === c)[0]; return hit ? hit[1] : '想法'; }

  async function refreshFbBadge() {
    try {
      const r = await api('/feedback/mine?limit=50');
      if (!r || r.code !== 0) return;
      const seen = localStorage.getItem(FB_SEEN_KEY) || '';
      const n = (r.data.feedbacks || []).filter(x => x.status === 1 && x.replied_at && String(x.replied_at) > seen).length;
      const b = $('#drawerFbBadge');
      if (b) { b.hidden = n <= 0; b.textContent = n > 99 ? '99+' : n; }
    } catch (e) {}
  }

  function openFeedbackModal() {
    const MAX = 9;
    const picked = [];   // { file, obj }
    let cat = 'idea';
    const body =
      '<div class="modal-hint" style="margin:0 0 10px;line-height:1.6">把你的想法、建议或遇到的问题告诉管理员，可附最多 ' + MAX + ' 张图片。管理员在后台接收后可以直接回复你。</div>' +
      '<div class="fb-cats" id="fbCats">' + FB_CATS.map(c => '<button type="button" class="fb-cat' + (c[0] === cat ? ' on' : '') + '" data-c="' + c[0] + '">' + c[1] + '</button>').join('') + '</div>' +
      '<textarea id="fbText" class="modal-input" rows="4" placeholder="例如：希望动态支持转发到群聊…" style="resize:vertical"></textarea>' +
      '<div class="rp-thumbs" id="fbThumbs"></div>' +
      '<button type="button" class="mini-btn blue" id="fbAdd" style="margin-top:8px">＋ 添加图片</button>' +
      '<input type="file" id="fbFile" accept="image/*" multiple hidden>' +
      '<div class="sec-title" style="padding-left:0">我的提交 · 管理员回复</div>' +
      '<div class="fb-list" id="fbList"><div class="loading-hint">加载中…</div></div>';
    openModal('我的想法 · 写给管理员', body, async () => {
      const content = $('#fbText').value.trim();
      if (!content) { toast('请先写下你的想法'); return; }
      const images = [];
      for (const p of picked) {
        const fd = new FormData(); fd.append('file', p.file);
        let r;
        try { const res = await fetch('/feedback/upload', { method: 'POST', body: fd, credentials: 'same-origin' }); r = await res.json(); }
        catch (e) { toast('图片上传失败，请重试'); return; }
        if (!r || r.code !== 0) { toast((r && r.msg) || '图片上传失败'); return; }
        images.push(r.data.url);
      }
      const res = await api('/feedback/submit', { category: cat, content, images });
      if (res && (res.code === 0 || res.code === 200)) { closeModal(); toast(res.msg || '已收到，感谢你的想法'); }
      else toast((res && res.msg) || '提交失败');
    }, '提交给管理员');
    const thumbs = $('#fbThumbs'), fileInput = $('#fbFile'), addBtn = $('#fbAdd'), listBox = $('#fbList');
    function render() {
      thumbs.innerHTML = picked.map((p, i) =>
        '<div class="rp-thumb"><img src="' + p.obj + '" alt=""><button type="button" class="rp-rm" data-i="' + i + '">✕</button></div>').join('');
      addBtn.style.display = picked.length >= MAX ? 'none' : '';
    }
    thumbs.addEventListener('click', (e) => {
      const rm = e.target.closest('.rp-rm'); if (!rm) return;
      const i = +rm.dataset.i; if (picked[i]) URL.revokeObjectURL(picked[i].obj);
      picked.splice(i, 1); render();
    });
    addBtn.addEventListener('click', () => fileInput.click());
    fileInput.addEventListener('change', () => {
      Array.from(fileInput.files || []).forEach(f => {
        if (picked.length >= MAX) return;
        picked.push({ file: f, obj: URL.createObjectURL(f) });
      });
      fileInput.value = ''; render();
    });
    $('#fbCats').addEventListener('click', (e) => {
      const b = e.target.closest('.fb-cat'); if (!b) return;
      cat = b.dataset.c;
      $$('#modalBody .fb-cat').forEach(x => x.classList.toggle('on', x === b));
    });
    // 历史配图点开大图：从已渲染的 DOM 收集图集，支持左右翻页（列表里是缩略图，大图走原图）
    listBox.addEventListener('click', (e) => {
      const im = e.target.closest('.fb-imgs img'); if (!im) return;
      const urls = [];
      $$('#fbList .fb-imgs img').forEach(x => urls.push(fullSrc(x)));
      const i = urls.indexOf(fullSrc(im));
      openAvatarViewer(fullSrc(im), im.getBoundingClientRect(), false, '', urls, i < 0 ? 0 : i);
    });
    render();
    loadFeedbackMine();
    setTimeout(() => { const ta = $('#fbText'); if (ta) ta.focus(); }, 320);
    modalCleanup = () => { picked.forEach(p => { try { URL.revokeObjectURL(p.obj); } catch (e) {} }); };
  }

  async function loadFeedbackMine() {
    const box = $('#fbList'); if (!box) return;
    let r;
    try { r = await api('/feedback/mine?limit=30'); } catch (e) { r = null; }
    if (!box.isConnected) return;
    if (!r || r.code !== 0) { box.innerHTML = '<div class="empty-hint">历史加载失败</div>'; return; }
    const rows = (r.data && r.data.feedbacks) || [];
    if (!rows.length) { box.innerHTML = '<div class="empty-hint">还没有提交过想法，写下第一条吧</div>'; return; }
    box.innerHTML = rows.map(x => {
      const st = x.status == null ? 0 : x.status;
      const imgs = (x.images || []).map(u => mediaImgHtml(u, 400, '')).join('');
      return '<div class="fb-item">' +
        '<div class="fb-hd"><span class="fb-tag">' + fbCatLabel(x.category) + '</span><span>' + esc(fmtRelTime(x.created_at) || '') + '</span>' +
        '<span class="fb-st s' + st + '">' + (FB_ST[st] || '待处理') + '</span></div>' +
        '<div class="fb-c">' + esc(x.content) + '</div>' +
        (imgs ? '<div class="fb-imgs">' + imgs + '</div>' : '') +
        (x.reply ? '<div class="fb-reply"><b>管理员回复' + (x.replied_at ? ' · ' + esc(fmtRelTime(x.replied_at)) : '') + '</b>' + esc(x.reply) + '</div>' : '') +
        '</div>';
    }).join('');
    // 打开即视为读完当前回复：记录最新回复时间并清角标
    let latest = '';
    rows.forEach(x => { if (x.status === 1 && x.replied_at && String(x.replied_at) > latest) latest = String(x.replied_at); });
    if (latest) { try { localStorage.setItem(FB_SEEN_KEY, latest); } catch (e) {} refreshFbBadge(); }
  }

  /* ---------------- 共享媒体画廊（私聊 / 频道通用）：等大方格 + 分类筛选（全部 / 图片·视频 / 文件） ---------------- */
  function mediaCat(it) { return (isImgFile(it) || isVidFile(it)) ? 'media' : 'file'; }
  function mediaCellHtml(it) {
    const url = esc(it.file_url);
    if (isImgFile(it)) return '<div class="mv-cell" data-img="' + url + '">' + mediaImgHtml(it.file_url, 400, '') + '</div>';
    if (isVidFile(it)) return '<div class="mv-cell" data-vid="' + url + '"><video src="' + url + '" preload="metadata" muted playsinline></video><span class="mv-play"><svg viewBox="0 0 24 24" width="26" height="26"><path d="M8 5v14l11-7z" fill="#fff"/></svg></span></div>';
    const full = it.filename || nameFromUrl(it.file_url);
    return '<a class="mv-cell mv-file" href="' + url + '" download="' + esc(full) + '" title="' + esc(full) + '"><span class="file-ic svg-ic"><img src="' + fileIconUrl(full) + '" alt="" draggable="false"></span><span class="mv-fname">' + esc(middleEllipsis(full, 12)) + '</span></a>';
  }
  function mediaFilterHtml(active) {
    const t = (k, label) => '<button class="mv-tab' + (active === k ? ' on' : '') + '" data-mtf="' + k + '" type="button">' + label + '</button>';
    return t('all', '全部') + t('media', '图片·视频') + t('file', '文件');
  }
  // 在给定网格容器内挂载画廊：fetchPage(beforeId) 拉取一页；opts: {isActive, scrollEl, scrollToBottom, keepScroll, moreBtn}
  function mountMediaGallery(bodyEl, filterEl, fetchPage, opts) {
    opts = opts || {};
    const st = { items: [], oldestId: null, hasMore: false, loading: false, filter: 'all' };
    const active = () => !opts.isActive || opts.isActive();
    const sc = () => opts.scrollEl || bodyEl;
    function render() {
      const list = st.items.filter(it => st.filter === 'all' || mediaCat(it) === st.filter);
      bodyEl.innerHTML = list.length ? list.map(mediaCellHtml).join('')
        : '<div class="empty-hint">' + (st.items.length ? '该分类下暂无内容' : '还没有共享的媒体文件') + '</div>';
      if (opts.moreBtn) opts.moreBtn.style.display = st.hasMore ? '' : 'none';
    }
    function renderFilter() { if (filterEl) filterEl.innerHTML = mediaFilterHtml(st.filter); }
    async function loadInitial() {
      bodyEl.innerHTML = '<div class="loading-hint">加载中…</div>';
      const r = await fetchPage(null).catch(() => null);
      if (!active()) return;
      if (!r || r.code !== 0) { bodyEl.innerHTML = '<div class="empty-hint">加载失败</div>'; return; }
      st.items = (r.data.media || []).slice();
      st.oldestId = r.data.oldest_id; st.hasMore = r.data.has_more;
      renderFilter(); render();
      if (opts.scrollToBottom) sc().scrollTop = sc().scrollHeight;  // 最新在最下，打开先滚到底
    }
    async function loadOlder() {
      if (!st.hasMore || st.loading || st.oldestId == null) return;
      st.loading = true;
      const r = await fetchPage(st.oldestId).catch(() => null);
      st.loading = false;
      if (!active() || !r || r.code !== 0) return;
      const el = sc(); const prevH = el.scrollHeight;
      st.items = (r.data.media || []).concat(st.items);
      st.oldestId = r.data.oldest_id; st.hasMore = r.data.has_more;
      render();
      if (opts.keepScroll) el.scrollTop = el.scrollHeight - prevH;  // 向上加载后保持视口位置
    }
    if (filterEl) filterEl.addEventListener('click', (e) => {
      const b = e.target.closest('[data-mtf]'); if (!b) return;
      st.filter = b.dataset.mtf; renderFilter(); render();
    });
    if (opts.moreBtn) opts.moreBtn.addEventListener('click', loadOlder);
    bodyEl.addEventListener('click', (e) => {
      const img = e.target.closest('[data-img]');
      if (img) {
        const urls = st.items.filter(isImgFile).map(x => x.file_url);
        const idx = urls.indexOf(img.dataset.img);
        openAvatarViewer(img.dataset.img, $('img', img).getBoundingClientRect(), false, '', urls, idx < 0 ? 0 : idx);
        return;
      }
      const vid = e.target.closest('[data-vid]');
      if (vid) openVideoViewer(vid.dataset.vid);
    });
    loadInitial();
    return { loadOlder, setFilter: (k) => { st.filter = k; render(); } };
  }

  /* 私聊 / 频道 共享媒体全屏画廊：kind='private'(id=对方uid) 或 'channel'(id=频道id，顶部带频道信息卡) */
  let mediaViewEl = null;
  function closeMediaView() {
    if (!mediaViewEl) return;
    const el = mediaViewEl; mediaViewEl = null;
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    // 收起：与私聊资料页 closeProfileView 完全一致——清掉光圈残留，整页向右平移移出
    if (!reduce && el.animate) {
      el.style.clipPath = 'none';
      const anim = el.animate(
        [{ transform: 'translateX(0)' }, { transform: 'translateX(100%)' }],
        { duration: 340, easing: 'cubic-bezier(.4,0,.2,1)', fill: 'forwards' });
      anim.onfinish = anim.oncancel = () => el.remove();
    } else { el.remove(); }
  }
  async function openMediaView(kind, id, origin, initialTab) {
    kind = kind || 'private';
    const key = kind + ':' + id;
    if (mediaViewEl && mediaViewEl.dataset.key === key) return;
    if (mediaViewEl) { mediaViewEl.remove(); mediaViewEl = null; }
    const overlay = document.createElement('div');
    overlay.className = 'media-view';
    overlay.dataset.key = key;
    overlay.innerHTML =
      '<div class="mv-bar"><button class="pv-back" id="mvBack" type="button"><span class="chev"></span></button>' +
      '<div class="mv-title" id="mvTitle">' + (kind === 'channel' ? '' : '媒体文件') + '</div></div>' +
      (kind === 'channel' ? '<div class="mv-hero" id="mvHero"><div class="loading-hint">加载中…</div></div><div class="mv-acts" id="mvActs"></div>' : '') +
      '<div class="mv-filter" id="mvFilter"></div>' +
      '<div class="mv-body" id="mvBody"><div class="media-grid" id="mvMediaWrap"></div><div class="mv-alt" id="mvAltWrap" hidden></div></div>';
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const canAnim = !reduce && !!overlay.animate;
    // 传入 origin（点击会话头部）时：套用与私聊资料页 openProfileView 完全相同的圆形“光圈”揭示入场；否则右侧滑入
    if (origin && canAnim) {
      const cx = origin.x != null ? origin.x : Math.round(window.innerWidth / 2);
      const cy = origin.y != null ? origin.y : 60;
      const radius = Math.hypot(Math.max(cx, window.innerWidth - cx), Math.max(cy, window.innerHeight - cy)) + 20;
      overlay.style.clipPath = 'circle(0px at ' + cx + 'px ' + cy + 'px)';  // 先藏，避免首帧闪现
      document.body.appendChild(overlay);
      mediaViewEl = overlay;
      const anim = overlay.animate(
        [{ clipPath: 'circle(0px at ' + cx + 'px ' + cy + 'px)' },
         { clipPath: 'circle(' + radius + 'px at ' + cx + 'px ' + cy + 'px)' }],
        { duration: 520, easing: 'cubic-bezier(.32,.72,.24,1)', fill: 'forwards' });
      anim.onfinish = () => { overlay.style.clipPath = 'none'; };
    } else {
      document.body.appendChild(overlay);
      mediaViewEl = overlay;
      if (canAnim) overlay.animate([{ transform: 'translateX(100%)' }, { transform: 'translateX(0)' }], { duration: 320, easing: 'cubic-bezier(.4,0,.2,1)' });
    }
    $('#mvBack').addEventListener('click', closeMediaView);
    const body = $('#mvBody', overlay);
    const mediaWrap = $('#mvMediaWrap', overlay);
    const altWrap = $('#mvAltWrap', overlay);
    const fetchPage = kind === 'channel'
      ? (beforeId) => api('/channel/' + id + '/media?per_page=80' + (beforeId ? '&before_id=' + beforeId : ''))
      : (beforeId) => api('/chat/conversation/media?to_user_id=' + id + '&per_page=80' + (beforeId ? '&before_id=' + beforeId : ''));
    // 画廊只负责媒体网格（筛选栏改由下方标签栏统一管理，故 filterEl 传 null）
    const gallery = mountMediaGallery(mediaWrap, null, fetchPage,
      { isActive: () => mediaViewEl === overlay, scrollToBottom: true, keepScroll: true, scrollEl: body });
    body.addEventListener('scroll', () => { if (body.scrollTop < 140) gallery.loadOlder(); });
    // 顶部标签栏：媒体类（全部/图片·视频/文件）+ 频道专属视图类（成员/我的帖子）
    const isChannel = kind === 'channel';
    const tabs = [['all', '全部'], ['media', '图片·视频'], ['file', '文件']]
      .concat(isChannel ? [['members', '成员'], ['mine', '我的帖子']] : []);
    let curTab = 'all';
    const filterBar = $('#mvFilter', overlay);
    const renderTabs = () => {
      filterBar.innerHTML = tabs.map(tk =>
        '<button class="mv-tab' + (curTab === tk[0] ? ' on' : '') + '" data-mtk="' + tk[0] + '" type="button">' + tk[1] + '</button>').join('');
    };
    const selectTab = (k) => {
      if (!tabs.some(t => t[0] === k)) k = 'all';
      curTab = k; renderTabs();
      if (k === 'members' || k === 'mine') {
        mediaWrap.hidden = true; altWrap.hidden = false;
        if (k === 'members') renderChannelMembers(altWrap, id); else renderMyPosts(altWrap, id);
      } else {
        altWrap.hidden = true; mediaWrap.hidden = false; gallery.setFilter(k);
      }
    };
    renderTabs();
    filterBar.addEventListener('click', (e) => {
      const b = e.target.closest('[data-mtk]'); if (!b) return;
      const k = b.dataset.mtk; if (k === curTab) return;
      selectTab(k);
    });
    // 从帖子详情“返回”时回到进入前的标签（如“我的帖子”）
    if (initialTab && initialTab !== 'all') selectTab(initialTab);
    // 频道：拉取频道基础信息渲染头部卡（头像 / 简介 / 订阅数）+ 操作区，并把标题换成频道名
    if (kind === 'channel') {
      const hero = $('#mvHero', overlay);
      const acts = $('#mvActs', overlay);
      api('/channel/' + id + '/members').then(r => {
        if (!hero || !hero.isConnected) return;
        if (!r || r.code !== 0) { hero.remove(); if (acts && acts.isConnected) acts.remove(); return; }
        const d = r.data;
        const myRole = d.my_role || 3;
        const canManage = myRole === 1 || myRole === 2;
        hero.innerHTML =
          '<img class="mv-hero-avatar" src="' + esc(d.avatar || letterAvatar(d.name)) + '" alt="">' +
          '<div class="mv-hero-meta"><div class="mv-hero-name">' + esc(d.name || '') + '</div>' +
          (d.description ? '<div class="mv-hero-desc">' + esc(d.description) + '</div>' : '') +
          '<div class="mv-hero-num">' + (d.total || 0) + ' 位订阅者</div></div>';
        // 操作区：管理项（编辑/邀请订阅/发布公告/机器人）+ 分享频道 + 拉黑；退订/删除已移至右上角 ⋮ 菜单
        if (acts) {
          acts.innerHTML =
            (canManage ? '<button class="mini-btn blue" data-mvedit type="button">编辑</button>' +
              '<button class="mini-btn blue" data-mvinvite type="button">＋ 邀请订阅</button>' +
              '<button class="mini-btn blue" data-mvannounce type="button">发布公告</button>' +
              '<button class="mini-btn blue" data-mvbots type="button">机器人</button>' : '') +
            '<button class="mini-btn blue" data-mvshare type="button">分享频道</button>' +
            '<button class="mini-btn danger-red" data-mvblock type="button">' + (isBlocked('channel', id) ? '取消拉黑' : '拉黑该频道') + '</button>' +
            // 仅频道主可见：新加入成员默认角色开关（默认开，可自行关闭）
            (myRole === 1 ? '<label class="mv-switch-row"><span>新成员默认副频道主</span><div class="switch' + (d.default_vice_owner == 1 ? ' on' : '') + '" data-mvdv type="button" data-on="' + (d.default_vice_owner == 1 ? 1 : 0) + '"></div></label>' : '');
          const eb = $('[data-mvedit]', acts);
          if (eb) eb.addEventListener('click', () => {
            openInfoEditor({
              title: '编辑频道信息', avatar: d.avatar, name: d.name, desc: d.description || '', nameLabel: '频道名称',
              onSave: async ({ name, description, avatarUrl }) => {
                const payload = { name, description };
                if (avatarUrl) payload.avatar_url = avatarUrl;
                const rr = await api('/channel/info?channel_id=' + id, payload, 'PUT');
                if (rr.code === 0) {
                  closeModal(); toast('已保存');
                  d.name = rr.data.name; d.description = rr.data.description || '';
                  if (rr.data.avatar) d.avatar = rr.data.avatar;
                  const hn = $('.mv-hero-name', hero); if (hn) hn.textContent = d.name;
                  const hd = $('.mv-hero-desc', hero); if (hd && d.description) hd.textContent = d.description;
                  const av = $('.mv-hero-avatar', hero); if (av && rr.data.avatar) av.src = rr.data.avatar;
                  if (S.current && S.current.type === 'channel' && S.current.id === id) {
                    S.current.name = rr.data.name;
                    if (rr.data.avatar) S.current.avatar = rr.data.avatar;
                    $('#chatTitle').textContent = rr.data.name;
                    if (rr.data.avatar) { $('#chatAvatar').src = rr.data.avatar; watchAvatar($('#chatAvatar'), rr.data.name); }
                  }
                  reloadConversations();
                } else toast(rr.msg || '保存失败');
              }
            });
          });
          const iv = $('[data-mvinvite]', acts); if (iv) iv.addEventListener('click', openChannelInvite);
          const an = $('[data-mvannounce]', acts); if (an) an.addEventListener('click', openAnnouncePublish);
          const bo = $('[data-mvbots]', acts); if (bo) bo.addEventListener('click', () => openBotPanel('channel', id));
          const sh = $('[data-mvshare]', acts); if (sh) sh.addEventListener('click', () => shareChannel({ id, name: d.name, avatar: d.avatar, description: d.description }));
          const bk = $('[data-mvblock]', acts);
          if (bk) bk.addEventListener('click', () => {
            if (isBlocked('channel', id)) { doUnblock('channel', id, () => { bk.textContent = '拉黑该频道'; }); }
            else { doBlock('channel', id, d.name, () => { closeMediaView(); }); }
          });
          const dv = $('[data-mvdv]', acts);
          if (dv) dv.addEventListener('click', async () => {
            const turnOn = dv.dataset.on !== '1';
            const rr = await api('/channel/default_vice/set', { channel_id: id, enabled: turnOn ? 1 : 0 });
            if (rr.code === 0) { dv.classList.toggle('on', turnOn); dv.dataset.on = turnOn ? '1' : '0'; toast(turnOn ? '新成员将默认为副频道主' : '新成员将默认为订阅者'); }
            else toast(rr.msg || '设置失败');
          });
        }
      }).catch(() => { if (hero && hero.isConnected) hero.remove(); if (acts && acts.isConnected) acts.remove(); });
    }
  }
  /* 频道详情页“成员”标签：成员列表 + 频道主右击升/降副频道主 */
  async function renderChannelMembers(altWrap, channelId) {
    altWrap.innerHTML = '<div class="loading-hint">加载中…</div>';
    const r = await api('/channel/' + channelId + '/members').catch(() => null);
    if (!altWrap.isConnected) return;
    if (!r || r.code !== 0) { altWrap.innerHTML = '<div class="empty-hint">加载失败</div>'; return; }
    const myRole = r.data.my_role || 3;
    const list = r.data.members || [];
    altWrap.innerHTML = '<div class="mv-members">' + (list.length ? list.map(m =>
      '<div class="list-row mv-member" data-uid="' + m.user_id + '" data-role="' + m.role + '">' +
        '<img src="' + esc(m.avatar_url || letterAvatar(m.nickname)) + '" alt="">' +
        '<div class="lr-main"><div class="lr-t">' + esc(dispName(m.user_id, m.nickname)) + '</div>' +
          '<div class="lr-s">' + esc(m.role_name) + '</div></div>' +
        '<span class="role-tag">' + esc(m.role_name) + '</span>' +
      '</div>').join('') : '<div class="empty-hint">暂无成员</div>') + '</div>';
    // 右击成员行：仅频道主可管理，且不能对频道主/自己操作
    altWrap.addEventListener('contextmenu', (e) => {
      const row = e.target.closest('.mv-member'); if (!row) return;
      if (myRole !== 1) return;
      const uid = +row.dataset.uid, role = +row.dataset.role;
      if (role === 1 || uid === S.me.id) return;
      e.preventDefault();
      const items = role === 2
        ? [{ label: '取消副频道主', fn: () => setChannelRole(channelId, uid, 'demote', altWrap) }]
        : [{ label: '设为副频道主', fn: () => setChannelRole(channelId, uid, 'promote', altWrap) }];
      openCtxMenu(e.clientX, e.clientY, items);
    });
  }
  async function setChannelRole(channelId, uid, action, altWrap) {
    const r = await api('/channel/permission/set', { channel_id: channelId, target_user_id: uid, action });
    if (r.code === 0) { toast(r.msg || '已更新'); if (altWrap && altWrap.isConnected) renderChannelMembers(altWrap, channelId); }
    else toast(r.msg || '操作失败');
  }

  /* 频道详情页“我的帖子”标签：仅展示本人发布的帖子 */
  async function renderMyPosts(altWrap, channelId) {
    altWrap.innerHTML = '<div class="loading-hint">加载中…</div>';
    const r = await api('/channel/post/get', { channel_id: channelId }).catch(() => null);
    if (!altWrap.isConnected) return;
    if (!r || r.code !== 0) { altWrap.innerHTML = '<div class="empty-hint">加载失败</div>'; return; }
    const files = (r.data && r.data.channel_file) || [];
    const mine = ((r.data && r.data.channel_post) || []).filter(p => p.author_id === S.me.id);
    if (!mine.length) { altWrap.innerHTML = '<div class="empty-hint">你还没有发布过帖子</div>'; return; }
    mine.sort((a, b) => b.post_id - a.post_id);
    altWrap.innerHTML = '<div class="mv-mine"></div>';
    const box = $('.mv-mine', altWrap);
    mine.forEach(p => { p.files = files.filter(f => f.post_id === p.post_id); box.appendChild(buildPostCard(p)); });
    // 媒体视图内不走 msgListEl 的委托，需就地绑定图片/视频点击放大
    box.addEventListener('click', mediaZoomDelegate);
  }

  // 媒体视图（遮罩层）内统一的图片/视频点击放大委托（“我的帖子”列表与评论弹窗共用）
  function mediaZoomDelegate(e) {
    const cell = e.target.closest('.tg-cell'); if (!cell) return;
    e.stopPropagation();
    if (cell.dataset.tgimg) {
      const img = cell.querySelector('img'); if (!img) return;
      const group = Array.from(cell.parentElement.querySelectorAll('.tg-cell[data-tgimg]'));
      // 拼贴格上的 data-tgimg 与容器上的 data-tgall 都是原始地址（被“+N”隐掉的几张也在里面）
      openAvatarViewer(cell.dataset.tgimg, img.getBoundingClientRect(), false, '', tgGallery(cell), group.indexOf(cell));
    } else if (cell.dataset.tgvid) { openVideoViewer(cell.dataset.tgvid); }
  }

  /* 频道详情页“我的帖子”内点击评论：弹出“帖子 + 评论”弹窗（不跳转、不就地展开）
     底部评论框可直接发评论，他人评论经 receive_comment 实时补入；右上角“删除帖子”按角色显示，关闭弹窗即回到“我的帖子”列表 */
  async function openPostCommentModal(postId) {
    const channelId = S.current ? S.current.id : null;
    let r;
    try { r = await api('/channel/post/detail', { post_id: +postId }); }
    catch (e) { toast('加载失败'); return; }
    if (r.code !== 0 && r.code !== 200) { toast(r.msg || '无法打开帖子'); return; }
    const d = r.data;
    const myRole = S.current ? S.current.role : null;
    const author = d.author_id === S.me.id;
    const canDelete = myRole === 1 || (author && myRole !== 3);  // 频道主删任意；副频道主/未知角色但为作者则可删
    const mosaic = mediaMosaicHtml(d.files);
    const others = (d.files || []).filter(f => !isImgFile(f) && !isVidFile(f))
      .map(f => '<div class="list-row">' + fileIcHtml(f.filename) + '<div class="lr-main"><div class="lr-t">' + esc(f.filename) + '</div><div class="lr-s">' + esc(fmtBytes(f.file_size)) + '</div></div><a class="mini-btn blue" href="' + esc(f.file_url) + '" download>下载</a></div>').join('');
    const cmts = (d.comments || []).map(c => commentItemHtml(c)).join('');
    const bodyHtml =
      '<div class="pc-modal">' +
        '<div class="pc-modal-scroll">' +
          (canDelete ? '<div class="pc-modal-tools"><div class="pc-del-wrap"></div></div>' : '') +
          '<div class="post-detail-wrap">' +
            '<div class="pd-post">' +
              '<div class="post-head"><span class="post-author">' + esc((S.current && S.current.name) || '') + '</span><span class="post-time">' + esc(fmtRelTime(d.created_at)) + '</span></div>' +
              mosaic +
              (d.title ? '<div class="post-title">' + esc(d.title) + '</div>' : '') +
              '<div class="post-content">' + esc(d.content || '') + '</div>' +
              others +
              '<div class="pd-meta"><span><span id="pdCommentCount">' + ((d.comments && d.comments.length) || 0) + '</span> 条评论</span><span>' + (d.view_count || 0) + ' 次浏览</span></div>' +
            '</div>' +
            '<div class="pd-comments"><div id="commentList">' + cmts + '</div></div>' +
          '</div>' +
        '</div>' +
        '<div class="pc-modal-foot"><div class="mv-detail-input"><input id="pcCmtInput" placeholder="发布评论…" autocomplete="off"><button class="mini-btn blue" id="pcCmtSend" type="button">发送</button></div></div>' +
      '</div>';
    openModal('帖子详情', bodyHtml, null);
    const modalEl = $('#modal');
    modalEl.classList.add('post-modal');
    modalCleanup = () => { modalEl.classList.remove('post-modal'); };
    // 加入帖子房间：新评论经 receive_comment 实时补到 #commentList（ensureCommentsContainer 优先命中弹窗）
    S.openPostRoom = { channel_id: d.channel_id, post_id: d.post_id };
    S.sock && S.sock.emit('join_channel_post', S.openPostRoom);
    const scroll = $('.pc-modal-scroll', modalEl);
    if (scroll) scroll.addEventListener('click', mediaZoomDelegate);
    // 删除：就地二次确认（复用单弹窗，避免再叠一层）
    const delWrap = $('.pc-del-wrap', modalEl);
    if (delWrap) {
      const paintBtn = () => {
        delWrap.innerHTML = '<button class="mv-detail-del danger-red" type="button">删除帖子</button>';
        $('button', delWrap).addEventListener('click', askDel);
      };
      const askDel = () => {
        delWrap.innerHTML = '<span class="pc-del-q">确定删除？</span><button class="pc-del-no" type="button">取消</button><button class="pc-del-yes danger-red" type="button">删除</button>';
        $('.pc-del-no', delWrap).addEventListener('click', paintBtn);
        $('.pc-del-yes', delWrap).addEventListener('click', async () => {
          const rr = await api('/channel/post/delete', { post_id: +postId });
          if (rr.code === 0) {
            closeModal(); if (window.SFX) SFX.delete(); toast('已删除');
            reloadConversations();
            if (S.current && S.current.type === 'channel') loadChannel(S.current.id);  // 同步刷新背后会话流
            const altWrap = mediaViewEl && $('#mvAltWrap', mediaViewEl);
            if (altWrap && altWrap.isConnected) renderMyPosts(altWrap, channelId);        // 回到“我的帖子”列表
          } else toast(rr.msg || '删除失败');
        });
      };
      paintBtn();
    }
    // 底部评论框：发送后经 receive_comment 广播回显（含自己）
    const input = $('#pcCmtInput', modalEl), sendBtn = $('#pcCmtSend', modalEl);
    const submit = () => {
      const text = (input.value || '').trim();
      if (!text || !S.openPostRoom) return;
      S.sock.emit('send_channel_comment', { channel_id: S.openPostRoom.channel_id, post_id: S.openPostRoom.post_id, comment: text }, (ack) => {
        if (ack && ack.code === 200) { input.value = ''; if (window.SFX) SFX.msgOut(); if (scroll) scroll.scrollTop = scroll.scrollHeight; }
        else toast(ack && ack.msg ? ack.msg : '评论失败');
      });
    };
    if (sendBtn) sendBtn.addEventListener('click', submit);
    if (input) input.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit(); } });
  }

  // 点击菜单外部 / Esc / 滚动 / 失焦：收起；忽略"⋮"按钮自身（避免与开合逻辑冲突）
  document.addEventListener('click', (e) => { if (chatMoreEl && !e.target.closest('.chat-more') && !e.target.closest('#chatInfoBtn')) closeChatMore(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeChatMore(); });
  if (msgScrollEl) msgScrollEl.addEventListener('scroll', closeChatMore);
  window.addEventListener('blur', closeChatMore);
  window.addEventListener('resize', closeChatMore);

  // 右上角"⋮"入口：私聊/群聊/频道均弹出下拉菜单（群聊：静音/退出/退出并拉黑）；群资料页改由点击头部进入
  async function toggleConvInfo() {
    if (!S.current) return;
    if (S.current.type === 'channel') { openChannelMore(); return; }
    if (S.current.type === 'group') { openGroupMore(); return; }
    openPrivateMore();
  }

  function hideConvInfo() {
    panelEl.classList.remove('info-open');
  }

  /* ---------------- 机器人管理面板（群/频道通用） ---------------- */
  async function openBotPanel(scope, id) {
    const r = await api('/bot/' + scope + '/' + id + '/bots');
    if (!r || r.code !== 0) { toast((r && r.msg) || '加载失败'); return; }
    const d = r.data;
    if (!d.can_manage) { toast('无权限管理机器人'); return; }
    const rows = (d.bots || []).map(b => {
      const on = b.enabled === 1;
      let right;
      if (!b.bound) {
        right = '<button class="mini-btn blue" data-bind="' + b.id + '">启用</button>';
      } else {
        right = '<div class="switch' + (on ? ' on' : '') + '" data-toggle="' + b.id + '" data-on="' + (on ? 1 : 0) + '"></div>' +
          '<button class="mini-btn" data-cfg="' + b.id + '">配置</button>' +
          '<button class="mini-btn danger-red" data-unbind="' + b.id + '">移除</button>';
      }
      return '<div class="bot-row"><div class="bot-info"><div class="bot-name">' + esc(b.name) + '</div>' +
        '<div class="bot-desc">' + esc(b.description || '') + '</div></div><div class="bot-acts">' + right + '</div></div>';
    }).join('');
    openModal('机器人管理',
      '<div class="modal-hint" style="margin:-6px 0 10px;line-height:1.6">为' + (scope === 'group' ? '本群' : '本频道') + '配置自动化 Bot：启用/停用、修改参数、查看执行日志。</div>' +
      (rows || '<div class="loading-hint">暂无可用 Bot</div>') +
      '<div style="text-align:right;margin-top:12px"><button class="mini-btn" id="botViewLogs">查看执行日志</button></div>',
      null);
    const body = $('#modalBody');
    $$('[data-bind]', body).forEach(btn => btn.addEventListener('click', async () => {
      const rr = await api('/bot/' + scope + '/' + id + '/bind', { bot_template_id: +btn.dataset.bind });
      if (rr.code !== 0) { toast(rr.msg || '失败'); return; }
      const b = (d.bots || []).find(x => x.id === +btn.dataset.bind);
      if (b && (BOT_SCHEMA[b.code] || []).length) { toast('已启用，请设置参数'); openBotConfig(scope, id, b); }
      else { toast('已启用'); openBotPanel(scope, id); }
    }));
    $$('[data-unbind]', body).forEach(btn => btn.addEventListener('click', async () => {
      const rr = await api('/bot/' + scope + '/' + id + '/unbind', { bot_template_id: +btn.dataset.unbind });
      toast(rr.msg || '已移除'); if (rr.code === 0) openBotPanel(scope, id);
    }));
    $$('[data-toggle]', body).forEach(sw => sw.addEventListener('click', async () => {
      const turnOn = sw.dataset.on !== '1';
      const rr = await api('/bot/' + scope + '/' + id + '/toggle', { bot_template_id: +sw.dataset.toggle, enabled: turnOn ? 1 : 0 });
      toast(rr.msg || '已更新'); if (rr.code === 0) openBotPanel(scope, id);
    }));
    $$('[data-cfg]', body).forEach(btn => btn.addEventListener('click', () => {
      const b = (d.bots || []).find(x => x.id === +btn.dataset.cfg);
      if (b) openBotConfig(scope, id, b);
    }));
    const vl = $('#botViewLogs', body); if (vl) vl.addEventListener('click', () => openBotLogs(scope, id));
  }

  /* 每个 Bot 的表单化配置字段（字段名严格对应 bots/rules.py 读取的 cfg 键） */
  const BOT_SCHEMA = {
    antiflood: [
      { key: 'window', label: '统计窗口（秒）', type: 'number', min: 1, def: 10 },
      { key: 'limit', label: '窗口内超过几条判定为刷屏', type: 'number', min: 1, def: 6 },
      { key: 'action', label: '触发后处置', type: 'select', def: 'mute', options: [['mute', '禁言'], ['kick', '踢出'], ['warn', '仅警告']] },
      { key: 'mute_minutes', label: '禁言时长（分钟）', type: 'number', min: 1, def: 5, showIf: ['action', 'mute'] },
    ],
    antiimage: [
      { key: 'window', label: '统计窗口（秒）', type: 'number', min: 1, def: 60 },
      { key: 'limit', label: '窗口内发图超过几张', type: 'number', min: 1, def: 6 },
      { key: 'action', label: '触发后处置', type: 'select', def: 'mute', options: [['mute', '禁言'], ['kick', '踢出']] },
      { key: 'mute_minutes', label: '禁言时长（分钟）', type: 'number', min: 1, def: 10, showIf: ['action', 'mute'] },
    ],
    antiad: [
      { key: 'only_new', label: '仅对新成员生效', type: 'switch', def: true },
      { key: 'new_minutes', label: '新成员时限（分钟）', type: 'number', min: 1, def: 30, showIf: ['only_new', 'true'] },
      { key: 'action', label: '触发后处置', type: 'select', def: 'kick', options: [['kick', '踢出'], ['mute', '禁言'], ['warn', '仅警告']] },
      { key: 'mute_minutes', label: '禁言时长（分钟）', type: 'number', min: 1, def: 10, showIf: ['action', 'mute'] },
    ],
    duplicate: [
      { key: 'window', label: '统计窗口（秒）', type: 'number', min: 1, def: 60 },
      { key: 'times', label: '连续重复几次触发', type: 'number', min: 2, def: 3 },
      { key: 'action', label: '触发后处置', type: 'select', def: 'warn', options: [['warn', '仅警告'], ['mute', '禁言']] },
      { key: 'mute_minutes', label: '禁言时长（分钟）', type: 'number', min: 1, def: 5, showIf: ['action', 'mute'] },
    ],
    nightmute: [
      { key: 'start', label: '禁言开始时间', type: 'time', def: '23:00' },
      { key: 'end', label: '禁言结束时间', type: 'time', def: '07:00' },
    ],
    keyword_reply: [
      { key: 'rules', label: '回复规则（每行一条，格式：关键词=回复内容）', type: 'rules', def: [] },
    ],
    welcome: [
      { key: 'text', label: '欢迎语（{nickname} 会替换为成员昵称）', type: 'text', def: '欢迎 {nickname} 加入本群！' },
    ],
    leave_notice: [
      { key: 'text', label: '通知文案（{nickname} 替换为昵称）', type: 'text', def: '{nickname} 退出了群聊' },
      { key: 'notice_admins', label: '通知群主/管理员', type: 'switch', def: true },
      { key: 'broadcast', label: '同时在群内播报', type: 'switch', def: false },
    ],
  };

  function _botFieldHtml(f, val) {
    const v = (val === undefined || val === null) ? f.def : val;
    const showif = f.showIf ? (' data-showif-key="' + f.showIf[0] + '" data-showif-val="' + f.showIf[1] + '"') : '';
    if (f.type === 'number') {
      return '<div class="bot-field"' + showif + '><label class="bf-label">' + esc(f.label) + '</label>' +
        '<input class="modal-input bf-input" type="number" data-key="' + f.key + '" value="' + esc(v) + '"' + (f.min != null ? ' min="' + f.min + '"' : '') + '></div>';
    }
    if (f.type === 'time') {
      return '<div class="bot-field"' + showif + '><label class="bf-label">' + esc(f.label) + '</label>' +
        '<input class="modal-input bf-input" type="time" data-key="' + f.key + '" value="' + esc(v) + '"></div>';
    }
    if (f.type === 'text') {
      return '<div class="bot-field"' + showif + '><label class="bf-label">' + esc(f.label) + '</label>' +
        '<input class="modal-input bf-input" type="text" data-key="' + f.key + '" value="' + esc(v) + '"></div>';
    }
    if (f.type === 'select') {
      const opts = (f.options || []).map(o => '<option value="' + esc(o[0]) + '"' + (String(v) === String(o[0]) ? ' selected' : '') + '>' + esc(o[1]) + '</option>').join('');
      return '<div class="bot-field"' + showif + '><label class="bf-label">' + esc(f.label) + '</label>' +
        '<select class="modal-input bf-input" data-key="' + f.key + '">' + opts + '</select></div>';
    }
    if (f.type === 'switch') {
      return '<label class="bot-field bot-field-row"' + showif + '><span class="bf-label">' + esc(f.label) + '</span>' +
        '<input type="checkbox" class="bf-check" data-key="' + f.key + '"' + (v ? ' checked' : '') + '></label>';
    }
    if (f.type === 'rules') {
      const lines = Array.isArray(v) ? v.map(r => (r.keyword || '') + '=' + (r.reply || '')).join('\n') : '';
      return '<div class="bot-field"' + showif + '><label class="bf-label">' + esc(f.label) + '</label>' +
        '<textarea class="modal-input bf-input" data-key="' + f.key + '" rows="5" style="resize:vertical;line-height:1.5" placeholder="入群=欢迎加入！\n群规=请查看群公告">' + esc(lines) + '</textarea></div>';
    }
    return '';
  }

  function openBotConfig(scope, id, b) {
    const eff = Object.assign({}, b.default_config || {}, b.config || {});
    const schema = BOT_SCHEMA[b.code] || [];
    if (!schema.length) { toast('该 Bot 无需额外配置，直接启用即可'); return; }
    const body = '<div class="modal-hint" style="margin:-6px 0 12px;line-height:1.6">' + esc(b.description || '') + '</div>' +
      schema.map(f => _botFieldHtml(f, eff[f.key])).join('');
    openModal('配置 · ' + b.name, body, async () => {
      const cfg = Object.assign({}, eff); // 保留白名单等未进表单的字段
      schema.forEach(f => {
        const el = document.querySelector('#modalBody [data-key="' + f.key + '"]');
        if (!el) return;
        if (f.type === 'number') { let n = parseInt(el.value, 10); if (isNaN(n)) n = f.def; if (f.min != null && n < f.min) n = f.min; cfg[f.key] = n; }
        else if (f.type === 'switch') cfg[f.key] = el.checked;
        else if (f.type === 'rules') {
          cfg[f.key] = el.value.split('\n').map(line => {
            const idx = line.indexOf('=');
            if (idx < 0) return null;
            const kw = line.slice(0, idx).trim(), rp = line.slice(idx + 1).trim();
            return (kw && rp) ? { keyword: kw, reply: rp } : null;
          }).filter(Boolean);
        }
        else cfg[f.key] = el.value; // text / time / select
      });
      const r = await api('/bot/' + scope + '/' + id + '/config', { bot_template_id: b.id, config: cfg });
      if (r.code === 0) { toast('配置已保存'); openBotPanel(scope, id); }
      else toast(r.msg || '保存失败');
    }, '保存');
    // 条件字段显隐（如：仅当选“禁言”才显示禁言时长）
    const bodyEl = $('#modalBody');
    const refresh = () => {
      $$('[data-showif-key]', bodyEl).forEach(node => {
        const k = node.getAttribute('data-showif-key'), want = node.getAttribute('data-showif-val');
        const ctrl = bodyEl.querySelector('[data-key="' + k + '"]');
        if (!ctrl) return;
        const cur = ctrl.type === 'checkbox' ? ctrl.checked : ctrl.value;
        node.style.display = (String(cur) === String(want)) ? '' : 'none';
      });
    };
    bodyEl.addEventListener('change', refresh);
    refresh();
  }

  async function openBotLogs(scope, id) {
    const r = await api('/bot/' + scope + '/' + id + '/logs?page=1&size=30');
    if (!r || r.code !== 0) { toast((r && r.msg) || '加载失败'); return; }
    const actName = { mute: '禁言', kick: '踢出', warning: '警告', notice: '通知', reply: '回复' };
    const rows = (r.data.logs || []).map(l =>
      '<div class="log-row"><span class="log-time">' + esc((l.created_at || '').slice(5, 16).replace('T', ' ')) + '</span>' +
      '<b>' + esc(l.bot) + '</b> ' + esc(actName[l.action] || l.action) +
      (l.target_name ? (' → ' + esc(l.target_name)) : '') +
      (l.reason ? ('（' + esc(l.reason) + '）') : '') + '</div>').join('');
    openModal('Bot 执行日志',
      '<div class="modal-hint" style="margin:-6px 0 10px;line-height:1.6">共 ' + (r.data.total || 0) + ' 条，最新 30 条。</div>' +
      (rows || '<div class="loading-hint">暂无执行记录</div>'), null);
  }

  let groupInfoViewMode = 'main';   // 群资料面板当前视图：main（主页）/ members（成员）/ files（群文件）

  async function showGroupInfo(view) {
    const id = S.current.id;
    groupInfoViewMode = view || 'main';
    convInfoView.innerHTML = '<div class="loading-hint">加载中…</div>';
    panelEl.classList.add('info-open');
    const [m, f] = await Promise.all([api('/group/' + id + '/members').catch(() => null), api('/group/' + id + '/files').catch(() => null)]);
    const memberList = (m && m.code === 0 && m.data.members) ? m.data.members : [];
    const myRole = (m && m.code === 0) ? (m.data.my_role || 3) : 3;
    const muteAll = (m && m.code === 0) ? (m.data.mute_all || 0) : 0;
    const joinVerify = (m && m.code === 0) ? (m.data.join_need_verify != null ? m.data.join_need_verify : 1) : 1;
    const gName = (m && m.code === 0) ? (m.data.name || '') : (S.current && S.current.name || '');
    const gDesc = (m && m.code === 0) ? (m.data.description || '') : '';
    const gAvatar = (m && m.code === 0) ? m.data.avatar : null;
    const gNumber = (m && m.code === 0) ? (m.data.number || '') : '';
    const fileList = (f && f.code === 0 && f.data.files) ? f.data.files : [];
    const canManage = myRole === 1 || myRole === 2;
    const canManageTarget = (tr) => (myRole === 1 && tr !== 1) || (myRole === 2 && tr === 3);
    const now = Date.now();
    // 成员行：可管理/可转让的行打标，右键交由上下文菜单处理（二级视图专用）
    const memberRowHtml = (x) => {
      const md = x.mute_until ? parseDate(x.mute_until) : null;
      const muted = md && md.getTime() > now;
      const manageable = x.user_id !== S.me.id && canManage && canManageTarget(x.role);
      // 转让群主：仅群主可对非群主成员发起，独立于权限管理接口
      const transferable = myRole === 1 && x.role !== 1 && x.user_id !== S.me.id;
      const tnameAttr = (manageable || transferable) ? ' data-tname="' + esc(dispName(x.user_id, x.nickname)) + '"' : '';
      return '<div class="list-row"' + (manageable ? ' data-manage="1" data-muid="' + x.user_id + '" data-mrole="' + x.role + '" data-muted="' + (muted ? 1 : 0) + '"' : '') + (transferable ? ' data-transfer="1" data-tuid="' + x.user_id + '"' : '') + tnameAttr + (manageable || transferable ? ' title="右键进行管理操作"' : '') + ' data-user-id="' + x.user_id + '"><img src="' + esc(x.avatar_url || 'https://robohash.org/u' + x.user_id) + '"><div class="lr-main"><div class="lr-t">' + esc(dispName(x.user_id, x.nickname)) + (muted ? '<span class="muted-tag">禁言中</span>' : '') + '</div></div><span class="role-tag">' + esc(x.role_name) + '</span></div>';
    };
    const fileRowHtml = (x) =>
      '<div class="list-row" data-fileid="' + x.file_id + '">' + fileIcHtml(x.filename, 'width:38px;height:38px') + '<div class="lr-main"><div class="lr-t">' + esc(x.filename) + '</div><div class="lr-s">' + esc(x.sender_name) + ' 上传</div></div><a class="mini-btn blue" href="' + esc(x.file_url) + '" download>下载</a><button class="mini-btn" data-delfile="' + x.file_id + '">删除</button></div>';

    // ===== 二级视图：群成员（列表 + 右键管理） =====
    if (groupInfoViewMode === 'members') {
      renderGroupInfoSub('群成员', memberList.length + ' 人', memberList.map(memberRowHtml).join('') || '<div class="loading-hint">无成员数据</div>');
      bindGroupMemberActions(id, myRole);
      return;
    }
    // ===== 二级视图：群文件（列表 + 下载/删除） =====
    if (groupInfoViewMode === 'files') {
      renderGroupInfoSub('群文件', fileList.length + ' 个文件', fileList.map(fileRowHtml).join('') || '<div class="loading-hint">还没有群文件</div>');
      bindGroupFileActions(id);
      return;
    }

    // ===== 主视图：信息卡 + 成员/群文件入口 + 群设置 + 更多操作 =====
    // 群信息顶部卡片（对齐频道 .ch-card 风格）：群主/管理员可编辑名称与简介
    const infoCard = '<div class="ch-card"><img src="' + esc(gAvatar || letterAvatar(gName)) + '"><div class="ch-meta"><div class="ch-name">' + esc(gName) + '</div>' +
      (gDesc ? '<div class="ch-desc">' + esc(gDesc) + '</div>' : '<div class="ch-desc ch-desc-empty">暂无简介</div>') +
      '<div class="ch-num">群号 ' + esc(gNumber) + ' · ' + memberList.length + ' 名成员</div></div>' +
      (canManage ? '<button class="mini-btn blue ch-edit-btn" id="gmEditInfo">编辑</button>' : '') + '</div>';
    // 成员 / 群文件：各自做成独立入口按钮，点击切入二级视图
    const entries =
      '<div class="gi-entries">' +
      '<button class="gi-entry" id="giMembers" type="button"><span class="gi-entry-ic"><i class="ic ic-group"></i></span><span class="gi-entry-label">群成员</span><span class="gi-entry-count">' + memberList.length + '</span><span class="gi-entry-chev">\u203A</span></button>' +
      '<button class="gi-entry" id="giFiles" type="button"><span class="gi-entry-ic"><i class="ic ic-files"></i></span><span class="gi-entry-label">群文件</span><span class="gi-entry-count">' + fileList.length + '</span><span class="gi-entry-chev">\u203A</span></button>' +
      '</div>';
    const bar = canManage ?
      '<div class="gm-bar"><span>全员禁言</span><div class="switch' + (muteAll ? ' on' : '') + '" id="gmMuteAll"></div></div>' +
      '<div class="gm-bar"><span>入群需验证</span><div class="switch' + (joinVerify ? ' on' : '') + '" id="gmJoinVerify"></div></div>' +
      '<div class="gm-bar"><span>机器人</span><button class="mini-btn blue" id="gmBots">管理</button></div>' : '';
    const acts = '<div class="gi-acts">' +
      '<button class="mini-btn blue" id="gmInvite">＋ 邀请成员</button>' +
      (canManage ? '<button class="mini-btn blue" id="gmAnnounce">发布公告</button>' : '') +
      // 解散群聊：仅群主；退出群聊：仅普通成员/管理员（群主用“解散”）；拉黑：所有人可用
      ((myRole === 1) ? '<button class="mini-btn danger-red" id="gmDisband">解散群聊</button>' : '') +
      ((myRole !== 1) ? '<button class="mini-btn danger-red" id="gmLeave">退出群聊</button>' : '') +
      '<button class="mini-btn danger-red" id="gmBlock">' + (isBlocked('group', id) ? '取消拉黑' : '拉黑该群聊') + '</button>' +
      '</div>';
    convInfoView.innerHTML =
      '<div class="conv-info-inner">' + infoCard + entries +
      (canManage ? '<div class="comments-title">群设置</div>' + bar : '') +
      '<div class="comments-title">更多操作</div>' + acts +
      '</div>';
    // 入口按钮：切入成员 / 群文件二级视图
    $('#giMembers', convInfoView).addEventListener('click', () => showGroupInfo('members'));
    $('#giFiles', convInfoView).addEventListener('click', () => showGroupInfo('files'));
    const ma = $('#gmMuteAll', convInfoView); if (ma) ma.addEventListener('click', () => {
      const turnOn = !ma.classList.contains('on');
      const doMuteAll = async () => {
        const r = await api('/group/mute_all', { group_id: id, enabled: turnOn ? 1 : 0 });
        if (r.code === 0) { closeModal(); toast(r.msg); showGroupInfo(); } else toast(r.msg || '操作失败');
      };
      openModal(turnOn ? '开启全员禁言' : '关闭全员禁言',
        '<div class="modal-hint" style="margin:-6px 0 0;line-height:1.6">' + (turnOn
          ? '开启后除群主/管理员外，所有成员将无法发送消息（含图片/文件）。'
          : '确定关闭全员禁言，恢复普通成员发言？') + '</div>',
        doMuteAll, turnOn ? '确认开启' : '确认关闭', turnOn);
    });
    const ib = $('#gmInvite', convInfoView); if (ib) ib.addEventListener('click', openGroupInvite);
    // 入群方式开关：需验证 / 直接加入
    const jv = $('#gmJoinVerify', convInfoView); if (jv) jv.addEventListener('click', async () => {
      const turnOn = !jv.classList.contains('on');
      const r = await api('/group/join_mode', { group_id: id, need_verify: turnOn ? 1 : 0 });
      if (r.code === 0) { toast(r.msg); showGroupInfo(); } else toast(r.msg || '操作失败');
    });
    // 机器人管理入口
    const gb = $('#gmBots', convInfoView); if (gb) gb.addEventListener('click', () => openBotPanel('group', id));
    const an = $('#gmAnnounce', convInfoView); if (an) an.addEventListener('click', openAnnouncePublish);
    // 编辑群头像/名称/简介：群主或管理员（后端 /group/info 允许 role 1/2）
    const ei = $('#gmEditInfo', convInfoView); if (ei) ei.addEventListener('click', () => {
      openInfoEditor({
        title: '编辑群信息', avatar: gAvatar, name: gName, desc: gDesc, nameLabel: '群名称',
        onSave: async ({ name, description, avatarUrl }) => {
          const payload = { name, description };
          if (avatarUrl) payload.avatar_url = avatarUrl;
          const r = await api('/group/info?group_id=' + id, payload, 'PUT');
          if (r.code === 0) {
            closeModal(); toast('已保存');
            if (S.current && S.current.type === 'group' && S.current.id === id) {
              S.current.name = r.data.name;
              if (r.data.avatar_url) S.current.avatar = r.data.avatar_url;
              $('#chatTitle').textContent = r.data.name;
              if (r.data.avatar_url) { $('#chatAvatar').src = r.data.avatar_url; watchAvatar($('#chatAvatar'), r.data.name); }
            }
            showGroupInfo();
            reloadConversations();
          } else toast(r.msg || '保存失败');
        }
      });
    });
    const dbBtn = $('#gmDisband', convInfoView); if (dbBtn) dbBtn.addEventListener('click', () => {
      const doDisband = async () => {
        const r = await api('/group/owner/disband', { group_id: id });
        if (r.code === 0) {
          closeModal();
          toast('群聊已解散');
          panelEl.classList.remove('info-open');
          await reloadConversations();
          const c = S.convs.get(convKey('group', id));
          if (c) c.deleted = true;
          if (S.current && S.current.type === 'group' && S.current.id === id) {
            S.current = null;
            msgListEl.innerHTML = '';
            convInfoView.innerHTML = '';
            annSyncVisible();   // 不走 panelHide，手动同步小狗显隐
          }
        } else toast(r.msg || '操作失败');
      };
      openModal('解散群聊',
        '<div class="modal-hint" style="margin:-6px 0 0;line-height:1.6">解散后群聊将被永久删除，所有成员、消息、文件都将清除，且<span style="color:#e53935;font-weight:700">不可恢复</span>。</div>',
        doDisband, '确认解散', true);
    });
    // 退出群聊：普通成员/管理员（群主不显示此按钮），成功后刷新会话并清空当前
    const lvBtn = $('#gmLeave', convInfoView); if (lvBtn) lvBtn.addEventListener('click', () => {
      const doLeave = async () => {
        const r = await api('/group/leave', { group_id: id });
        if (r.code === 0) {
          closeModal();
          toast('已退出群聊');
          panelEl.classList.remove('info-open');
          await reloadConversations();
          const c = S.convs.get(convKey('group', id));
          if (c) c.deleted = true;
          if (S.current && S.current.type === 'group' && S.current.id === id) {
            S.current = null;
            msgListEl.innerHTML = '';
            convInfoView.innerHTML = '';
            annSyncVisible();   // 不走 panelHide，手动同步小狗显隐
          }
        } else toast(r.msg || '操作失败');
      };
      openModal('退出群聊',
        '<div class="modal-hint" style="margin:-6px 0 0;line-height:1.6">退出后将不再接收该群消息，需重新被邀请或申请加入才能再次进群。</div>',
        doLeave, '确认退出', true);
    });
    // 拉黑/取消拉黑该群聊
    const gbBtn = $('#gmBlock', convInfoView); if (gbBtn) gbBtn.addEventListener('click', () => {
      if (isBlocked('group', id)) { doUnblock('group', id, () => { if (panelEl.classList.contains('info-open')) showGroupInfo(); }); }
      else { doBlock('group', id, gName, () => { convInfoView.innerHTML = ''; }); }
    });
    // 成员管理、群文件删除已下沉到各自的二级视图（见 bindGroupMemberActions / bindGroupFileActions）
  }

  // 群资料二级视图容器：返回按钮 + 标题/副标题 + 列表；返回切回主视图
  function renderGroupInfoSub(title, sub, listHtml) {
    convInfoView.innerHTML =
      '<div class="conv-info-inner"><div class="gi-sub">' +
      '<button class="gi-sub-back" id="giSubBack" type="button"><span class="chev"></span>返回</button>' +
      '<div class="gi-sub-head"><span class="gi-sub-title">' + esc(title) + '</span><span class="gi-sub-sub">' + esc(sub) + '</span></div>' +
      '<div class="gi-sub-list">' + listHtml + '</div>' +
      '</div></div>';
    $('#giSubBack', convInfoView).addEventListener('click', () => showGroupInfo('main'));
  }

  // 群文件二级视图：下载（a[download] 原生）+ 删除（成功溶解该行，删空则重渲染空状态）
  function bindGroupFileActions(id) {
    $$('[data-delfile]', convInfoView).forEach(btn => btn.addEventListener('click', async () => {
      const r = await api('/group/file/delete', { file_id: +btn.dataset.delfile });
      if (r.code === 0) {
        const row = btn.closest('.list-row');
        if (window.PARTICLES) PARTICLES.dissolve(row); else row.remove();
        toast('文件已删除');
        if (!$$('[data-delfile]', convInfoView).length) showGroupInfo('files');
      } else toast(r.msg || '无权删除');
    }));
  }

  // 群成员二级视图：右键成员行弹上下文菜单（转让群主 · 设/撤管理员 · 禁言 · 踢出），操作后刷新二级视图
  function bindGroupMemberActions(id, myRole) {
    const kickMember = (uid, name) => {
      const doKick = async () => {
        const r = await api('/group/kick', { group_id: id, target_user_id: uid });
        if (r.code === 0) { closeModal(); toast('已踢出'); showGroupInfo('members'); } else toast(r.msg || '操作失败');
      };
      openModal('踢出成员',
        '<div class="modal-hint" style="margin:-6px 0 0;line-height:1.6">确定将 <span style="font-weight:700">' + esc(name) + '</span> 踢出群聊？</div>',
        doKick, '确认踢出', true);
    };
    const muteMember = (uid, isMuted, name) => {
      const doMute = async () => {
        const r = await api('/group/mute', { group_id: id, target_user_id: uid, minutes: isMuted ? 0 : 10 });
        if (r.code === 0) { closeModal(); toast(r.msg); showGroupInfo('members'); } else toast(r.msg || '操作失败');
      };
      openModal(isMuted ? '解除禁言' : '禁言成员',
        '<div class="modal-hint" style="margin:-6px 0 0;line-height:1.6">' + (isMuted
          ? '确定解除 <span style="font-weight:700">' + esc(name) + '</span> 的禁言状态？'
          : '确定将 <span style="font-weight:700">' + esc(name) + '</span> 禁言 10 分钟？期间 TA 无法发送消息（含图片/文件）。') + '</div>',
        doMute, isMuted ? '确认解除' : '确认禁言', !isMuted);
    };
    const setMemberRole = (uid, action, name) => {
      const doRole = async () => {
        const r = await api('/group/permission/set', { group_id: id, target_member_id: uid, action });
        if (r.code === 0) { closeModal(); toast(action === 'promote' ? '已设为管理员' : '已取消管理员'); showGroupInfo('members'); } else toast(r.msg || '操作失败');
      };
      openModal(action === 'promote' ? '设为管理员' : '取消管理员',
        '<div class="modal-hint" style="margin:-6px 0 0;line-height:1.6">' + (action === 'promote'
          ? '确定将 <span style="font-weight:700">' + esc(name) + '</span> 设为管理员？TA 将可以禁言/踢出普通成员。'
          : '确定取消 <span style="font-weight:700">' + esc(name) + '</span> 的管理员身份？') + '</div>',
        doRole, action === 'promote' ? '确认设置' : '确认取消', false);
    };
    // 转让群主：专用接口 PUT /group/transfer 发起申请，需对方确认后生效（与 permission/set 互不混用）
    const transferOwner = (uid, name) => {
      const doTransfer = async () => {
        const r = await api('/group/transfer?group_id=' + id, { new_owner_id: uid }, 'PUT');
        if (r.code === 0) { closeModal(); toast(r.msg || '转让申请已发送'); }
        else toast(r.msg || '操作失败');
      };
      openModal('转让群主',
        '<div class="modal-hint" style="margin:-6px 0 0;line-height:1.6">将向<span style="font-weight:700">' + esc(name) + '</span>发送群主转让申请，对方接受后你将变为普通成员。</div>',
        doTransfer, '发送申请', true);
    };
    $$('[data-manage]', convInfoView).forEach(row => row.addEventListener('contextmenu', (e) => {
      e.preventDefault(); e.stopPropagation();
      const uid = +row.dataset.muid, role = +row.dataset.mrole, isMuted = row.dataset.muted === '1';
      const nm = row.dataset.tname || (row.querySelector('.lr-t') ? row.querySelector('.lr-t').childNodes[0].textContent : '');
      const items = [];
      if (myRole === 1 && role !== 1) items.push({ label: '转让群主', fn: () => transferOwner(uid, nm) });
      if (role === 2) items.push({ label: '取消管理员', fn: () => setMemberRole(uid, 'demote', nm) });
      if (role === 3) items.push({ label: '设为管理员', fn: () => setMemberRole(uid, 'promote', nm) });
      items.push({ label: isMuted ? '解除禁言' : '禁言 10 分钟', fn: () => muteMember(uid, isMuted, nm) });
      items.push({ label: '踢出群聊', danger: true, fn: () => kickMember(uid, nm) });
      openCtxMenu(e.clientX, e.clientY, items);
    }));
    // 群主对成员的全部可管理场景已由 data-manage 覆盖；若因规则限制不可管理但可转让，单独挂右键入口
    $$('[data-transfer]:not([data-manage])', convInfoView).forEach(row => row.addEventListener('contextmenu', (e) => {
      e.preventDefault(); e.stopPropagation();
      openCtxMenu(e.clientX, e.clientY, [{ label: '转让群主', fn: () => transferOwner(+row.dataset.tuid, row.dataset.tname) }]);
    }));
  }

  // 频道信息：与群聊同款内嵌视图（头部卡片 + 操作按钮 + 订阅者列表右键管理）
  async function showChannelInfo() {
    const id = S.current.id;
    convInfoView.innerHTML = '<div class="loading-hint">加载中…</div>';
    panelEl.classList.add('info-open');
    const m = await api('/channel/' + id + '/members').catch(() => null);
    if (!m || m.code !== 0) { convInfoView.innerHTML = '<div class="loading-hint">' + esc((m && m.msg) || '加载失败') + '</div>'; return; }
    const d = m.data;
    const myRole = d.my_role || 3;
    const canManage = myRole === 1 || myRole === 2;
    // 与后端一致：踢人需 1/2 且不能动频道主；设/撤副频道主仅频道主
    const canActOn = (tr) => (myRole === 1 && tr !== 1) || (myRole === 2 && tr === 3);
    const members = (d.members || []).map(x => {
      const manageable = x.user_id !== S.me.id && canActOn(x.role);
      return '<div class="list-row"' + (manageable ? ' data-cmanage="1" data-cuid="' + x.user_id + '" data-crole="' + x.role + '" title="右键进行管理操作"' : '') + ' data-user-id="' + x.user_id + '"><img src="' + esc(x.avatar_url || 'https://robohash.org/u' + x.user_id) + '"><div class="lr-main"><div class="lr-t">' + esc(dispName(x.user_id, x.nickname)) + '</div></div><span class="role-tag">' + esc(x.role_name) + '</span></div>';
    }).join('');
    const acts = '<div class="ch-acts">' +
      (canManage ? '<button class="mini-btn blue" id="chInvite">＋ 邀请订阅</button><button class="mini-btn blue" id="chAnnounce">发布公告</button><button class="mini-btn blue" id="chBots">机器人</button>' : '') +
      (myRole === 1 ? '<button class="mini-btn danger-red" id="chDelete">删除频道</button>'
        : '<button class="mini-btn danger-red" id="chLeave">退订频道</button>') +
      '<button class="mini-btn danger-red" id="chBlock">' + (isBlocked('channel', id) ? '取消拉黑' : '拉黑该频道') + '</button>' + '</div>';
    convInfoView.innerHTML =
      '<div class="conv-info-inner">' +
      '<div class="ch-card"><img src="' + esc(d.avatar || letterAvatar(d.name)) + '"><div class="ch-meta"><div class="ch-name">' + esc(d.name) + '</div>' +
      (d.description ? '<div class="ch-desc">' + esc(d.description) + '</div>' : '') +
      '<div class="ch-num">频道号 ' + esc(d.number || '') + ' · ' + d.total + ' 位订阅者</div></div>' +
      (canManage ? '<button class="mini-btn blue ch-edit-btn" id="chEditInfo">编辑</button>' : '') + '</div>' + acts +
      '<div class="comments-title">订阅者 · ' + d.total + '</div>' + (members || '<div class="loading-hint">无订阅者数据</div>') +
      '</div>';
    const iv = $('#chInvite', convInfoView); if (iv) iv.addEventListener('click', openChannelInvite);
    // 编辑频道头像/名称/简介：频道主或副频道主（后端 /channel/info 允许 role 1/2）
    const cei = $('#chEditInfo', convInfoView);
    if (cei) cei.addEventListener('click', () => {
      openInfoEditor({
        title: '编辑频道信息', avatar: d.avatar, name: d.name, desc: d.description || '', nameLabel: '频道名称',
        onSave: async ({ name, description, avatarUrl }) => {
          const payload = { name, description };
          if (avatarUrl) payload.avatar_url = avatarUrl;
          const r = await api('/channel/info?channel_id=' + id, payload, 'PUT');
          if (r.code === 0) {
            closeModal(); toast('已保存');
            if (S.current && S.current.type === 'channel' && S.current.id === id) {
              S.current.name = r.data.name;
              if (r.data.avatar) S.current.avatar = r.data.avatar;
              $('#chatTitle').textContent = r.data.name;
              if (r.data.avatar) { $('#chatAvatar').src = r.data.avatar; watchAvatar($('#chatAvatar'), r.data.name); }
            }
            showChannelInfo();
            reloadConversations();
          } else toast(r.msg || '保存失败');
        }
      });
    });
    const cb = $('#chBlock', convInfoView); if (cb) cb.addEventListener('click', () => {
      if (isBlocked('channel', id)) { doUnblock('channel', id, () => { if (panelEl.classList.contains('info-open')) showChannelInfo(); }); }
      else { doBlock('channel', id, d.name, () => { convInfoView.innerHTML = ''; }); }
    });
    const an = $('#chAnnounce', convInfoView); if (an) an.addEventListener('click', openAnnouncePublish);
    const cb2 = $('#chBots', convInfoView); if (cb2) cb2.addEventListener('click', () => openBotPanel('channel', id));
    const lv = $('#chLeave', convInfoView);
    if (lv) lv.addEventListener('click', () => {
      const doLeave = async () => {
        const r = await api('/channel/leave', { channel_id: id });
        if (r.code === 0) { closeModal(); toast('已退订频道'); closeChat(); reloadConversations(); }
        else toast(r.msg || '操作失败');
      };
      openModal('退订频道',
        '<div class="modal-hint" style="margin:-6px 0 0;line-height:1.6">确定退订该频道？退订后将不再接收其帖子。</div>',
        doLeave, '确认退订', true);
    });
    const del = $('#chDelete', convInfoView);
    if (del) del.addEventListener('click', () => {
      const doDelete = async () => {
        const r = await api('/channel/remove_channel', { channel_id: id });
        if (r.code === 0) { closeModal(); toast('频道已删除'); closeChat(); reloadConversations(); }
        else toast(r.msg || '操作失败');
      };
      openModal('删除频道',
        '<div class="modal-hint" style="margin:-6px 0 0;line-height:1.6">删除频道将清空所有帖子与订阅关系，且<span style="color:#e53935;font-weight:700">不可恢复</span>，确定继续？</div>',
        doDelete, '确认删除', true);
    });
    const setRole = async (uid, action) => {
      const r = await api('/channel/permission/set', { channel_id: id, target_user_id: uid, action });
      if (r.code === 0) { toast(r.msg || (action === 'promote' ? '已设为副频道主' : '已撤销副频道主')); showChannelInfo(); } else toast(r.msg || '操作失败');
    };
    const removeSub = async (uid) => {
      const r = await api('/channel/remove_member', { channel_id: id, target_user_id: uid });
      if (r.code === 0) { toast('已移出频道'); showChannelInfo(); } else toast(r.msg || '操作失败');
    };
    $$('[data-cmanage]', convInfoView).forEach(row => row.addEventListener('contextmenu', (e) => {
      e.preventDefault(); e.stopPropagation();
      const uid = +row.dataset.cuid, role = +row.dataset.crole;
      const items = [];
      if (myRole === 1) {
        if (role === 2) items.push({ label: '撤销副频道主', fn: () => setRole(uid, 'demote') });
        if (role === 3) items.push({ label: '设为副频道主', fn: () => setRole(uid, 'promote') });
      }
      items.push({ label: '移出频道', danger: true, fn: () => removeSub(uid) });
      openCtxMenu(e.clientX, e.clientY, items);
    }));
  }

  async function openGroupInvite() {
    const id = S.current.id;
    const [c, m] = await Promise.all([api('/chat/contacts').catch(() => null), api('/group/' + id + '/members').catch(() => null)]);
    const fr = (c && c.code === 0) ? (c.data.friends || []) : [];
    const memberIds = new Set(((m && m.code === 0 && m.data.members) || []).map(x => x.user_id));
    const candidates = fr.filter(x => !memberIds.has(x.user_id));
    if (!candidates.length) { toast('没有可邀请的好友（或好友都已在群中）'); return; }
    openModal('邀请成员', '<div class="modal-hint">选择好友邀请加入本群（需对方确认）</div>' +
      candidates.map(x => '<div class="list-row" style="border-radius:10px;margin-bottom:2px"><img src="' + esc(x.avatar || ('https://robohash.org/u' + x.user_id)) + '"><div class="lr-main"><div class="lr-t">' + esc(x.name) + '</div></div><button class="mini-btn blue" data-inv="' + x.user_id + '">邀请</button></div>').join(''), null);
    $$('[data-inv]').forEach(b => b.addEventListener('click', async () => {
      const r = await api('/group/invite', { group_id: id, invitee_id: +b.dataset.inv });
      if (r.code === 0) { b.textContent = '已邀请'; b.disabled = true; b.classList.remove('blue'); toast('邀请已发送'); }
      else toast(r.msg || '邀请失败');
    }));
  }

  async function openChannelInvite() {
    const id = S.current.id;
    const [c, m] = await Promise.all([api('/chat/contacts').catch(() => null), api('/channel/' + id + '/members').catch(() => null)]);
    const fr = (c && c.code === 0) ? (c.data.friends || []) : [];
    const subIds = new Set(((m && m.code === 0 && m.data.members) || []).map(x => x.user_id));
    const candidates = fr.filter(x => !subIds.has(x.user_id));
    if (!candidates.length) { toast('没有可邀请的好友（或好友都已订阅）'); return; }
    openModal('邀请订阅', '<div class="modal-hint">选择好友邀请订阅本频道（需对方确认）</div>' +
      candidates.map(x => '<div class="list-row" style="border-radius:10px;margin-bottom:2px"><img src="' + esc(x.avatar || ('https://robohash.org/u' + x.user_id)) + '"><div class="lr-main"><div class="lr-t">' + esc(x.name) + '</div></div><button class="mini-btn blue" data-cinv="' + x.user_id + '">邀请</button></div>').join(''), null);
    $$('[data-cinv]').forEach(b => b.addEventListener('click', async () => {
      const r = await api('/channel/invite', { channel_id: id, invitee_id: +b.dataset.cinv });
      if (r.code === 0) { b.textContent = '已邀请'; b.disabled = true; b.classList.remove('blue'); toast('邀请已发送'); }
      else toast(r.msg || '邀请失败');
    }));
  }

  /* ---------------- 朋友圈（动态） ---------------- */
  let momVis = 1;
  function momentCardHtml(m) {
    const media = (m.medias && m.medias.length) ? '<div class="moment-media' + (m.medias.length === 1 ? ' one' : '') + '">' +
      m.medias.map(md => md.media_type === 2 ? '<div class="vid-click"><video src="' + esc(md.url) + '" preload="metadata" playsinline></video><span class="tg-play"><svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z" fill="#fff"/></svg></span>' + vidExpandBtn(md.url, nameFromUrl(md.url)) + '</div>' : mediaImgHtml(md.url, 400, 'media')).join('') + '</div>' : '';
    const vis = m.visibility === 2 ? ' · 好友可见' : (m.visibility === 3 ? ' · 仅自己' : '');
    const card = document.createElement('div');
    card.className = 'moment-card';
    card.dataset.mid = m.moment_id; card.dataset.likes = m.like_count || 0; card.dataset.cmts = m.comment_count || 0;
    card.innerHTML =
      '<div class="moment-top">' + avatarImgHtml(m.user_avatar || letterAvatar(m.user_nickname), '', ' data-uid="' + m.user_id + '" data-nick="' + esc(m.user_nickname) + '"') +
      '<div style="flex:1;min-width:0"><div class="moment-name">' + esc(dispName(m.user_id, m.user_nickname)) + '</div><div class="moment-time">' + esc(fmtRelTime(m.created_at)) + esc(vis) + '</div></div>' +
      (m.is_self ? '<span class="moment-del" data-del="' + m.moment_id + '">删除</span>' : '') + '</div>' +
      (m.content ? '<div class="moment-content">' + esc(m.content) + '</div>' : '') + media +
      '<div class="moment-foot">' +
      '<div class="mf-actions">' +
      '<button class="mf-btn' + (m.liked ? ' liked' : '') + '" data-like="' + m.moment_id + '">' + likeBtnInner(m.liked) + '</button>' +
      '<span class="mf-sep"></span>' +
      '<button class="mf-btn" data-cmt="' + m.moment_id + '"><i class="ic ic-msg"></i><span>评论</span></button>' +
      '</div>' +
      '<button class="mf-dots" data-dots aria-label="互动"><i class="ic ic-dots"></i></button></div>' +
      '<div class="moment-likers" data-likers' + ((m.likers && m.likers.length) ? '' : ' hidden') + '><i class="ic ic-heart"></i><span>' + esc((m.likers || []).join('、')) + '</span></div>' +
      '<div class="moment-comments" data-cmts="' + m.moment_id + '"' + ((m.comments && m.comments.length) ? '' : ' hidden') + '>' +
      '<div class="mc-list" data-mclist>' + (m.comments || []).map(mcItemHtml).join('') + '</div>' +
      '<div class="mc-input" hidden><div class="mc-reply" hidden></div><input placeholder="写评论…"><button class="mini-btn blue">发送</button></div></div>';
    return card;
  }
  function mcItemHtml(c, i) {
    const delay = i ? ' style="animation-delay:' + Math.min(i * 45, 360) + 'ms"' : '';
    const self = c.user_id === S.me.id;
    const replyTo = c.reply_to_nickname ? '<span class="mc-rto"> 回复 <b>' + esc(c.reply_to_nickname) + '</b></span>' : '';
    return '<div class="mc-item" data-mcc="' + esc(c.comment_id) + '" data-mcuid="' + esc(c.user_id) + '" data-mcnick="' + esc(c.user_nickname) + '" data-self="' + (self ? '1' : '0') + '"' + delay + '>' +
      '<b>' + esc(dispName(c.user_id, c.user_nickname)) + '</b>' + replyTo + '：' + esc(c.content) +
      '<span class="mc-time">' + esc(fmtRelTime(c.created_at)) + '</span></div>';
  }
  /* 动态评论回复态：进入（记住目标 + 横幅 + 占位提示）/ 退出 */
  function enterMcReply(wrap, cid, nick) {
    wrap.dataset.replyTo = cid;
    const bar = $('.mc-reply', wrap);
    if (bar) { bar.hidden = false; bar.innerHTML = '<span>回复 @' + esc(nick || '') + '</span><button class="mcr-x" type="button" title="取消回复">✕</button>'; }
    const inp = $('input', wrap); if (inp) inp.placeholder = '回复 @' + (nick || '') + '…';
  }
  function exitMcReply(wrap) {
    delete wrap.dataset.replyTo;
    const bar = $('.mc-reply', wrap); if (bar) { bar.hidden = true; bar.innerHTML = ''; }
    const inp = $('input', wrap); if (inp) inp.placeholder = '写评论…';
  }
  // 删除自己的动态评论
  async function deleteMcComment(cid, item) {
    const r = await api('/moment/comment/delete', { comment_id: +cid });
    if (r.code === 0) {
      toast('已删除');
      const card = item.closest('.moment-card');
      const box = card && $('.moment-comments', card);
      if (card && box) refreshMcList(+card.dataset.mid, box, card);
    } else toast(r.msg || '删除失败');
  }
  function likeBtnInner(liked) {
    return '<i class="ic ic-heart"></i><span>' + (liked ? '已赞' : '赞') + '</span>';
  }
  function closeAllMf(scope) { $$('.moment-foot.open', scope || document).forEach(f => f.classList.remove('open')); }
  function bindMomentEvents(scope) {
    $$('.moment-card', scope).forEach(card => {
      const im = $('.moment-top img', card); if (im) watchAvatar(im, '');
      $$('img.media, .moment-media img', card).forEach(x => x.addEventListener('click', () => openAvatarViewer(fullSrc(x), x.getBoundingClientRect(), false, '')));
    });
    // 圆点按钮：展开/收起互动条；同时全页只保留一个展开（微信式）
    $$('[data-dots]', scope).forEach(b => b.addEventListener('click', e => {
      e.stopPropagation();
      const foot = b.closest('.moment-foot');
      const wasOpen = foot.classList.contains('open');
      closeAllMf();
      if (!wasOpen) foot.classList.add('open');
    }));
    $$('[data-like]', scope).forEach(b => b.addEventListener('click', async () => {
      const r = await api('/moment/like', { moment_id: +b.dataset.like });
      if (r.code === 0) {
        const liked = r.data.liked; b.classList.toggle('liked', liked); b.innerHTML = likeBtnInner(liked);
        // 点赞成功才播弹跳+光环动效（取消赞不播），重启动画以支持连续点击
        if (liked) { b.classList.remove('like-burst'); void b.offsetWidth; b.classList.add('like-burst'); setTimeout(() => b.classList.remove('like-burst'), 640); }
        const card = b.closest('.moment-card'); card.dataset.likes = r.data.like_count;
        // 点赞人条原地刷新（微信：红心 + 昵称列表）
        const lk = $('[data-likers]', card);
        if (lk) { const names = r.data.likers || []; lk.hidden = !names.length; $('span', lk).textContent = names.join('、'); }
        // 让"已赞/取消"状态先被看到，再收起互动条
        setTimeout(() => b.closest('.moment-foot').classList.remove('open'), 550);
      } else toast(r.msg || '操作失败');
    }));
    // 评论按钮：只切换输入框（评论列表常驻在卡片上）
    $$('[data-cmt]', scope).forEach(b => b.addEventListener('click', () => {
      const card = b.closest('.moment-card');
      const box = $('[data-cmts="' + b.dataset.cmt + '"]', card);
      const wrap = $('.mc-input', box);
      b.closest('.moment-foot').classList.remove('open'); // 收回互动条
      if (wrap.hidden) { box.hidden = false; wrap.hidden = false; $('input', wrap).focus(); }
      else { wrap.hidden = true; if (!$('[data-mclist]', box).children.length) box.hidden = true; }
    }));
    // 评论输入框：随卡片一次性渲染，发送后刷新该动态评论列表
    $$('.mc-input', scope).forEach(wrap => {
      const card = wrap.closest('.moment-card');
      const mid = +card.dataset.mid;
      const box = wrap.closest('.moment-comments');
      const inp = $('input', wrap), btn = $('.mini-btn', wrap);
      const send = () => {
        const v = inp.value.trim(); if (!v) return;
        const payload = { moment_id: mid, content: v };
        if (wrap.dataset.replyTo) payload.reply_to = +wrap.dataset.replyTo;
        api('/moment/comment', payload).then(rr => {
          if (rr.code === 0) { inp.value = ''; exitMcReply(wrap); refreshMcList(mid, box, card); }
          else toast(rr.msg || '失败');
        });
      };
      btn.addEventListener('click', send);
      inp.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); send(); } });
    });
    $$('[data-del]', scope).forEach(b => b.addEventListener('click', () => {
      const doDel = async () => {
        const r = await api('/moment/delete', { moment_id: +b.dataset.del });
        if (r.code === 0) { closeModal(); const card = b.closest('.moment-card'); if (window.PARTICLES) PARTICLES.dissolve(card); else card.remove(); toast('已删除'); }
        else toast(r.msg || '失败');
      };
      openModal('删除动态', '<div class="modal-hint" style="margin:-6px 0 0;line-height:1.6">删除后该动态及其评论将一并清除，确定删除？</div>', doDel, '确认删除', true);
    }));
    $$('.moment-top img[data-uid]', scope).forEach(im => im.addEventListener('click', () => openUserMoments(+im.dataset.uid, im.dataset.nick)));
  }
  async function refreshMcList(mid, box, card) {
    const r = await api('/moment/' + mid + '/comments');
    const list = (r.code === 0 && r.data.comments) ? r.data.comments : [];
    $('[data-mclist]', box).innerHTML = list.map(mcItemHtml).join('');
    box.hidden = false;
    card.dataset.cmts = list.length;
  }
  async function renderMoments() {
    const panel = $('#panelMoments');
    panel.innerHTML = '<div class="moments-head"><div class="mh-title">动态</div><span class="mh-tip">双击/长按下方"动态"发布</span></div><div id="momFeed"><div class="loading-hint">加载中…</div></div>';
    await loadMomentsFeed();
  }
  async function loadMomentsFeed() {
    const feed = $('#momFeed'); if (!feed) return;
    const r = await api('/moment/feed?per_page=20');
    if (r.code !== 0) { feed.innerHTML = '<div class="empty-hint">' + esc(r.msg || '加载失败') + '</div>'; return; }
    const list = r.data.moments || [];
    feed.innerHTML = '';
    if (!list.length) { feed.innerHTML = '<div class="empty-hint">还没有动态，双击/长按下方"动态"发布</div>'; return; }
    list.forEach(m => feed.appendChild(momentCardHtml(m)));
    bindMomentEvents(feed);
  }
  async function openUserMoments(uid, name) {
    const from = S.tab; // 记录来源页：返回时回到来处（通讯录来的就回通讯录）
    switchTab('moments');
    const panel = $('#panelMoments');
    panel.innerHTML = '<div class="moments-head"><div class="mh-title">' + esc(name || 'TA') + ' 的动态</div><button class="mini-btn" id="momBack">返回</button></div><div id="momFeed"><div class="loading-hint">加载中…</div></div>';
    $('#momBack').addEventListener('click', () => { if (from === 'moments') renderMoments(); else switchTab(from); });
    const feed = $('#momFeed');
    const r = await api('/moment/user/' + uid);
    if (r.code !== 0) { feed.innerHTML = '<div class="empty-hint">' + esc(r.msg || '加载失败') + '</div>'; return; }
    const list = r.data.moments || [];
    feed.innerHTML = '';
    if (!list.length) { feed.innerHTML = '<div class="empty-hint">没有可见的动态</div>'; return; }
    list.forEach(m => feed.appendChild(momentCardHtml(m)));
    bindMomentEvents(feed);
  }
  // 用户资料浮层卡：贴着点击的昵称弹出（不再居中），点外/滚动/Esc 关闭
  let userPopEl = null;
  // 全屏资料页覆盖层（点击私聊头部进入）
  let profileViewEl = null;
  function closeUserPop() {
    if (!userPopEl) return;
    const el = userPopEl; userPopEl = null;
    el.classList.remove('show');
    setTimeout(() => el.remove(), 200);
  }
  // 群/频道预览浮层卡（与 userPop 同一套定位/关闭机制，内含 echarts 性别饼图）
  let peekEl = null;
  function closePeek() {
    if (!peekEl) return;
    const el = peekEl; peekEl = null;
    try { if (el._chart && window.echarts) echarts.dispose(el._chart); } catch (e) {}
    el.classList.remove('show');
    setTimeout(() => el.remove(), 200);
  }
  // 通用锚定定位：默认贴锚点右侧，放不下翻到左侧，再夹到视口内
  function positionPop(pop, anchor) {
    const rect = (anchor || document.body).getBoundingClientRect();
    const w = pop.offsetWidth, h = pop.offsetHeight;
    let x = rect.right + 10;
    if (x + w > window.innerWidth - 12) x = rect.left - w - 10;
    if (x < 12) x = 12;
    let y = rect.top - 8;
    if (y + h > window.innerHeight - 12) y = window.innerHeight - h - 12;
    if (y < 12) y = 12;
    pop.style.left = x + 'px'; pop.style.top = y + 'px';
    pop.style.transformOrigin = (x > rect.right ? 'left' : 'right') + ' top';
  }
  document.addEventListener('mousedown', (e) => {
    if (userPopEl && !e.target.closest('#userPop') && !e.target.closest('.sender[data-pu]') && !e.target.closest('.peek-name') && !e.target.closest('.js-peek')) closeUserPop();
    if (peekEl && !e.target.closest('.peek-pop') && !e.target.closest('.peek-name') && !e.target.closest('.js-peek')) closePeek();
  }, true);
  document.addEventListener('scroll', () => { closeUserPop(); closePeek(); }, true);
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') { closeUserPop(); closePeek(); if (profileViewEl) closeProfileView(); if (mediaViewEl) closeMediaView(); } });
  async function openUserProfile(uid, anchor) {
    if (!uid || uid === S.me.id) return;
    if (userPopEl && +userPopEl.dataset.uid === uid) { closeUserPop(); return; } // 再点同一名字 = 收起
    closeUserPop(); closePeek();
    let r, mr;
    try {
      [r, mr] = await Promise.all([
        api('/user/profile/' + uid),
        api('/moment/user/' + uid).catch(() => null),
      ]);
    } catch (err) {
      toast('获取用户资料失败');
      return;
    }
    if (r.code !== 0) { toast(r.msg || '获取用户资料失败'); return; }
    const u = r.data;
    const moments = (mr && mr.code === 0 && mr.data.moments) ? mr.data.moments : [];
    // 近期动态缩略图：按时间先后取最近 4 张配图（视频封面跳过）
    const thumbs = [];
    for (const mo of moments) {
      for (const md of (mo.medias || [])) {
        if (md.media_type !== 2) { thumbs.push(md.url); if (thumbs.length >= 4) break; }
      }
      if (thumbs.length >= 4) break;
    }
    const momentsHtml = moments.length ? '<div class="up-moments" data-upmom><div class="up-label">近期动态 · ' + moments.length + '</div>' +
      (thumbs.length ? '<div class="up-thumbs">' + thumbs.map(t => mediaImgHtml(t, 400, '')).join('') + '</div>' : '<div class="up-empty">暂无图片内容</div>') + '</div>' : '';
    const sigHtml = u.is_friend ? '<div class="up-sig-row"><span class="up-label">个性签名</span>' + esc(u.signature || '未设置') + '</div>' : '';
    const genderLabel = u.gender === 1 ? '男' : (u.gender === 2 ? '女' : '未知');
    const actions = '<div class="up-actions">' +
      '<button class="btn-primary" data-upm type="button">发消息</button>' +
      (u.is_friend ? '<button class="btn-ghost" disabled type="button">已是好友</button>'
        : u.request_pending ? '<button class="btn-ghost" disabled type="button">申请已发出</button>'
        : '<button class="btn-ghost" data-ufr type="button">加好友</button>') +
      '<button class="btn-ghost danger-red" data-upblk type="button">' + (isBlocked('user', u.id) ? '取消拉黑' : '拉黑此人') + '</button>' +
      '</div>';
    const pop = document.createElement('div');
    pop.id = 'userPop'; pop.className = 'user-pop'; pop.dataset.uid = uid;
    pop.innerHTML = '<div class="up-card">' +
      '<div class="up-head"><img class="up-ava" src="' + esc(u.avatar || letterAvatar(u.remark || u.nickname)) + '" alt="">' +
      '<div class="up-meta"><div class="up-name">' + esc(u.remark || u.nickname) + '</div>' +
      (u.remark ? '<div class="up-real">昵称：' + esc(u.nickname) + '</div>' : '') +
      '<div class="up-pres"><span class="pres-dot' + (u.online ? ' on' : '') + '"></span>' + esc(u.presence_text || '') + '</div>' +
      '<div class="up-num">Ping号: ' + esc(stripNum(u.number) || '—') + ' · 性别: ' + genderLabel + '</div></div></div>' +
      momentsHtml + sigHtml + actions + '</div>';
    document.body.appendChild(pop);
    // 定位：默认贴锚点右侧，放不下翻左侧，再夹到视口内
    positionPop(pop, anchor);
    requestAnimationFrame(() => pop.classList.add('show'));
    userPopEl = pop;
    const pm = pop.querySelector('[data-upm]'), fr = pop.querySelector('[data-ufr]'), mm = pop.querySelector('[data-upmom]');
    const bl = pop.querySelector('[data-upblk]');
    if (bl) bl.addEventListener('click', () => {
      closeUserPop();
      if (isBlocked('user', u.id)) doUnblock('user', u.id);
      else doBlock('user', u.id, u.nickname);
    });
    if (pm) pm.addEventListener('click', () => {
      closeUserPop();
      openConversation({ conv_type: 'private', target_id: u.id, name: u.nickname, avatar: u.avatar || '', updated_at: new Date().toISOString() });
    });
    if (mm) mm.addEventListener('click', () => { closeUserPop(); openUserMoments(u.id, u.nickname); });
    if (fr) fr.addEventListener('click', async () => {
      fr.disabled = true;
      const rr = await api('/friend/request', { to_user_id: u.id, message: '你好，加个好友吧' });
      if (rr.code === 0) { fr.textContent = '申请已发出'; toast('好友申请已发送'); }
      else { fr.disabled = false; toast(rr.msg || '操作失败'); }
    });
  }
  /* 全屏资料页：点击私聊头部进入。基础信息（封面/头像/昵称/Ping号/签名/性别）始终展示；
     “他的动态”区块由对方 profile_visibility 控制，不可见时展示锁定提示（邮箱仅本人/好友可见） */
  function closeProfileView() {
    if (!profileViewEl) return;
    const el = profileViewEl; profileViewEl = null;
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    // 收起：整页向右平移移出屏幕（清掉打开时的光圈残留，确保是整页）
    if (!reduce && el.animate) {
      el.style.clipPath = 'none';
      const anim = el.animate(
        [{ transform: 'translateX(0)' }, { transform: 'translateX(100%)' }],
        { duration: 340, easing: 'cubic-bezier(.4,0,.2,1)', fill: 'forwards' });
      anim.onfinish = anim.oncancel = () => el.remove();
    } else {
      el.remove();
    }
  }
  async function openProfileView(uid, origin) {
    if (!uid) return;
    if (profileViewEl && +profileViewEl.dataset.uid === uid) return;
    closeUserPop(); closePeek();
    if (profileViewEl) { profileViewEl.remove(); profileViewEl = null; }
    const overlay = document.createElement('div');
    overlay.className = 'profile-view'; overlay.id = 'profileView'; overlay.dataset.uid = uid;
    overlay.innerHTML =
      '<div class="pv-bar"><button class="pv-back" id="pvBack" type="button"><span class="chev"></span></button></div>' +
      '<div class="pv-body"><div class="loading-hint" style="padding:40px 0">加载中…</div></div>';
    // 圆形“光圈”揭示：以点击点头像为圆心，半径盖到屏幕最远角，从 0 放大直至覆盖全屏（同主题切换丝滑观感）
    const cx = origin && origin.x != null ? origin.x : Math.round(window.innerWidth / 2);
    const cy = origin && origin.y != null ? origin.y : 60;
    const radius = Math.hypot(Math.max(cx, window.innerWidth - cx), Math.max(cy, window.innerHeight - cy)) + 20;
    overlay._origin = { x: cx, y: cy, r: radius };
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const canAnim = !reduce && !!overlay.animate;
    if (canAnim) overlay.style.clipPath = 'circle(0px at ' + cx + 'px ' + cy + 'px)'; // 先藏，避免首帧闪现
    document.body.appendChild(overlay);
    profileViewEl = overlay;
    if (canAnim) {
      const anim = overlay.animate(
        [{ clipPath: 'circle(0px at ' + cx + 'px ' + cy + 'px)' },
         { clipPath: 'circle(' + radius + 'px at ' + cx + 'px ' + cy + 'px)' }],
        { duration: 520, easing: 'cubic-bezier(.32,.72,.24,1)', fill: 'forwards' });
      anim.onfinish = () => { overlay.style.clipPath = 'none'; };
    }
    $('#pvBack').addEventListener('click', closeProfileView);
    let r;
    try { r = await api('/user/profile/' + uid); }
    catch (e) { if (profileViewEl === overlay) $('.pv-body', overlay).innerHTML = '<div class="empty-hint">获取资料失败</div>'; return; }
    if (r.code !== 0) { if (profileViewEl === overlay) $('.pv-body', overlay).innerHTML = '<div class="empty-hint">' + esc(r.msg || '获取失败') + '</div>'; return; }
    if (profileViewEl !== overlay) return; // 已关闭
    const u = r.data;
    const genderLabel = u.gender === 1 ? '男' : (u.gender === 2 ? '女' : '未知');
    const hasBg = !!u.background;
    const displayName = u.remark || u.nickname;
    // 基础信息（图3）永远展示；邮箱仅在本人/好友时非空
    const infoRows =
      (u.email ? '<div class="p-row"><span class="k">邮箱</span><span class="v">' + esc(u.email) + '</span></div>' : '') +
      '<div class="p-row"><span class="k">签名</span><span class="v">' + esc(u.signature || '未设置') + '</span></div>' +
      '<div class="p-row"><span class="k">性别</span><span class="v">' + esc(genderLabel) + '</span></div>';
    // 操作按钮区（备注 / 添加·删除好友 / 从列表移除会话 / 拉黑·取消拉黑）改由 renderActs() 动态渲染，见下
    // 动态区块：可见则加载动态；不可见则锁定提示（文案随 profile_visibility 变）
    let momentsHtml;
    if (u.moments_visible) {
      momentsHtml = '<div class="pf-moments"><div class="pf-moments-head"><i class="ic ic-tab-moments"></i><span>他的动态</span></div>' +
        '<div id="pvMomentsFeed"><div class="loading-hint">加载中…</div></div></div>';
    } else {
      const lockTip = u.profile_visibility === 3 ? '对方设置了动态仅自己可见' : '对方动态仅好友可见';
      momentsHtml = '<div class="pv-locked"><i class="ic ic-lock"></i><span>' + lockTip + '</span></div>';
    }
    const body = $('.pv-body', overlay);
    body.innerHTML =
      '<div class="profile-hero' + (hasBg ? ' has-bg' : '') + '" id="pvHero">' +
      '<div class="profile-idbox"><div class="profile-name">' + esc(displayName) + '</div>' +
      '<div class="profile-num">Ping号: ' + esc(stripNum(u.number) || '—') + '</div>' +
      '<div class="pv-pres"><span class="pres-dot' + (u.online ? ' on' : '') + '"></span>' + esc(u.presence_text || '') + '</div></div>' +
      '<img class="profile-avatar" id="pvAva" src="' + esc(u.avatar || letterAvatar(displayName)) + '" alt=""></div>' +
      '<div class="profile-card">' + infoRows + '</div>' + '<div class="up-actions" id="pvActs"></div>' + momentsHtml;
    if (hasBg) $('.profile-hero', body).style.backgroundImage = 'linear-gradient(180deg,rgba(10,20,30,.08),rgba(10,20,30,.42)),url("' + u.background + '")';
    const ava = $('#pvAva'); watchAvatar(ava, displayName);
    ava.addEventListener('click', () => openAvatarViewer(fullSrc(ava), ava.getBoundingClientRect(), true, displayName));
    // 操作区：备注 / 添加·删除好友 / 从列表移除会话 / 拉黑·取消拉黑（弹窗均已在资料页之上，不再被遮挡）
    const isCurConv = !!(S.current && S.current.type === 'private' && +S.current.id === +u.id);
    const st = { isFriend: !!u.is_friend, pending: !!u.request_pending, blocked: isBlocked('user', u.id) };
    const actsEl = $('#pvActs', overlay);
    function renderActs() {
      if (!actsEl) return;
      let frBtn;
      if (st.isFriend) frBtn = '<button class="btn-ghost danger-red" data-pvdelfr type="button">删除好友</button>';
      else if (st.pending) frBtn = '<button class="btn-ghost" disabled type="button">申请已发出</button>';
      else frBtn = '<button class="btn-ghost" data-pvfr type="button">添加好友</button>';
      actsEl.innerHTML =
        '<button class="btn-primary" data-pvrmk type="button">备注</button>' + frBtn +
        (isCurConv ? '<button class="btn-ghost" data-pvcvdel type="button">从列表移除会话</button>' : '') +
        '<button class="btn-ghost danger-red" data-pvblk type="button">' + (st.blocked ? '取消拉黑' : '拉黑此人') + '</button>';
      const rmk = $('[data-pvrmk]', actsEl), addf = $('[data-pvfr]', actsEl),
        delf = $('[data-pvdelfr]', actsEl), cvdel = $('[data-pvcvdel]', actsEl), blk = $('[data-pvblk]', actsEl);
      if (rmk) rmk.addEventListener('click', () => openRemarkEditor(u.id, S.remarks.get(+u.id) || '', () => {
        const nEl = $('.profile-name', overlay); if (nEl) nEl.textContent = S.remarks.get(+u.id) || u.nickname;
      }));
      if (addf) addf.addEventListener('click', async () => {
        addf.disabled = true;
        const rr = await api('/friend/request', { to_user_id: u.id, message: '你好，加个好友吧' });
        if (rr.code === 0) { st.pending = true; renderActs(); toast('好友申请已发送'); }
        else { addf.disabled = false; toast(rr.msg || '操作失败'); }
      });
      if (delf) delf.addEventListener('click', () => {
        openModal('删除好友',
          '<div class="modal-hint" style="margin:0;line-height:1.6">确定删除好友 <b>' + esc(dispName(u.id, u.nickname)) + '</b>？删除后将从通讯录移除，聊天中的对方消息一并清除。</div>',
          async () => {
            const rr = await api('/friend/delete', { friend_id: u.id });
            if (rr.code === 0) { closeModal(); toast('已删除好友'); st.isFriend = false; st.pending = false; if (S.tab === 'contacts') renderContacts(); renderActs(); }
            else toast(rr.msg || '删除失败');
          }, '确认删除', true);
      });
      if (cvdel) cvdel.addEventListener('click', async () => {
        const rr = await api('/chat/conversation/delete', { to_user_id: u.id });
        if (rr.code === 0) {
          closeProfileView();
          const el = $('[data-key="' + convKey('private', u.id) + '"]', convListEl);
          if (el && window.PARTICLES) { SFX.delete && SFX.delete(); PARTICLES.dissolve(el); }
          else if (el) el.remove();
          S.current = null; panelHide();
          reloadConversations();
        } else toast(rr.msg || '删除失败');
      });
      if (blk) blk.addEventListener('click', () => {
        if (st.blocked) doUnblock('user', u.id, () => { st.blocked = false; renderActs(); });
        else doBlock('user', u.id, u.nickname, () => { st.blocked = true; renderActs(); });
      });
    }
    renderActs();
    if (u.moments_visible) {
      const mr = await api('/moment/user/' + uid).catch(() => null);
      const feed = $('#pvMomentsFeed', overlay);
      if (!feed) return;
      if (!mr || mr.code !== 0) { feed.innerHTML = '<div class="empty-hint">动态加载失败</div>'; return; }
      const list = (mr.data && mr.data.moments) || [];
      feed.innerHTML = '';
      if (!list.length) { feed.innerHTML = '<div class="empty-hint">还没有可见的动态</div>'; return; }
      list.forEach(m => feed.appendChild(momentCardHtml(m)));
      bindMomentEvents(feed);
    }
  }
  // 群/频道预览卡：点击搜索结果名称弹出，展示简介/人数 + echarts 性别构成饼图
  async function openPeek(kind, id, anchor) {
    if (peekEl && +peekEl.dataset.id === id && peekEl.dataset.kind === kind) { closePeek(); return; }
    closeUserPop(); closePeek();
    const url = kind === 'group' ? '/group/' + id + '/peek' : '/channel/' + id + '/peek';
    let r;
    try { r = await api(url); }
    catch (err) { toast('获取信息失败'); return; }
    if (!r || r.code !== 0) { toast((r && r.msg) || '获取失败'); return; }
    const d = r.data;
    const g = d.gender || { unknown: 0, male: 0, female: 0 };
    const total = (g.male || 0) + (g.female || 0) + (g.unknown || 0);
    const countLabel = kind === 'group' ? ((d.member_count || 0) + ' 名成员') : ((d.subscriber_count || 0) + ' 订阅者');
    const chartHtml = total > 0
      ? '<div class="peek-chart"></div>'
      : '<div class="up-empty">暂无成员数据</div>';
    const pop = document.createElement('div');
    pop.className = 'peek-pop'; pop.dataset.id = id; pop.dataset.kind = kind;
    pop.innerHTML = '<div class="peek-card">' +
      '<div class="up-head"><img class="up-ava" src="' + esc(d.avatar || letterAvatar(d.name)) + '" alt="">' +
      '<div class="up-meta"><div class="up-name">' + esc(d.name) + '</div>' +
      '<div class="up-num">' + (esc(stripNum(d.number) || '—') + ' · ' + countLabel) + '</div></div></div>' +
      '<div class="peek-desc">' + esc(d.description || '暂无简介') + '</div>' +
      '<div class="peek-glabel">成员性别构成</div>' + chartHtml +
      '<div class="up-actions"><button class="btn-primary" data-peekgo type="button"' + (kind === 'group' && !d.joined && d.pending ? ' disabled style="opacity:.6;cursor:default"' : '') + '>' + (d.joined ? '进入' : (kind === 'group' ? (d.pending ? '申请中' : '申请加入') : '进入')) + '</button></div>' +
      '</div>';
    document.body.appendChild(pop);
    positionPop(pop, anchor);
    requestAnimationFrame(() => pop.classList.add('show'));
    peekEl = pop;
    if (total > 0) {
      const holder = pop.querySelector('.peek-chart');
      // 首次用到才下载 echarts，加载完再画图；期间卡片被关闭则放弃
      loadEcharts().then(() => {
        if (peekEl !== pop || !holder.isConnected) return;
        const chart = echarts.init(holder);
        pop._chart = chart;
        chart.setOption({
          tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
          legend: { bottom: 0, icon: 'circle', textStyle: { color: 'var(--text-2, #bbb)', fontSize: 12 } },
          series: [{
            type: 'pie', radius: ['46%', '70%'], center: ['50%', '44%'], avoidLabelOverlap: true,
            label: { show: true, formatter: '{b} {c}', color: 'inherit' }, labelLine: { length: 6, length2: 6 },
            data: [
              { value: g.male, name: '男', itemStyle: { color: '#4c8dff' } },
              { value: g.female, name: '女', itemStyle: { color: '#ff6b9d' } },
              { value: g.unknown, name: '未知', itemStyle: { color: '#8a8f98' } }
            ].filter(x => x.value > 0)
          }]
        });
      }).catch(() => {
        if (peekEl === pop && holder.isConnected) holder.outerHTML = '<div class="up-empty">图表库加载失败</div>';
      });
    }
    const go = pop.querySelector('[data-peekgo]');
    if (go) go.addEventListener('click', async () => {
      if (kind === 'group' && !d.joined) {
        go.disabled = true;
        const j = await api('/group/join', { group_id: id });
        if (j.code !== 0 && j.code !== 200) { go.disabled = false; toast(j.msg || '申请失败'); return; }
        if (j.data && j.data.already) {
          closePeek(); await reloadConversations();
          const c = S.convs.get(convKey('group', id));
          openConversation(c || { conv_type: 'group', target_id: id, name: d.name, avatar: d.avatar || '', updated_at: new Date().toISOString() });
          return;
        }
        toast(j.msg || '入群申请已发送，等待审核');
        go.textContent = '申请中'; go.disabled = true; go.style.opacity = '.6'; go.style.cursor = 'default';
        return;
      }
      // 频道：未订阅也直接查看（不强制 join，加入与否由频道内底部“加入频道”按钮决定）
      closePeek();
      await reloadConversations();
      const stored = S.convs.get(convKey(kind, id));
      openConversation(stored || { conv_type: kind, target_id: id, name: d.name, avatar: d.avatar || '', updated_at: new Date().toISOString() });
    });
  }
  function openMomentPublish() {
    momVis = 1;
    openModal('发布动态',
      '<div class="field"><textarea id="momContent" class="modal-input" rows="4" placeholder="分享此刻…" style="resize:vertical"></textarea></div>' +
      '<div class="pub-vis" id="momVis"><span class="chip on" data-v="1">公开</span><span class="chip" data-v="2">好友可见</span><span class="chip" data-v="3">仅自己</span></div>' +
      '<div class="pub-files"><input type="file" id="momFiles" accept="image/*,video/*" multiple hidden><button class="mini-btn blue" id="momFilesBtn" type="button">选择图片/视频</button></div>' +
      '<div class="pub-thumbs" id="momThumbs"></div>',
      momentPublishSubmit, '发布');
    $$('#momVis .chip').forEach(ch => ch.addEventListener('click', () => { momVis = +ch.dataset.v; $$('#momVis .chip').forEach(c => c.classList.toggle('on', c === ch)); }));
    $('#momFilesBtn').addEventListener('click', () => $('#momFiles').click());
    $('#momFiles').addEventListener('change', () => { const fs = $('#momFiles').files; $('#momThumbs').innerHTML = Array.from(fs).slice(0, 9).map(f => /^image\//.test(f.type) ? '<img src="' + URL.createObjectURL(f) + '">' : fileIcHtml(f.name, 'width:60px;height:60px')).join(''); });
  }
  async function momentPublishSubmit() {
    const content = $('#momContent').value.trim();
    const fs = $('#momFiles') ? $('#momFiles').files : [];
    if (!content && !fs.length) { toast('内容不能为空'); return; }
    const fd = new FormData(); fd.append('content', content); fd.append('visibility', momVis);
    for (const f of fs) fd.append('files', f);
    showLoading('发布中…');
    try {
      const res = await fetch('/moment/publish', { method: 'POST', body: fd, credentials: 'same-origin' });
      const r = await res.json();
      if (r.code === 0) { closeModal(); toast('发布成功'); renderMoments(); }
      else toast(r.msg || '发布失败');
    } catch (e) { toast('发布失败'); }
    finally { hideLoading(); }
  }

  /* ---------------- 搜索浮层 ---------------- */
  const searchInput = $('#searchInput');
  let searchWrap = null, searchTimer = null;
  function ensureSearchWrap() {
    if (searchWrap) return searchWrap;
    searchWrap = document.createElement('div');
    searchWrap.className = 'search-results';
    $('.sidebar').appendChild(searchWrap);
    return searchWrap;
  }
  function closeSearch() { if (searchWrap) searchWrap.classList.remove('show'); }

  function srRow(html, onClick) {
    const el = document.createElement('div');
    el.className = 'sr-item';
    el.innerHTML = html;
    el.addEventListener('click', onClick);
    return el;
  }

  // 关键字标亮：先转义再包 <mark>，对纯中文/字母数字安全（HTML 实体不参与匹配）
  function hl(text, q) {
    const t = esc(text || ''), k = esc(q || '');
    if (!k) return t;
    try { return t.replace(new RegExp(k.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi'), m => '<mark class="sr-hl">' + m + '</mark>'); }
    catch (e) { return t; }
  }

  async function runSearch(q) {
    const wrap = ensureSearchWrap();
    wrap.classList.add('show');
    wrap.innerHTML = '<div class="sr-empty">搜索中…</div>';
    try {
      if (/^(user|group|channel)_.+/.test(q)) {
        const r = await api('/search/number', { search_number: q });
        wrap.innerHTML = '<div class="sr-group-title">编号 · ' + esc(q) + '</div>';
        if (r.code !== 0) { wrap.innerHTML = '<div class="sr-empty">' + esc(r.msg) + '</div>'; return; }
        const d = r.data;
        if (d.to_user_id) wrap.appendChild(srRow('<div class="sr-t">' + esc(d.nickname) + '</div><div class="sr-s">用户 · 点击发起会话</div>', () => { closeSearch(); openConversation({ conv_type: 'private', target_id: d.to_user_id, name: d.nickname, avatar: d.avatar }); }));
        if (d.to_group_id) wrap.appendChild(srRow('<div class="sr-t">' + esc(d.name) + '</div><div class="sr-s">群组 · 点击申请加入</div>', async () => { closeSearch(); const j = await api('/group/join', { group_id: d.to_group_id }).catch(() => null); if (!j || (j.code !== 0 && j.code !== 200)) { toast((j && j.msg) || '操作失败'); return; } if (j.data && j.data.already) { await reloadConversations(); openConversation({ conv_type: 'group', target_id: d.to_group_id, name: d.name, avatar: d.avatar }); } else { toast(j.msg || '入群申请已发送，等待审核'); } }));
        if (d.to_channel_id) wrap.appendChild(srRow('<div class="sr-t">' + esc(d.name) + '</div><div class="sr-s">频道 · 点击查看</div>', () => { closeSearch(); openConversation({ conv_type: 'channel', target_id: d.to_channel_id, name: d.name, avatar: d.avatar }); }));
      } else {
        const r = await api('/search/content', { keyword: q, limit: 30 });
        wrap.innerHTML = '';
        const res = r.data && r.data.results;
        if (!res) { wrap.innerHTML = '<div class="sr-empty">' + esc(r.msg || '无结果') + '</div>'; return; }
        const sec = (title, arr, render) => {
          if (!arr || !arr.length) return;
          const h = document.createElement('div'); h.className = 'sr-group-title'; h.textContent = title; wrap.appendChild(h);
          arr.slice(0, 8).forEach(render);
        };
        // 私聊/群聊：文本直接展示 content，文件展示后端拼接的 "[文件] 名称"(display)，点击后打开会话并定位到该消息气泡
        sec('私聊', res.private, x => wrap.appendChild(srRow('<div class="sr-t">' + hl(x.display || x.content, q) + '</div><div class="sr-s">' + esc(x.sender_nickname) + ' · ' + esc(x.created_at) + '</div>', () => { closeSearch(); const c = S.convs.get(convKey('private', x.counterpart_id)); openConversation(c || { conv_type: 'private', target_id: x.counterpart_id, name: x.counterpart_id === x.sender_id ? x.sender_nickname : 'TA', avatar: null }); setTimeout(() => locateMsg(x.message_id), 600); })));
        sec('群聊', res.group, x => wrap.appendChild(srRow('<div class="sr-t">' + hl(x.display || x.content, q) + '</div><div class="sr-s">' + esc(x.group_name) + ' · ' + esc(x.sender_nickname) + '</div>', () => { closeSearch(); const c = S.convs.get(convKey('group', x.group_id)); if (c) openConversation(c); else reloadConversations(); setTimeout(() => locateMsg(x.message_id), 600); })));
        sec('频道帖子', res.channel, x => wrap.appendChild(srRow('<div class="sr-t">' + hl(x.title || x.content, q) + '</div><div class="sr-s">' + esc(x.channel_name) + ' · ' + esc(x.created_at) + '</div>', () => { closeSearch(); const c = S.convs.get(convKey('channel', x.channel_id)); if (c) openConversation(c); else reloadConversations(); setTimeout(() => openPostDetail(x.post_id), 600); })));
        if (!wrap.children.length) wrap.innerHTML = '<div class="sr-empty">没有找到相关内容</div>';
      }
    } catch (e) { wrap.innerHTML = '<div class="sr-empty">搜索失败</div>'; }
  }

  searchInput.addEventListener('input', () => {
    clearTimeout(searchTimer);
    const q = searchInput.value.trim();
    if (!q) { closeSearch(); return; }
    searchTimer = setTimeout(() => runSearch(q), 400);
  });
  searchInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') { clearTimeout(searchTimer); runSearch(searchInput.value.trim()); } if (e.key === 'Escape') { searchInput.value = ''; closeSearch(); searchInput.blur(); } });
  document.addEventListener('click', (e) => { if (searchWrap && !searchWrap.contains(e.target) && e.target !== searchInput) closeSearch(); });

  /* ---------------- 会话内搜索：点放大镜，左侧胶囊原地展开为搜索框，结果下拉在头部下方 ---------------- */
  let csTimer = null, csResults = null;
  const chatIdEl = $('#chatId'), csInput = $('#chatSearchInput'), csCount = $('#chatSearchCount');
  function ensureCsResults() {
    if (csResults && csResults.isConnected) return csResults;
    csResults = document.createElement('div');
    csResults.className = 'cs-list';
    $('.chat-panel').appendChild(csResults);
    return csResults;
  }
  function positionCsResults() {
    if (csResults) csResults.style.top = ($('.chat-head').offsetHeight + 6) + 'px';
  }
  function convSearchIsOpen() { return !!chatIdEl && chatIdEl.classList.contains('searching'); }
  function openConvSearch() {
    if (!S.current) { toast('先打开一个会话'); return; }
    chatIdEl.classList.add('searching');
    ensureCsResults(); positionCsResults();
    if (csInput.value.trim()) runConvSearch(csInput.value.trim());
    requestAnimationFrame(() => csInput.focus());
  }
  function closeConvSearch() {
    if (!chatIdEl) return;
    chatIdEl.classList.remove('searching');
    if (csInput) { csInput.value = ''; csInput.blur(); }
    if (csCount) csCount.textContent = '';
    if (csResults) csResults.classList.remove('show');
  }
  function toggleConvSearch() { convSearchIsOpen() ? closeConvSearch() : openConvSearch(); }
  if (csInput) {
    csInput.addEventListener('input', () => {
      clearTimeout(csTimer);
      csTimer = setTimeout(() => runConvSearch(csInput.value.trim()), 300);
    });
    csInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { clearTimeout(csTimer); runConvSearch(csInput.value.trim()); }
      if (e.key === 'Escape') closeConvSearch();
    });
  }
  // 点别处收回（放大镜、胶囊自身与结果列表除外）
  document.addEventListener('mousedown', (e) => {
    if (convSearchIsOpen() && !e.target.closest('.chat-id') && !e.target.closest('#chatSearchBtn') && !(csResults && csResults.contains(e.target))) closeConvSearch();
  }, true);
  function locateMsg(mid) {
    const el = $('[data-id="' + mid + '"]', msgListEl);
    if (!el) { toast('该消息较久远，向上滚动加载后可再试'); return; }
    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    el.classList.remove('sr-flash'); void el.offsetWidth; el.classList.add('sr-flash');
  }
  async function runConvSearch(q) {
    if (!S.current) return;
    const list = ensureCsResults(); positionCsResults();
    if (!q) { list.innerHTML = ''; list.classList.remove('show'); if (csCount) csCount.textContent = ''; return; }
    list.innerHTML = '<div class="sr-empty">搜索中…</div>';
    list.classList.add('show');
    try {
      const r = await api('/search/content', { keyword: q, conv: { type: S.current.type, id: S.current.id }, limit: 50 });
      if (r.code !== 0) { list.innerHTML = '<div class="sr-empty">' + esc(r.msg || '搜索失败') + '</div>'; if (csCount) csCount.textContent = ''; return; }
      const arr = (r.data.results || {})[S.current.type] || [];
      if (csCount) csCount.textContent = arr.length ? arr.length + ' 条' : '';
      list.innerHTML = '';
      if (!arr.length) { list.innerHTML = '<div class="sr-empty">本会话没有匹配内容</div>'; return; }
      arr.forEach(x => {
        const isPost = S.current.type === 'channel';
        const mid = isPost ? x.post_id : x.message_id;
        const line = isPost ? hl((x.title ? x.title + '  ' : '') + (x.content || ''), q) : hl(x.display || x.content, q);
        const sub = isPost ? esc(fmtRelTime(x.created_at)) : esc(x.sender_nickname) + ' · ' + esc(x.created_at);
        list.appendChild(srRow('<div class="sr-t">' + line + '</div><div class="sr-s">' + sub + '</div>', () => locateMsg(mid)));
      });
    } catch (e) { list.innerHTML = '<div class="sr-empty">搜索失败</div>'; }
  }

  /* ---------------- 群聊/频道公告：头部下方横幅 → 点击弹窗 → “我已知晓”消失 ---------------- */
  function removeAnnounceBar() { const old = $('#announceBar'); if (old) old.remove(); }
  // 知晓后播退场动画（上浮+缩小+淡出）再移除，避免横幅瞬间消失的突兀感
  function dismissAnnounceBar(bar) {
    if (!bar || bar.classList.contains('out')) return;
    bar.classList.add('out');
    setTimeout(() => bar.remove(), 320);
  }
  async function refreshAnnounceBar() {
    removeAnnounceBar();
    if (!S.current || (S.current.type !== 'group' && S.current.type !== 'channel')) return;
    const isGroup = S.current.type === 'group';
    const q = isGroup ? { group_id: S.current.id } : { channel_id: S.current.id };
    const r = await api(isGroup ? '/group/announcement/latest' : '/channel/announcement/latest', q).catch(() => null);
    if (!r || r.code !== 0 || !r.data) return;
    const d = r.data;
    const bar = document.createElement('div');
    bar.className = 'announce-bar'; bar.id = 'announceBar';
    bar.innerHTML = '<i class="ic ic-announce"></i><span class="ab-text">' + esc((d.title ? d.title + '：' : '') + d.content) + '</span><span class="ab-hint">查看公告 ›</span>';
    bar.addEventListener('click', () => {
      openModal(isGroup ? '群公告' : '频道公告',
        (d.title ? '<div class="ann-title">' + esc(d.title) + '</div>' : '') +
        '<div class="ann-full">' + esc(d.content) + '</div>' +
        '<div class="modal-hint">' + esc(d.publisher_nickname) + ' 发布于 ' + esc(d.created_at) + '</div>',
        async () => {
          const ackQ = Object.assign({ announcement_id: d.announcement_id }, q);
          const ack = await api(isGroup ? '/group/announcement/get' : '/channel/announcement/get', ackQ).catch(() => null);
          if (ack && ack.code === 0) { closeModal(); dismissAnnounceBar(bar); toast('已知晓'); }
          else toast((ack && ack.msg) || '操作失败');
        }, '我已知晓');
    });
    $('.chat-head').insertAdjacentElement('afterend', bar);
  }
  // 管理员发布公告（群：群主/管理员；频道：频道主/副频道主）
  function openAnnouncePublish() {
    if (!S.current) return;
    const isGroup = S.current.type === 'group';
    openModal(isGroup ? '发布群公告' : '发布频道公告',
      (isGroup ? '' : '<div class="field"><input id="annTitle" class="modal-input" placeholder=" " autocomplete="off"></div><div class="modal-hint" style="margin:-14px 0 6px">标题（可选）</div>') +
      '<div class="field"><textarea id="annContent" class="modal-input" rows="5" placeholder="公告内容…" style="resize:vertical"></textarea></div>' +
      '<div class="modal-hint" style="margin:-14px 0 0">成员打开会话时会看到公告横幅，点击“我已知晓”后消失。</div>',
      async () => {
        const content = $('#annContent').value.trim();
        if (!content) { toast('请输入公告内容'); return; }
        const body = isGroup ? { group_id: S.current.id, content } : { channel_id: S.current.id, title: ($('#annTitle') ? $('#annTitle').value.trim() : ''), content };
        const r = await api(isGroup ? '/group/publish/announcement' : '/channel/publish/announcement', body);
        if (r.code === 0) { closeModal(); toast('公告已发布'); refreshAnnounceBar(); }
        else toast(r.msg || '发布失败');
      }, '发布');
  }

  /* ---------------- 输入区交互 ---------------- */
  const msgInput = $('#msgInput');
  msgInput.addEventListener('input', () => { autoresize(msgInput); updateMentionPop(); });
  msgInput.addEventListener('keyup', (e) => { if (['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].indexOf(e.key) >= 0) updateMentionPop(); });
  msgInput.addEventListener('blur', () => setTimeout(hideMentionPop, 150));
  msgInput.addEventListener('keydown', (e) => {
    const popShown = mentionPop && mentionPop.classList.contains('show');
    if (e.key === 'ArrowUp' && popShown) { e.preventDefault(); moveMention(-1); return; }
    if (e.key === 'ArrowDown' && popShown) { e.preventDefault(); moveMention(1); return; }
    if (e.key === 'Escape' && popShown) { hideMentionPop(); return; }
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      if (popShown) { pickMention(mentionIndex); return; }
      sendText();
    }
  });
  $('#sendBtn').addEventListener('click', sendText);
  const postText = $('#postText');
  postText.addEventListener('input', () => autoresize(postText));
  postText.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendComment(); } });
  $('#postSendBtn').addEventListener('click', sendComment);
  $('#publishPostBtn').addEventListener('click', openPublishModal);
  // 未订阅者：加入频道（订阅后底部栏切为铃铛态，帖子流重新拉取）
  $('#channelJoinBtn').addEventListener('click', async () => {
    if (!S.current || S.current.type !== 'channel') return;
    const r = await api('/channel/join', { channel_id: S.current.id });
    if (r.code === 0) {
      toast('已订阅频道');
      S.current.role = 3; S.current.notify = 1;
      await reloadConversations();
      if (S.channelView === 'detail') { setChannelBottom('comment'); }  // 在帖子详情页加入：就地解锁评论框，不跳回 feed
      else { setChannelBottom('feed'); loadChannel(S.current.id); }
    } else toast(r.msg || '订阅失败');
  });
  // 通知按钮：一键互切 正常接收(1) ⇄ 静音(3)
  $('#attachBtn').addEventListener('click', () => $('#fileInput').click());
  $('#fileInput').addEventListener('change', (e) => {
    const fs = Array.from(e.target.files || []);
    if (fs.length) uploadFiles(fs);
    e.target.value = '';
  });
  $('#backBtn').addEventListener('click', () => {
    // 左上角返回键统一处理：信息页→回聊天；帖子详情→回帖子列表；否则关闭整个会话
    if (panelEl.classList.contains('info-open')) { hideConvInfo(); return; }
    if (S.current && S.current.type === 'channel' && S.channelView === 'detail') { backToChannelFeed(); return; }
    closeChat();
  });
  $('#chatInfoBtn').addEventListener('click', toggleConvInfo);
  // 头像点击放大：本人抽屉头像 / 当前会话头部 / 消息与帖子图片
  $('#drawerAvatar').addEventListener('click', (e) => {
    const r = e.target.getBoundingClientRect();
    hideDrawer();
    openAvatarViewer(fullSrc(e.target), r, true, S.me ? S.me.nickname : '');
  });
  // 点头部进入相关页面：私聊→全屏资料页；频道→全屏媒体画廊；群聊→内嵌群资料页（卡片+成员+群文件）
  $('#chatAvatar').addEventListener('click', (e) => {
    if (!S.current) return;
    e.stopPropagation();
    if (S.current.type === 'private') { const r = e.currentTarget.getBoundingClientRect(); openProfileView(S.current.id, { x: r.left + r.width / 2, y: r.top + r.height / 2 }); return; }
    if (S.current.type === 'channel') { const r = e.currentTarget.getBoundingClientRect(); openMediaView('channel', S.current.id, { x: r.left + r.width / 2, y: r.top + r.height / 2 }); return; }
    if (S.current.type === 'group') { showGroupInfo(); return; }
    const el = $('#chatAvatar');
    openAvatarViewer(fullSrc(el), el.getBoundingClientRect(), true, S.current.name);
  });
  // 点击头部名称区域（非搜索态、非点搜索框本身）：私聊进资料页、频道进媒体画廊、群聊进群资料页
  $('#chatId').addEventListener('click', (e) => {
    if (!S.current) return;
    if (convSearchIsOpen() || e.target.closest('.chat-search-field')) return;
    if (S.current.type === 'private') { const r = $('#chatAvatar').getBoundingClientRect(); openProfileView(S.current.id, { x: r.left + r.width / 2, y: r.top + r.height / 2 }); return; }
    if (S.current.type === 'channel') { const r = $('#chatAvatar').getBoundingClientRect(); openMediaView('channel', S.current.id, { x: r.left + r.width / 2, y: r.top + r.height / 2 }); return; }
    if (S.current.type === 'group') { showGroupInfo(); }
  });
  msgListEl.addEventListener('click', (e) => {
    const fwd = e.target.closest('.fwd-card');
    if (fwd) { e.stopPropagation();
      if (fwd.classList.contains('fwd-share-card')) openSharedChannel({ id: +fwd.dataset.fwdCh, name: fwd.dataset.chName, avatar: fwd.dataset.chAvatar, desc: fwd.dataset.chDesc });
      else jumpToChannelPost(fwd.dataset.fwdCh, fwd.dataset.fwdPost);
      return; }
    const cell = e.target.closest('.tg-cell');
    if (cell) {
      e.stopPropagation();
      if (cell.dataset.tgimg) {
        const img = cell.querySelector('img'); if (!img) return;
        // 同一图集（同一 .tg-media 内的所有图片）可左右切换
        const group = Array.from(cell.parentElement.querySelectorAll('.tg-cell[data-tgimg]'));
        // 图集优先用容器上的原始地址列表，被“+N”隐掉的几张也能翻到
        openAvatarViewer(cell.dataset.tgimg, img.getBoundingClientRect(), false, '', tgGallery(cell), group.indexOf(cell));
      }
      else if (cell.dataset.tgvid) { openVideoViewer(cell.dataset.tgvid); }
      return;
    }
    const im = e.target.closest('img.media, .post-files img');
    if (!im) return;
    e.stopPropagation();
    openAvatarViewer(fullSrc(im), im.getBoundingClientRect(), false, '');
  });
  $('#chatSearchBtn').addEventListener('click', () => { toggleConvSearch(); });

  // 滚到底按钮 + 上滑加载更早
  const jumpBtn = $('#jumpBtn');
  msgScrollEl.addEventListener('scroll', () => {
    jumpBtn.hidden = isNearBottom();
    if (msgScrollEl.scrollTop < 60 && S.hasMore && !S.loadingOlder && S.current && S.current.type !== 'channel') {
      S.loadingOlder = true;
      (S.current.type === 'private' ? loadOlderPrivate() : loadOlderGroup())
        .finally(() => { S.loadingOlder = false; });
    }
  });
  jumpBtn.addEventListener('click', () => scrollBottom(true));

  // 键盘 Esc 关闭抽屉
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      if (modalMask.classList.contains('show')) closeModal();
      else if (drawer.classList.contains('show')) hideDrawer();
      else if (isMobile() && S.current) closeChat();
    }
  });

  /* ---------------- 初始化 ---------------- */
  /* 外观：data-theme 控制深浅两套 CSS 变量；全局背景经 CSS 变量注入（DOM API 赋值，
     避免拼 HTML 时 url 引号截断属性的坑），两种模式共用同一张图、各配遮罩色 */
  function applyAppearance() {
    const me = S.me || {};
    document.documentElement.dataset.theme = me.theme === 'light' ? 'light' : 'dark';
    const root = document.documentElement;
    // 磨砂层不透明度由用户自定义（仅全局背景存在时视觉生效）
    root.style.setProperty('--bg-alpha', (me.bg_opacity == null ? 55 : +me.bg_opacity) / 100);
    if (me.global_bg) {
      root.style.setProperty('--global-bg', 'url("' + me.global_bg + '")');
      document.body.classList.add('has-global-bg');
    } else {
      root.style.removeProperty('--global-bg');
      document.body.classList.remove('has-global-bg');
    }
  }
  function setTheme(mode) {
    if ((S.me.theme || 'dark') === mode) return;
    api('/user/update/personal_info', { theme: mode }).then(r => {
      if (r.code === 0) { S.me = r.data; applyAppearance(); refreshSettings(); }
      else toast(r.msg || '切换失败');
    });
  }
  /* 主题切换动效：以按钮（汉堡）圆心为起点，新主题的圆形遮罩逐级放大直到覆盖全屏（View Transitions）；
     乐观切换：DOM 立即变更，后端持久化失败再回滚；不支持 VT 或用户偏好减少动效时退化为瞬切 */
  function toggleThemeFromBtn(btn) {
    const mode = ((S.me.theme || 'dark') === 'dark') ? 'light' : 'dark';
    const prev = S.me.theme || 'dark';
    const rect = btn.getBoundingClientRect();
    const cx = rect.left + rect.width / 2, cy = rect.top + rect.height / 2;
    // 圆半径需盖到距圆心最远的屏幕角，取两轴最大距离的斜边
    const radius = Math.hypot(Math.max(cx, innerWidth - cx), Math.max(cy, innerHeight - cy)) + 20;
    function apply() {
      S.me.theme = mode;
      applyAppearance();
      refreshSettings();
      // 注意：不能 return api(...)，startViewTransition 会等待回调返回的 Promise，卡到网络响应才播动画
      api('/user/update/personal_info', { theme: mode }).then(r => {
        if (r.code !== 0) {
          S.me.theme = prev;
          applyAppearance();
          refreshSettings();
          toast(r.msg || '切换失败');
        }
      });
    }
    if (!document.startViewTransition || window.matchMedia('(prefers-reduced-motion: reduce)').matches) { apply(); return; }
    const vt = document.startViewTransition(apply);
    vt.ready.then(() => {
      document.documentElement.animate(
        { clipPath: ['circle(0px at ' + cx + 'px ' + cy + 'px)', 'circle(' + radius + 'px at ' + cx + 'px ' + cy + 'px)'] },
        { duration: 680, easing: 'cubic-bezier(.32,.72,.24,1)', pseudoElement: '::view-transition-new(root)' }
      );
    });
    vt.catch(() => {});  // 页面隐藏等导致 VT 中止时静默，主题已在 apply 中切换
  }
  function refreshSettings() { renderSettings(); }
  /* 抽屉头部背景：有资料页封面则铺封面（叠加暗角保文字可读），否则交回 CSS 默认渐变（浅色蓝白/深色灰） */
  function paintDrawerHead() {
    const head = $('.drawer-head');
    if (!head) return;
    if (S.me && S.me.background) {
      head.classList.add('has-cover');
      head.style.backgroundImage = 'linear-gradient(180deg,rgba(10,20,30,.12),rgba(10,20,30,.42)),url("' + S.me.background + '")';
    } else {
      head.classList.remove('has-cover');
      head.style.backgroundImage = '';
    }
  }
  function fillDrawer() {
    $('#drawerAvatar').src = S.me.avatar || 'https://robohash.org/me' + S.me.id;
    watchAvatar($('#drawerAvatar'), S.me.nickname);
    $('#drawerName').textContent = S.me.nickname || '未命名';
    $('#drawerNumber').textContent = S.me.number || '';
    paintDrawerHead();
    renderSettings();
  }

  /* 侧栏与聊天区之间可拖拽分隔条：仅桌面（鼠标）可调节，移动端不启用；宽度记忆到 localStorage */
  function initSidebarResizer() {
    const resizer = $('#sidebarResizer'), sidebar = $('.sidebar'), app = $('#app');
    if (!resizer || !sidebar || !app) return;
    const KEY = 'chatonline_sidebar_w', MIN = 240, MAX = 640;
    const isMobile = () => window.matchMedia('(max-width:720px)').matches;
    const applyW = (w) => {
      w = Math.max(MIN, Math.min(MAX, Math.round(w)));
      sidebar.style.width = w + 'px';
      sidebar.style.maxWidth = 'none';   // 覆盖默认 420px 上限，改由 JS 夹取
      return w;
    };
    const saved = parseInt(localStorage.getItem(KEY) || '0', 10);
    if (saved && !isMobile()) applyW(saved);
    let dragging = false;
    resizer.addEventListener('mousedown', (e) => {
      if (isMobile()) return;
      dragging = true; resizer.classList.add('dragging');
      document.body.style.userSelect = 'none'; document.body.style.cursor = 'col-resize';
      e.preventDefault();
    });
    document.addEventListener('mousemove', (e) => {
      if (!dragging) return;
      applyW(e.clientX - app.getBoundingClientRect().left);
    });
    document.addEventListener('mouseup', () => {
      if (!dragging) return;
      dragging = false; resizer.classList.remove('dragging');
      document.body.style.userSelect = ''; document.body.style.cursor = '';
      localStorage.setItem(KEY, parseInt(sidebar.style.width, 10) || '');
    });
    // 回到窄屏：清除内联宽度，交回 CSS 全宽布局（移动端不可调）
    window.addEventListener('resize', () => {
      if (isMobile()) { sidebar.style.width = ''; sidebar.style.maxWidth = ''; }
    });
  }

  /* 空会话占位的小吉娃娃：在占位区内悠闲飘动，鼠标悬停暂停，点击冒爱心 + 头顶气泡 */
  function initPlaceholderPet() {
    const ph = $('#chatPlaceholder'), roam = $('#phRoam'), hearts = $('#phHearts'), bubble = $('#phBubble');
    if (!ph || !roam) return;
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const PET = 158;
    const PHRASES = ['点点我好酥胡~', '嘿嘿，被你点啦~', '摸摸头，陪我聊会儿嘛~', '选个会话开始叭~', '汪汪！点我干嘛~'];
    let x = 0, y = 0, tx = 0, ty = 0, placed = false, paused = false, idleUntil = 0, bubbleTimer = null;
    const bounds = () => ({ maxX: Math.max(0, ph.clientWidth - PET), maxY: Math.max(0, ph.clientHeight - PET) });
    const pick = () => { const b = bounds(); tx = Math.random() * b.maxX; ty = Math.random() * b.maxY; };
    const center = () => {
      const b = bounds(); x = b.maxX / 2; y = b.maxY / 2; tx = x; ty = y;
      roam.style.transform = 'translate(' + x + 'px,' + y + 'px)';
      roam.classList.add('placed');
    };
    function loop() {
      requestAnimationFrame(loop);
      if (ph.hidden || reduce || paused || !placed) return;
      const b = bounds();
      const dx = tx - x, dy = ty - y;
      if (Math.hypot(dx, dy) < 5) {
        const now = performance.now();
        if (now > idleUntil) { pick(); idleUntil = now + 1400 + Math.random() * 2800; }
      } else {
        x = Math.max(0, Math.min(b.maxX, x + dx * 0.012));
        y = Math.max(0, Math.min(b.maxY, y + dy * 0.012));
        roam.style.transform = 'translate(' + x.toFixed(1) + 'px,' + y.toFixed(1) + 'px)';
      }
    }
    // 首次可见且有尺寸时定位到中心，再启动飘动循环
    (function ensure() {
      if (placed) { loop(); return; }
      if (!ph.hidden && ph.clientWidth > PET) { center(); pick(); placed = true; loop(); return; }
      requestAnimationFrame(ensure);
    })();
    roam.addEventListener('mouseenter', () => paused = true);
    roam.addEventListener('mouseleave', () => paused = false);
    function popHearts() {
      if (!hearts) return;
      const n = 5 + Math.floor(Math.random() * 4);
      for (let i = 0; i < n; i++) {
        const s = document.createElement('span');
        s.className = 'ph-heart';
        s.innerHTML = '<i class="ic ic-heart"></i>';
        s.style.setProperty('--dx', (Math.random() * 100 - 50).toFixed(0) + 'px');
        s.style.setProperty('--sz', (16 + Math.random() * 14).toFixed(0) + 'px');
        s.style.animationDelay = (Math.random() * 0.18).toFixed(2) + 's';
        hearts.appendChild(s);
        setTimeout(() => s.remove(), 1500);
      }
    }
    function showBubble() {
      if (!bubble) return;
      bubble.textContent = PHRASES[Math.floor(Math.random() * PHRASES.length)];
      roam.classList.add('show-bubble');
      clearTimeout(bubbleTimer);
      bubbleTimer = setTimeout(() => roam.classList.remove('show-bubble'), 1700);
    }
    roam.addEventListener('click', () => { popHearts(); showBubble(); });
    window.addEventListener('resize', () => {
      if (!placed) return;
      const b = bounds();
      x = Math.max(0, Math.min(b.maxX, x)); y = Math.max(0, Math.min(b.maxY, y));
      tx = Math.min(tx, b.maxX); ty = Math.min(ty, b.maxY);
      roam.style.transform = 'translate(' + x.toFixed(1) + 'px,' + y.toFixed(1) + 'px)';
    });
  }

  /* ---------------- 全站公告 · 送信小吉娃娃 ----------------
     管理员在管理端发布后：socket 实时广播 site_announcement；离线用户下次进入由 /announcement/pending 补发。
     只要有未确认的公告，就在会话列表上放出一只小吉娃娃（可盖在会话条目上飞行，但容器不拦点击），
     配常驻气泡；点它弹公告：
       右上角 ✕ 仅关闭（不写确认记录，小狗继续飞）；右下角「已知晓」才写 SiteAnnouncementRead，
       并让小狗先说一句“信已送到，下次见”，再播旋转升空消失动画。 */
  const ANN_PET_BUBBLE = '说来公告啦，快来看看吧';
  const ANN_SENT_TEXT = '信已送到，下次见';
  const ANN_PET_W = 72, ANN_PET_H = 72, ANN_SIDE_GAP = 40;   // 40：气泡宽 150 比本体多出的单侧宽度
  const annState = { list: [], x: 0, y: 0, tx: 0, ty: 0, idleUntil: 0, running: false, stopped: false, hover: false, timers: [] };

  function clearAnnTimers() { annState.timers.forEach(clearTimeout); annState.timers = []; }

  /* 飞行区域：整个侧栏会话区（可盖在会话条目上），顶部避开搜索栏，底部避开悬浮 tabbar */
  function annBounds() {
    const pet = $('#annPet'), side = pet && pet.parentElement;
    if (!side) return null;
    const tabbar = $('#tabbar');
    const head = side.querySelector('.sidebar-head');
    const minY = Math.max(8, (head ? head.offsetHeight : 60) + 6);
    const maxY = Math.max(minY, side.clientHeight - ANN_PET_H - ((tabbar ? tabbar.offsetHeight : 64) + 34));
    const maxX = Math.max(ANN_SIDE_GAP, side.clientWidth - ANN_PET_W - ANN_SIDE_GAP);
    return { maxX, minY, maxY };
  }
  function annPick(b) {
    annState.tx = ANN_SIDE_GAP + Math.random() * Math.max(0, b.maxX - ANN_SIDE_GAP);
    annState.ty = b.minY + Math.random() * Math.max(0, b.maxY - b.minY);
  }
  function annApplyPos() {
    const pet = $('#annPet');
    if (pet) pet.style.transform = 'translate(' + annState.x.toFixed(1) + 'px,' + annState.y.toFixed(1) + 'px)';
  }
  function annPlace() {
    const b = annBounds(); if (!b) return;
    annState.x = 6; annState.y = b.maxY; annApplyPos();   // 从左下角起飞
    annPick(b);
  }
  function annFlyLoop() {
    if (annState.running) return;
    annState.running = true;
    const step = () => {
      requestAnimationFrame(step);
      const pet = $('#annPet');
      // 桌面端鼠标悬停时完全定住（CSS 只停浮动动画，位置由这里冻结），方便点击
      if (!pet || pet.hidden || annState.stopped || annState.hover) return;
      const b = annBounds(); if (!b) return;
      const dx = annState.tx - annState.x, dy = annState.ty - annState.y;
      if (Math.hypot(dx, dy) < 9) {
        const now = performance.now();
        if (now > annState.idleUntil) { annPick(b); annState.idleUntil = now + 1500 + Math.random() * 3000; }
      } else {
        // 0.008：缓游系数，太大小狗窜太快点不住
        annState.x = Math.max(6, Math.min(b.maxX, annState.x + dx * 0.008));
        annState.y = Math.max(b.minY, Math.min(b.maxY, annState.y + dy * 0.008));
        annApplyPos();
      }
    };
    requestAnimationFrame(step);
  }

  function annCheer() {
    const dog = $('.ap-dog');
    if (!dog || !window.PARTICLES) return;
    const r = dog.getBoundingClientRect();
    if (!r.width) return;
    try { PARTICLES.burstRect(r, { colors: ['#37bbfe', '#a8d4ff', '#ffffff'], count: 180 }); } catch (e) {}
  }

  /* 只在“聊天 tab 且未打开任何会话”时显示：打开会话时小狗收起，回到列表再出现 */
  function annAllowed() { return S.tab === 'chats' && !S.current; }
  function annSyncVisible() {
    const pet = $('#annPet'); if (!pet) return;
    if (annState.stopped) return;              // “信已送到”告别动画进行中不插手
    const show = annState.list.length > 0 && annAllowed();
    if (show === !pet.hidden) return;
    pet.hidden = !show;
    if (show) { annPlace(); annFlyLoop(); }    // 重新露面时从左下角再次起飞
  }

  function annRender(newOne) {
    const pet = $('#annPet'); if (!pet) return;
    const n = annState.list.length;
    // 没有待确认公告，或当前不在会话列表视图（已打开会话/切了 tab），都不露面
    if (!n || !annAllowed()) { pet.hidden = true; return; }
    clearAnnTimers();
    annState.stopped = false;
    pet.classList.remove('ack');
    const bubble = $('#apBubble');
    if (bubble) { bubble.textContent = ANN_PET_BUBBLE; bubble.classList.remove('sent'); }
    const cnt = $('#apCount');
    if (cnt) { cnt.textContent = n > 9 ? '9+' : n; cnt.hidden = n < 2; }
    const wasHidden = pet.hidden;
    pet.hidden = false;
    if (wasHidden) { annPlace(); annFlyLoop(); }
    if (newOne) {
      annCheer();
      if (navigator.vibrate) { try { navigator.vibrate(12); } catch (e) {} }
    }
  }

  function annItemHtml(x) {
    const media = (x.media || []).filter(m => m && m.url);
    const imgs = media.filter(m => (m.type || 3) === 3);
    const vids = media.filter(m => m.type === 2);
    const when = x.created_at ? (fmtDay(x.created_at) + ' ' + fmtTime(x.created_at)) : '';
    return '<div class="ann-item">' +
      '<div class="ann-t">' + esc(x.title) + '</div>' +
      (when ? '<div class="ann-meta">发布 · ' + esc(when) + '</div>' : '') +
      (x.content ? '<div class="ann-body">' + esc(x.content) + '</div>' : '') +
      (imgs.length ? '<div class="ann-media">' + imgs.map(m => mediaImgHtml(m.url, 900, '')).join('') + '</div>' : '') +
      vids.map(m => '<div class="ann-vid"><video src="' + esc(m.url) + '" controls playsinline preload="metadata"></video>' + vidExpandBtn(m.url, m.name) + '</div>').join('') +
      '</div>';
  }

  function openAnnouncementModal() {
    const list = annState.list;
    if (!list.length) return;
    const body = '<div style="max-height:56vh;overflow-y:auto">' + list.map(annItemHtml).join('') + '</div>';
    openModal('', body, ackAnnouncements, '已知晓');
    $('#modalBody').addEventListener('click', (e) => {
      const im = e.target.closest('.ann-media img'); if (!im) return;
      const urls = [];
      $$('#modalBody .ann-media img').forEach(x => urls.push(fullSrc(x)));
      const i = urls.indexOf(fullSrc(im));
      openAvatarViewer(fullSrc(im), im.getBoundingClientRect(), false, '', urls, i < 0 ? 0 : i);
    });
  }

  async function ackAnnouncements() {
    const ids = annState.list.map(x => x.id);
    const r = await api('/announcement/ack', { ids });
    if (!r || r.code !== 0) { toast((r && r.msg) || '操作失败'); return; }
    annState.list = [];
    closeModal();
    annVanish();
  }

  /* 道别 → 旋转升空消失：先定住并改气泡文案，滞口后开旋，同时在小狗位置撒一把粒子 */
  function annVanish() {
    const pet = $('#annPet'), bubble = $('#apBubble');
    if (!pet || pet.hidden) return;
    annState.stopped = true;
    if (bubble) { bubble.textContent = ANN_SENT_TEXT; bubble.classList.add('sent'); }
    if (navigator.vibrate) { try { navigator.vibrate(8); } catch (e) {} }
    const t1 = setTimeout(() => {
      const dog = $('.ap-dog', pet);
      if (dog && window.PARTICLES) {
        const r = dog.getBoundingClientRect();
        if (r.width) { try { PARTICLES.burstRect(r, { colors: ['#37bbfe', '#a8d4ff', '#ffd479', '#ffffff'], count: 260 }); } catch (e) {} }
      }
      pet.classList.add('ack');
    }, 1150);
    const t2 = setTimeout(() => {
      pet.classList.remove('ack');
      pet.hidden = true;
      if (bubble) { bubble.textContent = ANN_PET_BUBBLE; bubble.classList.remove('sent'); }
    }, 1150 + 1400);
    annState.timers = [t1, t2];
  }

  /* 拉取未确认公告（首屏 / 重进页面 / 广播后兜底） */
  async function refreshAnnouncements() {
    let r;
    try { r = await api('/announcement/pending'); } catch (e) { return; }
    if (!r || r.code !== 0) return;
    annState.list = (r.data && r.data.announcements) || [];
    annRender(false);
  }

  function initSiteAnnouncement() {
    const pet = $('#annPet'); if (!pet) return;
    pet.addEventListener('click', () => { if (!annState.stopped) openAnnouncementModal(); });
    // 鼠标悬停→冻结飞行，移开后短暂停留再出发（避免刚移开就窜走点不住）
    const inner = $('#apInner');
    if (inner) {
      inner.addEventListener('mouseenter', () => { annState.hover = true; });
      inner.addEventListener('mouseleave', () => {
        annState.hover = false;
        annState.idleUntil = performance.now() + 900;
      });
    }
    window.addEventListener('resize', () => {
      if (pet.hidden || annState.stopped) return;
      const b = annBounds(); if (!b) return;
      annState.x = Math.max(6, Math.min(b.maxX, annState.x));
      annState.y = Math.max(b.minY, Math.min(b.maxY, annState.y));
      annApplyPos();
    });
    refreshAnnouncements();
  }

  async function init() {
    try {
      const r = await api('/user/me');
      if (r.code !== 0) throw new Error();
      S.me = r.data;
    } catch (e) { location.href = '/user/login'; return; }
    applyAppearance();
    fillDrawer();
    paintProfileTab();
    initSidebarResizer();
        initPlaceholderPet();
    initSocket();
    await loadRemarks();          // 备注映射须先于会话渲染，供 dispName 覆盖显示名
    await reloadConversations();
    loadBlocked();
    refreshReqBadge();
    refreshNotifBadge();
    refreshFbBadge();
    initSiteAnnouncement();
    // 周期性轻刷会话列表（兜底：多端/离线消息）
    setInterval(reloadConversations, 30000);
    // 页面重新可见时刷新
    document.addEventListener('visibilitychange', () => { if (!document.hidden) { reloadConversations(); refreshNotifBadge(); refreshFbBadge(); refreshAnnouncements(); } });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
