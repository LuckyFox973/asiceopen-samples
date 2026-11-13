# Prototyp shadera (1:1 logika budúceho Metal shadera) – vyrenderuje video.
import numpy as np, sys, os, subprocess, math
import terrain_preview as D
from preview_base import hash2, smooth, sstep, fbm, vnoise, rgb, mix, C, build_field, bil

def fract(x): return x - np.floor(x)

def worley_anim(X, Y, cell, seed, t):
    """Worley s bodmi krúžiacimi okolo stredov buniek – kaustiky."""
    cx = np.floor(X/cell); cy = np.floor(Y/cell)
    F1 = np.full(X.shape, 1e9, np.float32); F2 = np.full(X.shape, 1e9, np.float32)
    for dj in (-1, 0, 1):
        for di in (-1, 0, 1):
            i = cx+di; j = cy+dj
            w = .35 + .3*hash2(i, j, seed+5)
            ph = hash2(i, j, seed+6)*6.2832
            fx = (i + .5 + (hash2(i, j, seed)-.5)*.6 + .24*np.cos(t*w + ph))*cell
            fy = (j + .5 + (hash2(i, j, seed+1)-.5)*.6 + .24*np.sin(t*w*1.3 + ph))*cell
            d = np.sqrt((X-fx)**2 + (Y-fy)**2)
            F2 = np.where(d < F1, F1, np.minimum(F2, d)); F1 = np.minimum(F1, d)
    return F1, F2

WAVE_T = 7.0       # perióda vlny (s)
WAVE_L = 34.0      # vzdialenosť hrebeňov (body)

def swash_profile(p):
    """Výbeh vody na pláž počas jednej periódy: rýchlo hore, pomaly späť, chvíľu nič."""
    up = np.sin(np.clip(p/.22, 0, 1)*np.pi/2)
    down = np.cos(np.clip((p-.22)/.48, 0, 1)*np.pi/2)
    return np.where(p < .22, up, np.where(p < .70, down, 0.0))

def beach_overlay(c, X, Y, sdf, n40, n22, t, sea_tint):
    sh = X.shape; W = C(0xFFFFFF, sh)
    d = np.maximum(-sdf, 0) + (n40-.5)*6
    water = sstep(.6, -.6, sdf)
    # --- hladina: pomaly tečúce svetlo
    fl = fbm((X + t*7)/150, (Y - t*3)/150, 901, 3)
    c = c*(1 + (fl-.5)*.10*sstep(6, 40, d)*water)[..., None]
    # --- kaustiky na plytčine
    F1, F2 = worley_anim(X, Y, 24, 77, t*.9)
    web = (1-sstep(0, 2.0, F2-F1))*(1-sstep(6, 46, d))*sstep(0, 5, d)*water
    c = mix(c, W, web*.13)
    # --- vlny bežiace k brehu: mäkké pásy peny, ktoré pri brehu hrubnú a lámu sa
    ph = d/WAVE_L + t/WAVE_T + (n22-.5)*.22
    fr = fract(ph)
    qd = np.minimum(fr, 1-fr)*WAVE_L                             # vzdialenosť od hrebeňa v bodoch
    near = 1-sstep(4, 55, d)
    width = 1.2 + 4.5*near
    amp = sstep(85, 35, d)*(.25+.65*near)
    br = sstep(.32, .7, vnoise(X/34 + t*.25, Y/34 - t*.15, 911))
    crest = (1-sstep(width*.35, width, qd))*amp*(.25+.75*br)
    behind = np.where(fr < .5, fr*WAVE_L, 99)                    # morská strana hrebeňa
    lace = np.exp(-behind/5)*amp*sstep(.55, .72, vnoise(X/9 + t*.2, Y/9, 912))*.45
    c = mix(c, W, np.clip(crest*.85 + lace, 0, 1)*water*sstep(1, 4, d))
    # --- odlesky slnka: riedke mäkké body, ktoré krátko zablikajú
    cell = 16.0
    ci = np.floor(X/cell); cj = np.floor(Y/cell)
    gx = (ci + .2 + .6*hash2(ci, cj, 931))*cell; gy = (cj + .2 + .6*hash2(ci, cj, 932))*cell
    rr = np.sqrt((X-gx)**2 + (Y-gy)**2)
    flick = np.maximum(0, np.sin(t*(1.3+1.7*hash2(ci, cj, 933)) + hash2(ci, cj, 934)*6.2832))**12
    glint = (1-sstep(.2, 1.1, rr))*flick*(hash2(ci, cj, 935) < .55)*sstep(20, 70, d)*(.4+.6*sstep(.45, .65, fl))
    c = mix(c, W, glint*.9*water)
    # --- výbeh vody na pláž, pena na čele, mokrý piesok
    s = np.maximum(sdf, 0)
    p = fract(t/WAVE_T + (n22-.5)*.22)
    Rmax = 13 + n40*12
    R = Rmax*swash_profile(p)
    sheet = sstep(R+1.2, R-1.2, s)*(R > .3)*(sdf > -1)
    adv = p < .22
    front = np.exp(-((s-R)/np.where(adv, 1.5, 1.0))**2)*sstep(.3, 2, R)*(.7+.3*vnoise(X/14 + t*.3, Y/14, 915))*(sdf > -1)
    # mokrosť: čas od chvíle, keď voda z bodu odišla
    v = np.clip(s/np.maximum(Rmax, 1), 0, 1)
    pr = .22 + .48*(2/np.pi)*np.arccos(v)
    dt = fract(p - pr)*WAVE_T
    wet = np.exp(-dt/2.4)*(s < Rmax)*(1-sheet)
    wet = np.maximum(wet, (1-sstep(0, 5, s))*.6)*(sdf > 0)
    c = mix(c, C(0xDCCBAA, sh), wet*.6)
    c = c + (wet*.03)[..., None]                                # vlhký lesk
    tint = mix(C(sea_tint, sh), W, .15)
    c = mix(c, tint, sheet*.42*(1-.5*s/np.maximum(R, 1)))
    F1l, F2l = worley_anim(X, Y, 6, 91, t*.6)
    lace2 = (1-sstep(0, .7, F2l-F1l))*sheet*(1-adv)*sstep(R-8, R-1, s)*.3
    c = mix(c, W, np.clip(front*.95 + lace2, 0, 1))
    return c

