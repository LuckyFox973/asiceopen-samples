// Krajinka – spoločný motor ilustrovaných tém v šikmom pohľade (~65°).
// Téma (window.KRAJINKA_THEME) dodá: farby zeme a čistín, typy objektov a ich kresbu.
// Komunikácia s hostiteľom je rovnaká ako pri témach Desklandu (window.chrome.webview):
//   {"t":"icons","vis":true,"r":[[l,t,r,b],…]} (fyzické pixely), {"t":"pause","v":bool}
(function () {
  'use strict';
  const T = window.KRAJINKA_THEME;
  const QS = new URLSearchParams(location.search);
  const WALL = QS.has('wallpaper');
  const cv = document.getElementById('art');
  const ctx = cv.getContext('2d');

  // ------------------------------------------------------------ šum a náhoda
  function hash(x, y, s) {
    let h = Math.imul(x | 0, 374761393) + Math.imul(y | 0, 668265263) + Math.imul(s | 0, 2246822519) | 0;
    h = Math.imul(h ^ (h >>> 13), 1274126177); h ^= h >>> 16;
    return (h >>> 0) / 4294967296;
  }
  const sm = t => t * t * (3 - 2 * t);
  function vnoise(x, y, s) {
    const xf = Math.floor(x), yf = Math.floor(y), tx = sm(x - xf), ty = sm(y - yf);
    const a = hash(xf, yf, s), b = hash(xf + 1, yf, s), c = hash(xf, yf + 1, s), d = hash(xf + 1, yf + 1, s);
    const top = a + (b - a) * tx, bot = c + (d - c) * tx; return top + (bot - top) * ty;
  }
  function fbm(x, y, s, o = 3) { let v = 0, a = .5, f = 1, n = 0; for (let i = 0; i < o; i++) { v += a * vnoise(x * f + i * 17.3, y * f - i * 17.3, s); n += a; a *= .5; f *= 2; } return v / n; }
  const sstep = (a, b, x) => { const t = Math.max(0, Math.min(1, (x - a) / (b - a))); return t * t * (3 - 2 * t); };
  function rng(seed) { let s = seed | 0; return () => { s = (s + 0x6d2b79f5) | 0; let v = Math.imul(s ^ s >>> 15, 1 | s); v ^= v + Math.imul(v ^ v >>> 7, 61 | v); return ((v ^ v >>> 14) >>> 0) / 4294967296; }; }
  const K = { hash, vnoise, fbm, sstep, rng };

  // ------------------------------------------------------------ stav
  let W = 0, H = 0, DPR = 1, icons = [], paused = false;
  let field = null, staticCv = null, prevCv = null, fadeT0 = -1e9;
  const FADE = 900;

  function resize() {
    W = innerWidth; H = innerHeight; DPR = window.devicePixelRatio || 1;
    cv.width = Math.round(W * DPR); cv.height = Math.round(H * DPR);
    cv.style.width = W + 'px'; cv.style.height = H + 'px';
  }

  // ------------------------------------------------------------ pole oblastí (čistiny pod ikonami)
  const CELL = 6;
  function smin(a, b, k) { const h = Math.max(0, Math.min(1, .5 + .5 * (b - a) / k)); return b * (1 - h) + a * h - k * h * (1 - h); }
  function buildField() {
    const cols = Math.ceil(W / CELL) + 2, rows = Math.ceil(H / CELL) + 2;
    const sdf = new Float32Array(cols * rows);
    const [rMin, rMax] = T.region.radius, wob = T.region.wobble, blend = T.region.blend, s = T.seed;
    const shapes = icons.map((ic, k) => {
      const cr = Math.min(28, ic.w / 2, ic.h / 2), ox = (hash(k, 1, 901) - .5) * 14, oy = (hash(k, 2, 901) - .5) * 14;
      return { x0: ic.x + cr + ox, y0: ic.y + cr + oy, x1: ic.x + ic.w - cr + ox, y1: ic.y + ic.h - cr + oy, cr, r: rMin + (rMax - rMin) * hash(k, 3, 901) };
    });
    for (let j = 0; j < rows; j++) for (let i = 0; i < cols; i++) {
      const px = i * CELL, py = j * CELL;
      let e = 1e4;
      for (const sh of shapes) {
        const dx = Math.max(sh.x0 - px, 0, px - sh.x1), dy = Math.max(sh.y0 - py, 0, py - sh.y1);
        e = smin(e, Math.max(Math.hypot(dx, dy) - sh.cr, 0) - sh.r, blend);
      }
      sdf[j * cols + i] = -(e + (fbm(px / 60, py / 60, s + 555) - .5) * wob);
    }
    const at = (x, y) => {
      const gx = Math.max(0, Math.min(cols - 1.001, x / CELL)), gy = Math.max(0, Math.min(rows - 1.001, y / CELL));
      const ix = gx | 0, iy = gy | 0, fx = gx - ix, fy = gy - iy, b = iy * cols + ix;
      const t = sdf[b] + (sdf[b + 1] - sdf[b]) * fx, u = sdf[b + cols] + (sdf[b + cols + 1] - sdf[b + cols]) * fx;
      return t + (u - t) * fy;
    };
    return { at, cols, rows };
  }

  // ------------------------------------------------------------ statická vrstva
  function newLayer() { const c = document.createElement('canvas'); c.width = cv.width; c.height = cv.height; const x = c.getContext('2d'); x.setTransform(DPR, 0, 0, DPR, 0, 0); return [c, x]; }

  function renderStatic() {
    field = buildField();
    const [gc, g] = newLayer();
    T.paintGround(g, W, H, K);
    // čistiny: vlastná textúra orezaná maskou poľa
    const [rc, r] = newLayer();
    T.paintRegion(r, W, H, K);
    const mw = Math.ceil(W / 2), mh = Math.ceil(H / 2);
    const mc = document.createElement('canvas'); mc.width = mw; mc.height = mh;
    const mx = mc.getContext('2d'), img = mx.createImageData(mw, mh);
    for (let y = 0; y < mh; y++) for (let x = 0; x < mw; x++) {
      const a = sstep(-1.2, 1.2, field.at(x * 2 + 1, y * 2 + 1));
      img.data[(y * mw + x) * 4 + 3] = a * 255;
    }
    mx.putImageData(img, 0, 0);
    r.setTransform(1, 0, 0, 1, 0, 0); r.globalCompositeOperation = 'destination-in';
    r.imageSmoothingEnabled = true; r.drawImage(mc, 0, 0, rc.width, rc.height);
    r.globalCompositeOperation = 'source-over';
    if (T.paintRegionEdge) { r.setTransform(DPR, 0, 0, DPR, 0, 0); }
    g.setTransform(1, 0, 0, 1, 0, 0); g.drawImage(rc, 0, 0); g.setTransform(DPR, 0, 0, DPR, 0, 0);
    if (T.paintRegionEdge) T.paintRegionEdge(g, W, H, K, field);

    // objekty: rozmiestnenie mimo čistín a mimo ikon, kresba odzadu dopredu
    const objs = T.place(W, H, K, field, icons).filter(o => {
      const b = o.box;                                    // [x0,y0,x1,y1] celý obrys aj s korunou/strechou
      return !icons.some(ic => !(b[2] < ic.x - 4 || b[0] > ic.x + ic.w + 4 || b[3] < ic.y - 4 || b[1] > ic.y + ic.h + 4));
    });
    objs.sort((a, b) => a.y - b.y);
    for (const o of objs) T.shadow(g, o, K);
    for (const o of objs) T.draw(g, o, K);
    if (T.paintOver) T.paintOver(g, W, H, K, field);
    prevCv = staticCv; staticCv = gc; fadeT0 = performance.now();
    if (T.actors) T.actors.layout(W, H, K, field, icons);
  }

  // ------------------------------------------------------------ snímky
  let last = 0;
  function frame(now) {
    requestAnimationFrame(frame);
    if (paused || !staticCv) return;
    if (now - last < 31) return;                       // ~30 fps
    const dt = last ? Math.min(.1, (now - last) / 1000) : .033; last = now;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    const f = Math.min(1, (now - fadeT0) / FADE);
    if (prevCv && f < 1) { ctx.globalAlpha = 1; ctx.drawImage(prevCv, 0, 0); ctx.globalAlpha = sm(f); }
    ctx.drawImage(staticCv, 0, 0); ctx.globalAlpha = 1;
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    if (T.actors) T.actors.frame(ctx, dt, now / 1000, K);
  }

  // ------------------------------------------------------------ hostiteľ
  function setIcons(list) { icons = list; renderStatic(); }
  function demoIcons() {
    const out = [];
    for (let i = 0; i < 2; i++) for (let j = 0; j < 5; j++) out.push({ x: 16 + i * 84, y: 14 + j * 104, w: 76, h: 92 });
    for (const [x, y] of [[W * .42, H * .25], [W * .42 + 84, H * .25], [W * .75, H * .6]]) out.push({ x, y, w: 76, h: 92 });
    return out;
  }
  window.addEventListener('resize', () => { resize(); renderStatic(); });
  resize();
  if (WALL) {
    icons = []; renderStatic();
    const host = window.chrome && window.chrome.webview;
    if (host) {
      host.addEventListener('message', ev => {
        const m = JSON.parse(ev.data);
        if (m.t === 'pause') { paused = !!m.v; last = 0; return; }
        if (m.t !== 'icons') return;
        const k = window.devicePixelRatio || 1;
        setIcons(m.vis ? m.r.map(([l, t, r, b]) => ({ x: l / k, y: t / k, w: (r - l) / k, h: (b - t) / k })) : []);
      });
      host.postMessage('ready');
    }
  } else {
    setIcons(demoIcons());
  }
  requestAnimationFrame(frame);
})();
