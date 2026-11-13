// Krajinka – corgi na lúke (náhrada chlapca s drakom v téme Meadow).
// Rovnaké rozhranie ako meadow-boy.js: prepareMeadowBoy(now, visible) vráti
// {rect:[x,y,w,h] (CSS px), actor: textúra RGBA, shadow: textúra (R = tieň), state}.
// Corgi sa kreslí perom a farbami do malého plátna (Canvas 2D) a nahrá sa ako textúra.
// Pohľad je šikmo zhora (~65°): vidno chrbát aj bok, tieň padá doprava dole.
let meadowCorgiScene = null;

function prepareMeadowBoy(now, visible = true) {
  if (!meadowCorgiScene) meadowCorgiScene = createMeadowCorgiScene();
  return meadowCorgiScene.draw(now, visible);
}

function createMeadowCorgiScene() {
  const BOX_W = 150, BOX_H = 120;                 // plátno okolo psa (v jednotkách psa)
  const actorCanvas = document.createElement('canvas');
  const shadowCanvas = document.createElement('canvas');
  const ac = actorCanvas.getContext('2d'), sc = shadowCanvas.getContext('2d');
  const actorTex = gl.createTexture(), shadowTex = gl.createTexture();
  for (const t of [actorTex, shadowTex]) {
    gl.activeTexture(gl.TEXTURE5); gl.bindTexture(gl.TEXTURE_2D, t);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  }

  // ---- náhoda so semienkom
  let rs = 0x2c1b3c6d;
  const rnd = () => { rs = (rs + 0x6d2b79f5) | 0; let v = Math.imul(rs ^ rs >>> 15, 1 | rs); v ^= v + Math.imul(v ^ v >>> 7, 61 | v); return ((v ^ v >>> 14) >>> 0) / 4294967296; };

  // ---- stav
  let layout = '', time = 0, prev = null;
  const dog = { x: 0, y: 0, face: 1, mode: 'idle', until: 0, target: null, speed: 0, gait: 0, phase: 0,
                head: 0, tail: 0, sit: 0, sniff: 0, lie: 0, tongue: 0 };

  const scale = () => Math.max(.55, Math.min(1.25, Math.min(W / 1379, (H - TASKBAR) / 1141))) * 1.15;

  // voľné miesto na lúke: ďalej od ikon (tie sú v oblakoch) a od okrajov
  function freeSpot(near) {
    const s = scale(), pad = 70 * s, margin = (P.sigma || 72) + 60 * s;
    let best = null, bestScore = -1e9;
    for (let i = 0; i < 90; i++) {
      const p = near && i < 45
        ? { x: near.x + (rnd() - .5) * 520 * s, y: near.y + (rnd() - .5) * 360 * s }
        : { x: pad + rnd() * (W - 2 * pad), y: pad + rnd() * (H - TASKBAR - 2 * pad) };
      if (p.x < pad || p.x > W - pad || p.y < pad || p.y > H - TASKBAR - pad) continue;
      let clear = 1e9;
      for (const ic of icons) {
        const dx = Math.max(ic.x - p.x, 0, p.x - ic.x - ic.w), dy = Math.max(ic.y - p.y, 0, p.y - ic.y - ic.h);
        clear = Math.min(clear, Math.hypot(dx, dy));
      }
      const score = Math.min(clear, 400) - (near ? Math.hypot(p.x - near.x, p.y - near.y) * .15 : 0);
      if (clear > margin && score > bestScore) { best = p; bestScore = score; }
    }
    return best || { x: W * .5, y: (H - TASKBAR) * .6 };
  }

  function choose() {
    const r = rnd();
    if (r < .38) { dog.mode = 'trot'; dog.target = freeSpot(dog); dog.speed = 70; }
    else if (r < .52) { dog.mode = 'zoom'; dog.target = freeSpot(null); dog.speed = 230; }
    else if (r < .70) { dog.mode = 'sniff'; dog.until = time + 3 + rnd() * 3; }
    else if (r < .88) { dog.mode = 'sit'; dog.until = time + 4 + rnd() * 5; }
    else { dog.mode = 'lie'; dog.until = time + 7 + rnd() * 6; }
  }

  function update(dt) {
    const s = scale();
    const ease = (v, to, k) => v + (to - v) * Math.min(1, dt * k);
    let moving = false;
    if (dog.mode === 'trot' || dog.mode === 'zoom') {
      const dx = dog.target.x - dog.x, dy = dog.target.y - dog.y, d = Math.hypot(dx, dy);
      const v = dog.speed * s * Math.min(1, d / (40 * s) + .25);
      if (d < 4) { choose(); }
      else {
        dog.x += dx / d * Math.min(d, v * dt); dog.y += dy / d * Math.min(d, v * dt);
        if (Math.abs(dx) > 3) dog.face = dx > 0 ? 1 : -1;
        moving = true;
      }
    } else if (time > dog.until) {
      choose();
    }
    const run = dog.mode === 'zoom' && moving;
    dog.gait = ease(dog.gait, moving ? 1 : 0, 6);
    dog.phase += dt * (run ? 14 : 9.5) * dog.gait;
    dog.sit = ease(dog.sit, dog.mode === 'sit' ? 1 : 0, 5);
    dog.lie = ease(dog.lie, dog.mode === 'lie' ? 1 : 0, 3);
    dog.sniff = ease(dog.sniff, dog.mode === 'sniff' ? 1 : 0, 4);
    dog.tongue = ease(dog.tongue, run || dog.mode === 'sit' ? 1 : 0, 3);
    const wagSpeed = dog.mode === 'sit' ? 16 : run ? 20 : 9;
    dog.tail = Math.sin(time * wagSpeed) * (dog.mode === 'lie' ? .15 : .6);
    dog.head = dog.mode === 'sniff' ? Math.sin(time * 2.3) * .25 : Math.sin(time * .7) * .08;
    dog.run = run;
  }

  // ---- kresba psa (pohľad zboku-zhora, otočený doprava; jednotka = 1 px pri mierke 1)
  const INK = '#5b3b22', FUR = '#d9883c', FUR_LIGHT = '#eba659', FUR_DARK = '#b86a2a', WHITE = '#fff6ea', PINK = '#e99a97';
  function blob(c, x, y, rx, ry, rot = 0) { c.beginPath(); c.ellipse(x, y, rx, ry, rot, 0, Math.PI * 2); }
  function inked(c, fill) { c.fillStyle = fill; c.fill(); c.lineWidth = 1.3; c.strokeStyle = INK; c.stroke(); }

  function drawDog(c) {
    const ph = dog.phase, g = dog.gait, sit = dog.sit, lie = dog.lie, sniff = dog.sniff;
    const bob = -Math.abs(Math.sin(ph)) * (dog.run ? 3.2 : 1.6) * g;
    const bodyY = -24 + bob + sit * 4 + lie * 9;
    // nohy (zadné dvojice za telom tmavšie)
    const legs = [
      { x: -20, phase: 0, back: true }, { x: 17, phase: Math.PI, back: true },
      { x: -15, phase: Math.PI, back: false }, { x: 22, phase: 0, back: false },
    ];
    for (const L of legs) {
      if (lie > .6) continue;
      const swing = Math.sin(ph + L.phase) * (dog.run ? 9 : 6) * g;
      const lift = Math.max(0, Math.cos(ph + L.phase)) * 3 * g;
      const isRear = L.x < 0;
      const sitFold = isRear ? sit : 0;
      const topX = L.x, topY = bodyY + 6;
      const footX = L.x + swing - sitFold * 6, footY = -2 - lift - sitFold * 2 + lie * 8;
      c.beginPath();
      c.moveTo(topX - 4.5, topY); c.lineTo(footX - 4, footY - 3);
      c.quadraticCurveTo(footX - 4.5, footY + 1.5, footX + 1, footY + 1.5);
      c.quadraticCurveTo(footX + 5.5, footY + 1.5, footX + 4, footY - 3);
      c.lineTo(topX + 4.5, topY); c.closePath();
      inked(c, L.back ? '#e8dccb' : WHITE);
      // ryšavý vrch nohy
      blob(c, topX, topY + 1, 5.5, 4.5); c.fillStyle = L.back ? FUR_DARK : FUR; c.fill();
    }
    // chvost (huňatý, vrtí sa)
    c.save(); c.translate(-33, bodyY - 4 + sit * 6); c.rotate(-.5 + dog.tail - sit * .6);
    blob(c, -6, 0, 9, 5.5); inked(c, FUR);
    blob(c, -10, -1, 4, 2.6); c.fillStyle = WHITE; c.fill();
    c.restore();
    // telo: dlhé, nízke; chrbát svetlejší (pohľad zhora)
    c.save(); c.translate(0, bodyY); c.rotate(-sit * .32);
    c.beginPath();
    c.moveTo(-34, -2); c.bezierCurveTo(-36, -16, -16, -19, 4, -18);
    c.bezierCurveTo(24, -19, 36, -14, 34, -2);
    c.bezierCurveTo(33, 10, 18, 13, 2, 12);
    c.bezierCurveTo(-18, 13, -33, 10, -34, -2); c.closePath();
    inked(c, FUR);
    c.save(); c.clip();
    blob(c, 0, -13, 32, 7); c.fillStyle = FUR_LIGHT; c.fill();             // chrbát na svetle
    blob(c, 6, 10, 24, 6); c.fillStyle = WHITE; c.fill();                 // biele brucho
    blob(c, 30, 4, 9, 11); c.fillStyle = WHITE; c.fill();                 // biela hruď
    c.restore();
    c.restore();
    // hlava
    const hx = 34 + sit * -2, hy = bodyY - 14 - sit * 10 + sniff * 14 + lie * 4;
    c.save(); c.translate(hx, hy); c.rotate(dog.head + sniff * .55);
    // uši
    for (const [ex, back] of [[-4, true], [7, false]]) {
      c.beginPath(); c.moveTo(ex - 6, -8); c.lineTo(ex - 1, -27); c.lineTo(ex + 6, -8); c.closePath();
      inked(c, back ? FUR_DARK : FUR);
      c.beginPath(); c.moveTo(ex - 3, -10); c.lineTo(ex - .5, -22); c.lineTo(ex + 3, -10); c.closePath();
      c.fillStyle = PINK; c.fill();
    }
    blob(c, 2, -2, 13, 11.5); inked(c, FUR);
    // biela lysina a papuľa
    c.beginPath(); c.moveTo(0, -12); c.quadraticCurveTo(4, -2, 8, 2); c.lineTo(-2, 4); c.closePath();
    c.fillStyle = WHITE; c.fill();
    blob(c, 12, 3, 9, 6.5); inked(c, WHITE);
    blob(c, 20, 1.5, 2.6, 2.1); c.fillStyle = '#2a1d16'; c.fill();        // nos
    // oko (pri čuchaní privreté)
    if (sniff > .5 || lie > .7) { c.beginPath(); c.moveTo(3, -4); c.quadraticCurveTo(5.5, -2.5, 8, -4); c.lineWidth = 1.3; c.strokeStyle = '#2a1d16'; c.stroke(); }
    else { blob(c, 5.5, -4, 1.9, 2.1); c.fillStyle = '#2a1d16'; c.fill(); blob(c, 6, -4.8, .6, .6); c.fillStyle = '#fff'; c.fill(); }
    // jazyk
    if (dog.tongue > .3) { blob(c, 13, 9, 2.6, 3.6 * dog.tongue); c.fillStyle = '#e46f78'; c.fill(); }
    c.restore();
  }

  // tieň sa nezrkadlí so psom – vždy padá doprava dole (svetlo zľava zhora)
  function drawShadow(c) {
    const sit = dog.sit, lie = dog.lie, f = dog.face;
    c.fillStyle = '#ff0000';
    c.filter = 'blur(2.5px)';
    c.globalAlpha = .55;
    blob(c, 4 * f + 9, 8, 40 - sit * 8, 9 + lie * 3); c.fill();
    blob(c, 36 * f + 12, 4 - sit * 6, 12, 8); c.fill();
    c.globalAlpha = 1; c.filter = 'none';
  }

  function upload(tex, canvas, unit) {
    gl.activeTexture(unit); gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, canvas);
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
  }

  return {
    draw(now, visible) {
      if (prev !== null) time += Math.max(0, Math.min(.05, (now - prev) / 1000));
      const dt = prev === null ? 0 : Math.max(0, Math.min(.05, (now - prev) / 1000));
      prev = now;
      const next = [W, H, TASKBAR, seed].join(',');
      if (layout !== next) {
        layout = next; rs = (seed | 0) ^ 0x2c1b3c6d;
        const p = freeSpot(null); dog.x = p.x; dog.y = p.y; dog.mode = 'sit'; dog.until = time + 2;
      }
      update(dt);
      const state = { time, position: { x: dog.x, y: dog.y }, layout, routeTarget: dog.target, running: dog.gait > .5 };
      if (!visible) return { state };
      const s = scale(), k = cv._scale;
      const w = BOX_W * s, h = BOX_H * s;
      const left = dog.x - w * .5, top = dog.y - h * .78;
      const pw = Math.max(2, Math.ceil(w * k)), ph = Math.max(2, Math.ceil(h * k));
      for (const cnv of [actorCanvas, shadowCanvas]) if (cnv.width !== pw || cnv.height !== ph) { cnv.width = pw; cnv.height = ph; }
      for (const [c, fn, flip] of [[ac, drawDog, dog.face], [sc, drawShadow, 1]]) {
        c.setTransform(1, 0, 0, 1, 0, 0); c.clearRect(0, 0, pw, ph);
        // počiatok: stred medzi labkami na zemi
        c.setTransform(k * s * flip, 0, 0, k * s, pw * .5, ph * .78);
        fn(c);
      }
      upload(actorTex, actorCanvas, gl.TEXTURE5);
      upload(shadowTex, shadowCanvas, gl.TEXTURE6);
      return { rect: [left, top, w, h], actor: actorTex, shadow: shadowTex, state };
    }
  };
}