def cloud_shadows(c, X, Y, t, strength=.11):
    cs = fbm((X - t*9)/420, (Y + t*2)/300, 921, 3)
    return c*(1 - sstep(.55, .78, cs)*strength)[..., None]

def render_video(name, W, H, crop, secs=7, fps=15, out='.'):
    D.LIVE = True
    th = D.THEMES[name]; s = th['seed']
    F = build_field(W, H, D.layout(W, H), th)
    base = D.compose(F, W, H, 1.0, name, s)
    x0, y0, x1, y1 = crop                                         # v pixeloch obrázka (riadok 0 = hore)
    bc = base[y0:y1, x0:x1].astype(np.float32)
    X, Y = np.meshgrid((np.arange(x0, x1)+.5), (H - (np.arange(y0, y1)+.5)))
    X = X.astype(np.float32); Y = Y.astype(np.float32)
    sdf = bil(F['sdf'], X, Y); n40 = fbm(X/40, Y/40, s+5, 2); n22 = fbm(X/22, Y/22, s+8, 2)
    os.makedirs(f'{out}/frames_{name}', exist_ok=True)
    from PIL import Image
    for k in range(int(secs*fps)):
        t = k/fps
        c = beach_overlay(bc.copy(), X, Y, sdf, n40, n22, t, 0x9FE3DA)
        c = cloud_shadows(c, X, Y, t)
        Image.fromarray((np.clip(c, 0, 1)*255).astype(np.uint8)).save(f'{out}/frames_{name}/{k:04d}.png')
    subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-framerate', str(fps), '-i', f'{out}/frames_{name}/%04d.png',
                    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '20', f'{out}/{name}.mp4'], check=True)

def wind_overlay(c, X, Y, t, grass, canopy, cloud):
    """Poryvy vetra: svetlé vlny prebiehajúce trávou, korune sa jemne pohupujú, oblaky „dýchajú“."""
    gust = fbm((X - t*42)/150, (Y + t*6)/80, 941, 3)
    ripple = vnoise((X - t*60)/26, Y/14, 942)
    light = sstep(.5, .78, gust)*(.6+.4*ripple)
    c = c*(1 + light*.09*grass)[..., None] + (light*grass*.015)[..., None]*np.array([.6, .9, -.2], np.float32)
    c = c*(1 - sstep(.3, .1, gust)*.05*grass)[..., None]
    sway = fbm((X - t*24)/70, (Y + t*8)/70, 943, 2)
    c = c*(1 + (sway-.5)*.08*canopy)[..., None]
    puff = fbm((X + t*4)/90, (Y - t*3)/90, 944, 2)
    return c*(1 + (puff-.5)*.05*cloud)[..., None]

