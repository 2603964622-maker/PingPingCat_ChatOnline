/* ===== particles.js — 元素"随风消散"粒子特效（Canvas 实现）=====
   目标：删除/撤回/取消拉黑时，元素不是瞬间炸开，而是
   慢慢分解成细密的微小粒子，被风轻轻吹起、左右摇曳着缓缓飘散消失。 */
(function () {
  const canvas = document.getElementById('fxCanvas');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  let dpr = 1;
  let particles = [];
  let running = false;

  // 画布尺寸跟随窗口（含高分屏 DPR）
  function resize() {
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.floor(window.innerWidth * dpr);
    canvas.height = Math.floor(window.innerHeight * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  window.addEventListener('resize', resize);
  resize();

  const rand = (a, b) => a + Math.random() * (b - a);

  // 在元素矩形内撒出一批细密粒子（phase: 0~1 释放进度，越晚批次越"松散"）
  function emitRect(rect, colors, count, phase) {
    const cx = rect.left + rect.width / 2;
    const cy = rect.top + rect.height / 2;
    for (let i = 0; i < count; i++) {
      // 均匀采样整个矩形面，模拟"表面逐点剥离"
      const px = rect.left + Math.random() * rect.width;
      const py = rect.top + Math.random() * rect.height;
      const dx = px - cx, dy = py - cy;
      const spread = 6 + phase * 14; // 后期批次扩散略强，像结构开始松动
      particles.push({
        x: px, y: py,
        // 初速很弱：只有轻微向外扩散 + 一点上飘，绝不"炸开"
        vx: (dx / (rect.width / 2 || 1)) * rand(2, spread) + rand(-6, 6),
        vy: (dy / (rect.height / 2 || 1)) * rand(1, spread * 0.5) - rand(8, 26),
        size: rand(0.35, 1.3),                // 极细粒子（越细密越有"粉尘"感）
        color: colors[(Math.random() * colors.length) | 0],
        life: 1,
        decay: rand(0.55, 1.05),              // 衰减更快 → 单粒寿命约 0.95~1.8s（比之前快约 0.5 倍）
        wind: rand(16, 44),                   // 恒定侧风（整体往一侧飘）
        sway: rand(10, 30),                   // 风的摆动幅度
        swayFreq: rand(1.1, 2.4),             // 摆动频率
        swayPhase: rand(0, Math.PI * 2),
        rise: rand(6, 16),                    // 持续上托力（升华感）
        spin: rand(-2.4, 2.4),
        rot: rand(0, Math.PI * 2),
        square: Math.random() < 0.22,         // 少量方形，增加"碎屑"质感
        depth: rand(0.6, 1.25)                // 景深：不同粒子受风强弱不同，形成层次
      });
    }
    if (particles.length > 4000) particles.splice(0, particles.length - 4000);
    if (!running) { running = true; requestAnimationFrame(tick); }
  }

  // 兼容旧接口：一次性爆发（内部转用细密粒子参数）
  function burstRect(rect, opts) {
    opts = opts || {};
    const colors = opts.colors && opts.colors.length ? opts.colors : ['#3390ec', '#7fbaec', '#ffffff'];
    const area = rect.width * rect.height;
    const count = opts.count || Math.max(160, Math.min(600, Math.round(area / 45)));
    emitRect(rect, colors, count, 0.5);
  }

  let last = 0;
  function tick(ts) {
    if (!last) last = ts;
    let dt = (ts - last) / 1000;
    last = ts;
    if (dt > 0.05) dt = 0.05; // 防切后台后大跳
    ctx.clearRect(0, 0, canvas.width / dpr, canvas.height / dpr);
    const t = ts / 1000; // 全局时间，用于风的相位
    for (let i = particles.length - 1; i >= 0; i--) {
      const p = particles[i];
      p.life -= p.decay * dt;
      if (p.life <= 0) { particles.splice(i, 1); continue; }
      // 风：恒定侧风 + 随时间/位置的正弦湍流，按景深加权
      const gust = Math.sin(t * p.swayFreq + p.swayPhase + p.y * 0.01) * p.sway;
      const targetVx = (p.wind + gust) * p.depth;
      // 横向速度向风速平滑靠拢（空气阻力），形成"被风带走"的柔和感
      p.vx += (targetVx - p.vx) * Math.min(1, 2.2 * dt);
      // 纵向：持续上托 + 极轻重力抵消，尾段几乎匀速上浮
      p.vy += (-p.rise * 0.6 + 26) * dt;
      p.vy *= (1 - 0.8 * dt);
      p.x += p.vx * dt;
      p.y += p.vy * dt;
      p.rot += p.spin * dt;
      // 透明度：pow 缓出，尾段细腻渐隐；尺寸随生命微微收缩
      const alpha = Math.pow(p.life, 1.7);
      const sz = p.size * (0.5 + p.life * 0.7);
      ctx.globalAlpha = alpha;
      ctx.fillStyle = p.color;
      if (p.square) {
        ctx.save();
        ctx.translate(p.x, p.y);
        ctx.rotate(p.rot);
        ctx.fillRect(-sz / 2, -sz / 2, sz, sz);
        ctx.restore();
      } else {
        ctx.beginPath();
        ctx.arc(p.x, p.y, sz, 0, Math.PI * 2);
        ctx.fill();
      }
    }
    ctx.globalAlpha = 1;
    if (particles.length) {
      requestAnimationFrame(tick);
    } else {
      running = false;
      last = 0;
      ctx.clearRect(0, 0, canvas.width / dpr, canvas.height / dpr);
    }
  }

  // 读取元素实际颜色（气泡背景 + 文本色 + 父级底色），用于粒子配色
  function pickColors(el) {
    const out = [];
    try {
      const cs = getComputedStyle(el);
      const bg = cs.backgroundColor;
      if (bg && bg !== 'transparent' && bg !== 'rgba(0, 0, 0, 0)') out.push(bg);
      out.push(cs.color);
      const parent = el.parentElement && parentBg(el.parentElement);
      if (parent) out.push(parent);
    } catch (e) { /* ignore */ }
    out.push('#3390ec', '#a2dc7a');
    return out;
  }
  function parentBg(el) {
    const bg = getComputedStyle(el).backgroundColor;
    return (bg && bg !== 'rgba(0, 0, 0, 0)') ? bg : null;
  }

  // 对外：对某个 DOM 元素做"逐渐分解并随风消散"
  // 关键：粒子分批持续释放（而非一次爆出），配合元素缓慢淡出，形成"慢慢崩解"感
  function dissolve(el, opts) {
    if (!el) return;
    opts = opts || {};
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 && rect.height === 0) { el.remove(); return; }
    const colors = opts.colors || pickColors(el);
    const area = rect.width * rect.height;
    // 细密：粒子非常多（按面积高密度），限幅提高上限，保证大卡片也足够密
    const total = opts.count || Math.max(180, Math.min(650, Math.round(area / 40)));
    const waves = 8;                 // 分 8 批释放，配合更多粒子让崩解更连续
    const perWave = Math.ceil(total / waves);
    const span = 420;                // 释放总时长约 0.42s（更集中，消散更快）
    // 极轻的回弹（几乎不可察），随后进入淡出 + 持续撒粒子
    el.style.transition = 'transform .14s cubic-bezier(.34,1.56,.64,1)';
    el.style.transform = 'scale(1.015)';
    setTimeout(() => {
      el.classList.add('dissolve');
      for (let w = 0; w < waves; w++) {
        setTimeout(() => emitRect(rect, colors, perWave, w / (waves - 1)), w * (span / waves));
      }
      setTimeout(() => el.remove(), 850);
    }, 140);
  }

  // 对外：整列表清空时多元素错峰粒子化
  function dissolveAll(els, stagger) {
    stagger = stagger || 55;
    els.forEach((el, i) => setTimeout(() => dissolve(el), i * stagger));
  }

  window.PARTICLES = { burstRect, dissolve, dissolveAll, resize };
})();
