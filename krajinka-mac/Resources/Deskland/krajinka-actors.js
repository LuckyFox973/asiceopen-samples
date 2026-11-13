// Krajinka – spoločné pohyblivé prvky: tiene oblakov, kŕdle vtákov, padajúce lístky/okvetné lístky.
// Kreslia sa každý snímok nad statickú vrstvu, preto sú zámerne ľahké.
function KrajinkaActors(opts) {
  'use strict';
  const o = Object.assign({ clouds: .10, cloudColor: '20,40,30', birds: true, birdColor: '#3b3530',
                            leaves: 0, leafColors: ['#e9a3b6'], leafSize: 4 }, opts || {});
  let W = 0, H = 0, clouds = [], flock = null, nextFlock = 6, leaves = [], t0 = 0;
  const R = Math.random;

  function layout(w, h) {
    W = w; H = h;
    clouds = [];
    const n = Math.max(2, Math.round(W / 700));
    for (let i = 0; i < n; i++) {
      const puffs = [];
      for (let k = 0; k < 7; k++) puffs.push({ dx: (R() - .5) * 260, dy: (R() - .5) * 70, r: 60 + R() * 70 });
      clouds.push({ x: R() * (W + 600) - 300, y: R() * H, v: 6 + R() * 5, puffs });
    }
    leaves = [];
    const nl = Math.round(W * H / 52000 * o.leaves);
    for (let i = 0; i < nl; i++) leaves.push(newLeaf(true));
  }

  function newLeaf(anywhere) {
    return { x: R() * W, y: anywhere ? R() * H : -10, vy: 10 + R() * 14, sway: 10 + R() * 22, ph: R() * 6.28,
             rot: R() * 6.28, vr: (R() - .5) * 2.4, c: o.leafColors[(R() * o.leafColors.length) | 0], s: o.leafSize * (.7 + R() * .6) };
  }

  function drawClouds(c, dt) {
    if (o.clouds <= 0) return;
    for (const cl of clouds) {
      cl.x += cl.v * dt;
      if (cl.x - 400 > W) { cl.x = -400; cl.y = R() * H; }
      for (const p of cl.puffs) {
        const x = cl.x + p.dx, y = cl.y + p.dy;
        const g = c.createRadialGradient(x, y, 0, x, y, p.r);
        g.addColorStop(0, `rgba(${o.cloudColor},${o.clouds})`); g.addColorStop(.55, `rgba(${o.cloudColor},${o.clouds * .75})`);
        g.addColorStop(1, `rgba(${o.cloudColor},0)`);
        c.fillStyle = g; c.beginPath(); c.arc(x, y, p.r, 0, 6.2832); c.fill();
      }
    }
  }

  function bird(c, x, y, s, flap, color, alpha) {
    const w = 7 * s, up = (flap * 4 - 1.5) * s;
    c.globalAlpha = alpha; c.strokeStyle = color; c.lineWidth = 1.6 * s; c.lineCap = 'round';
    c.beginPath(); c.moveTo(x - w, y + up); c.quadraticCurveTo(x - w * .4, y - 2 * s, x, y + .6 * s);
    c.quadraticCurveTo(x + w * .4, y - 2 * s, x + w, y + up); c.stroke(); c.globalAlpha = 1;
  }

  function drawBirds(c, dt, t) {
    if (!o.birds) return;
    if (!flock) {
      nextFlock -= dt;
      if (nextFlock > 0) return;
      const n = 3 + ((R() * 4) | 0), ltr = R() < .5, y = H * (.15 + R() * .6);
      flock = { x: ltr ? -60 : W + 60, y, vx: (ltr ? 1 : -1) * (55 + R() * 25), vy: (R() - .5) * 14,
                members: Array.from({ length: n }, (_, i) => ({ dx: -Math.ceil(i / 2) * 22 * (ltr ? 1 : -1), dy: (i % 2 ? 1 : -1) * Math.ceil(i / 2) * 13, ph: R() * 6.28 })) };
    }
    flock.x += flock.vx * dt; flock.y += flock.vy * dt;
    for (const m of flock.members) {
      const flap = .5 + .5 * Math.sin(t * 9 + m.ph);
      bird(c, flock.x + m.dx + 46, flock.y + m.dy + 62, 1, flap, 'rgba(0,0,0,1)', .12);          // tieň na zemi
      bird(c, flock.x + m.dx, flock.y + m.dy, 1.05, flap, o.birdColor, .9);
    }
    if (flock.x < -200 || flock.x > W + 200) { flock = null; nextFlock = 18 + R() * 25; }
  }

  function drawLeaves(c, dt, t) {
    for (const L of leaves) {
      L.y += L.vy * dt; L.rot += L.vr * dt;
      const x = L.x + Math.sin(t * .8 + L.ph) * L.sway;
      if (L.y > H + 10) Object.assign(L, newLeaf(false));
      c.save(); c.translate(x, L.y); c.rotate(L.rot); c.scale(1, .55 + .45 * Math.abs(Math.sin(t * 1.7 + L.ph)));
      c.fillStyle = L.c; c.beginPath(); c.ellipse(0, 0, L.s, L.s * .6, 0, 0, 6.2832); c.fill(); c.restore();
    }
  }

  return {
    layout(w, h) { layout(w, h); },
    frame(c, dt, t) { drawClouds(c, dt); drawLeaves(c, dt, t); drawBirds(c, dt, t); }
  };
}
