/* ===== sfx.js — Web Audio 合成音效（无需外部音频文件）===== */
(function () {
  let ctx = null;
  let enabled = true;

  function ac() {
    if (!ctx) {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return null;
      ctx = new AC();
    }
    if (ctx.state === 'suspended') ctx.resume();
    return ctx;
  }

  // 通用：在指定时间播放一个带包络的振荡器音
  function tone(freq, start, dur, type, gainPeak) {
    const c = ac();
    if (!c) return;
    const osc = c.createOscillator();
    const g = c.createGain();
    osc.type = type || 'sine';
    osc.frequency.setValueAtTime(freq, start);
    g.gain.setValueAtTime(0.0001, start);
    g.gain.exponentialRampToValueAtTime(gainPeak || 0.18, start + 0.012);
    g.gain.exponentialRampToValueAtTime(0.0001, start + dur);
    osc.connect(g).connect(c.destination);
    osc.start(start);
    osc.stop(start + dur + 0.02);
  }

  const API = {
    setEnabled(v) { enabled = !!v; },
    isEnabled() { return enabled; },
    // 收到消息：轻快的“叮”—两音上行
    msgIn() {
      if (!enabled) return;
      const c = ac(); if (!c) return;
      const t = c.currentTime;
      tone(660, t, 0.12, 'sine', 0.16);
      tone(990, t + 0.06, 0.16, 'sine', 0.14);
    },
    // 发送消息：短促“啵”
    msgOut() {
      if (!enabled) return;
      const c = ac(); if (!c) return;
      const t = c.currentTime;
      const osc = c.createOscillator(); const g = c.createGain();
      osc.type = 'sine';
      osc.frequency.setValueAtTime(520, t);
      osc.frequency.exponentialRampToValueAtTime(280, t + 0.09);
      g.gain.setValueAtTime(0.0001, t);
      g.gain.exponentialRampToValueAtTime(0.16, t + 0.01);
      g.gain.exponentialRampToValueAtTime(0.0001, t + 0.11);
      osc.connect(g).connect(c.destination);
      osc.start(t); osc.stop(t + 0.13);
    },
    // 会话置顶：柔和弹跳
    bump() {
      if (!enabled) return;
      const c = ac(); if (!c) return;
      const t = c.currentTime;
      tone(440, t, 0.08, 'triangle', 0.10);
      tone(660, t + 0.05, 0.10, 'triangle', 0.10);
    },
    // 打开/收起窗口：滑动“唰”
    whoosh(up) {
      if (!enabled) return;
      const c = ac(); if (!c) return;
      const t = c.currentTime;
      const osc = c.createOscillator(); const g = c.createGain();
      osc.type = 'sine';
      osc.frequency.setValueAtTime(up ? 300 : 600, t);
      osc.frequency.exponentialRampToValueAtTime(up ? 600 : 300, t + 0.18);
      g.gain.setValueAtTime(0.0001, t);
      g.gain.exponentialRampToValueAtTime(0.08, t + 0.05);
      g.gain.exponentialRampToValueAtTime(0.0001, t + 0.2);
      osc.connect(g).connect(c.destination);
      osc.start(t); osc.stop(t + 0.22);
    },
    // 删除：低沉消散
    delete() {
      if (!enabled) return;
      const c = ac(); if (!c) return;
      const t = c.currentTime;
      const osc = c.createOscillator(); const g = c.createGain();
      osc.type = 'sawtooth';
      osc.frequency.setValueAtTime(400, t);
      osc.frequency.exponentialRampToValueAtTime(90, t + 0.35);
      g.gain.setValueAtTime(0.0001, t);
      g.gain.exponentialRampToValueAtTime(0.09, t + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, t + 0.4);
      osc.connect(g).connect(c.destination);
      osc.start(t); osc.stop(t + 0.42);
    }
  };
  window.SFX = API;
})();