def snow_overlay(c, X, Y, t, field):
    """Sneh v troch hĺbkach: väčšie vločky bližšie a rýchlejšie, všetky sa pohupujú."""
    sh = X.shape
    for cell, v, r, a, seed in ((46, 34, 2.2, .85, 951), (30, 22, 1.5, .7, 952), (18, 13, .95, .55, 953)):
        Ys = Y + t*v
        sw = np.sin(t*.9 + hash2(np.floor(X/cell), 0, seed+3)*6.28)*cell*.18
        Xs = X + sw + t*v*.25
        ci = np.floor(Xs/cell); cj = np.floor(Ys/cell)
        on = hash2(ci, cj, seed+2) < .55
        fx = (ci + .15 + .7*hash2(ci, cj, seed))*cell; fy = (cj + .15 + .7*hash2(ci, cj, seed+1))*cell
        d = np.sqrt((Xs-fx)**2 + (Ys-fy)**2)
        flake = (1-sstep(r*.35, r, d))*on*a
        c = mix(c, C(0xFFFFFF, sh), flake)
    # trblietky na snehovej pláni
    cell = 12.0
    ci = np.floor(X/cell); cj = np.floor(Y/cell)
    gx = (ci + .2 + .6*hash2(ci, cj, 961))*cell; gy = (cj + .2 + .6*hash2(ci, cj, 962))*cell
    rr = np.sqrt((X-gx)**2 + (Y-gy)**2)
    flick = np.maximum(0, np.sin(t*(1.1+1.4*hash2(ci, cj, 963)) + hash2(ci, cj, 964)*6.2832))**16
    return mix(c, C(0xFFFFFF, sh), (1-sstep(.15, .9, rr))*flick*(hash2(ci, cj, 965) < .5)*field*.9)

def render_land(name, W, H, crop, secs=6, fps=15, out='.'):
    from PIL import Image
    th = D.THEMES[name]; s = th['seed']
    F = build_field(W, H, D.layout(W, H), th)
    base = D.compose(F, W, H, 1.0, name, s)
    x0, y0, x1, y1 = crop
    bc = base[y0:y1, x0:x1].astype(np.float32)
    X, Y = np.meshgrid((np.arange(x0, x1)+.5), (H - (np.arange(y0, y1)+.5)))
    X = X.astype(np.float32); Y = Y.astype(np.float32)
    sdf = bil(F['sdf'], X, Y)
    P = dict(X=X, Y=Y, F=F, L=bil(F['large'], X, Y), d=bil(F['detail'], X, Y))
    tree, _ = D.forest_mask(P, s, .22 if name == 'zima' else .5)
    region = sstep(-1, 1, sdf)
    os.makedirs(f'{out}/frames_{name}', exist_ok=True)
    for k in range(int(secs*fps)):
        t = k/fps
        c = bc.copy()
        if name == 'les':
            c = wind_overlay(c, X, Y, t, (1-tree)*(1-region), tree*(1-region), region)
            c = cloud_shadows(c, X, Y, t, .13)
        else:
            c = cloud_shadows(c, X, Y, t, .06)
            c = snow_overlay(c, X, Y, t, region)
        Image.fromarray((np.clip(c, 0, 1)*255).astype(np.uint8)).save(f'{out}/frames_{name}/{k:04d}.png')
    subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-framerate', str(fps), '-i', f'{out}/frames_{name}/%04d.png',
                    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '20', f'{out}/{name}.mp4'], check=True)

if __name__ == '__main__' and len(sys.argv) > 1 and sys.argv[1] in ('les', 'zima'):
    D.LIVE = False
    render_land(sys.argv[1], 1600, 900, (300, 160, 1020, 600), secs=6)
    print('ok'); sys.exit()

if __name__ == '__main__':
    render_video('plaz', 1600, 900, (380, 160, 1100, 600), secs=float(sys.argv[1]) if len(sys.argv) > 1 else 7, out='.')
    print('ok')
