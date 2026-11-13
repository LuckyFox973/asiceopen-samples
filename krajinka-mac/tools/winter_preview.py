import numpy as np, cairo, math, sys
import terrain_preview as D
from preview_base import hash2, h1, sstep, fbm, rgb, mix, C, build_field, bil, Pen, worley

def river_mask(X, Y, s):
    n = fbm(X/650, Y/650, s+700, 3)
    wob = (fbm(X/60, Y/60, s+701, 2)-.5)*.006
    gate = sstep(.42, .5, fbm(X/900, Y/900, s+703, 2))            # potok len miestami
    dist = np.abs(n - .5 + wob)/.6
    return np.where(gate > .5, dist, 1.0)                                  # potok tam, kde dist < ~0.018

def ground(P, s):
    X, Y = P['X'], P['Y']; sh = X.shape
    m = D.mottle(X, Y, s+600, 120)
    c = mix(C(0xEEF3F8, sh), C(0xDCE6F0, sh), D.sstep(.35, .7, m))
    c = mix(c, C(0xFFFFFF, sh), D.dots(X, Y, s+601, 5, .25, .5)*.6)
    dist = river_mask(X, Y, s)
    ice = 1-sstep(.012, .016, dist)
    bank = (1-sstep(.016, .026, dist))*(1-ice)
    streak = fbm(X/90, Y/9, s+702, 3)
    ic = mix(C(0xA9C7DC, sh), C(0xCFE2EE, sh), sstep(.3, .7, streak))
    ic = mix(ic, C(0x8DB0C9, sh), (1-sstep(.0, .006, .016-dist))*.6)     # tmavší okraj ľadu
    c = mix(c, C(0xC9D7E5, sh), bank*.6)                                  # modrastý tieň brehu
    return mix(c, ic, ice)

def spruce(p, x, y, r, seed):
    c = p.c
    # tieň
    g = cairo.RadialGradient(x+r*.45, y-r*.45, 0, x+r*.45, y-r*.45, r*1.15)
    g.add_color_stop_rgba(0, .25, .33, .45, .32); g.add_color_stop_rgba(1, .25, .33, .45, 0)
    c.set_source(g); c.arc(x+r*.45, y-r*.45, r*1.15, 0, 6.2832); c.fill()
    n = 7 + int(h1(seed, 1, 1)*3); rot = h1(seed, 2, 2)*6.28
    layers = [(1.0, 0x1F4038, 0x2F5E50), (.7, 0x28503F, 0x3D705E), (.42, 0x335E4F, 0x4E8270)]
    for k, (f, dark, mid) in enumerate(layers):
        R = r*f; Ri = R*.74
        c.save(); c.translate(x, y); c.rotate(rot + k*.35)
        for i in range(n*2):
            a = i/(n*2)*6.2832; rr = R if i % 2 == 0 else Ri
            rr *= .9 + .2*h1(seed+k, i, 3)
            (c.move_to if i == 0 else c.line_to)(math.cos(a)*rr, math.sin(a)*rr)
        c.close_path(); c.restore()
        gr = cairo.RadialGradient(x-R*.3, y+R*.3, 0, x, y, R)
        gr.add_color_stop_rgb(0, *map(float, rgb(mid))); gr.add_color_stop_rgb(1, *map(float, rgb(dark)))
        c.set_source(gr); c.fill()
        # sneh na vetvách: mäkké biele škvrny na osvetlenej (ľavej hornej) strane
        for i in range(n):
            a = rot + k*.35 + i/n*6.2832
            lit = .5 + .5*math.cos(a - 2.36)               # smer svetla zľava hore
            if lit < .25: continue
            sx = x + math.cos(a)*R*.58; sy = y + math.sin(a)*R*.58; sr = R*(.22+.16*lit)
            gs = cairo.RadialGradient(sx-sr*.3, sy+sr*.3, 0, sx, sy, sr)
            gs.add_color_stop_rgba(0, 1, 1, 1, .95*lit); gs.add_color_stop_rgba(.7, .93, .96, .99, .8*lit); gs.add_color_stop_rgba(1, .9, .94, .98, 0)
            c.set_source(gs); c.arc(sx, sy, sr, 0, 6.2832); c.fill()
    gs = cairo.RadialGradient(x-r*.08, y+r*.08, 0, x, y, r*.22)
    gs.add_color_stop_rgba(0, 1, 1, 1, .95); gs.add_color_stop_rgba(1, 1, 1, 1, 0)
    c.set_source(gs); c.arc(x, y, r*.22, 0, 6.2832); c.fill()

def render(W, H, icons, crop=None):
    th = D.THEMES['zima']; s = th['seed']
    F = build_field(W, H, icons, th)
    w, h = W, H
    X, Y = np.meshgrid((np.arange(w)+.5), (np.arange(h)[::-1]+.5)); X = X.astype(np.float32); Y = Y.astype(np.float32)
    P = dict(X=X, Y=Y, W=W, H=H, F=F, sdf=bil(F['sdf'], X, Y), calm=bil(F['calm'], X, Y), d=bil(F['detail'], X, Y),
             m=bil(F['micro'], X, Y), L=bil(F['large'], X, Y))
    edge = sstep(-1, 1, P['sdf'])
    bg = ground(P, s)
    bg = bg*(1-.10*(1-sstep(-12, 0, P['sdf']))*sstep(-24, -12, P['sdf']))[..., None]
    col = mix(bg, D.snowfield(P, s), edge) + D.grain(X, Y)[..., None]
    col = np.clip(col, 0, 1)
    arr = np.zeros((h, w, 4), np.uint8)
    arr[..., 0] = (col[..., 2]*255).astype(np.uint8); arr[..., 1] = (col[..., 1]*255).astype(np.uint8); arr[..., 2] = (col[..., 0]*255).astype(np.uint8); arr[..., 3] = 255
    surf = cairo.ImageSurface.create_for_data(memoryview(arr), cairo.FORMAT_ARGB32, w, h, w*4)
    p = Pen(surf, H, 1.0)
    trees = []
    g = 17
    for j in range(int(H/g)+2):
        for i in range(int(W/g)+2):
            x = (i+.1+.8*h1(i, j, s+11))*g; y = (j+.1+.8*h1(i, j, s+23))*g
            sd = float(bil(F['sdf'], x, y)); L = float(bil(F['large'], x, y)); dd = float(bil(F['detail'], x, y))
            dens = sstep(.3, .55, L + (dd-.5)*.3)
            if h1(i, j, s+37) > .35 + .6*dens: continue
            r = 8 + 7*h1(i, j, s+53)*(.6+.4*dens)
            if sd > -r-4: continue
            if float(river_mask(np.float32(x), np.float32(y), s)) < .02: continue
            if any(x0-r-3 < x < x1+r+3 and y0-r-3 < y < y1+r+3 for (x0, y0, x1, y1) in icons): continue
            trees.append((x, y, r, int(h1(i, j, s+61)*1e6)))
    for (x, y, r, sid) in sorted(trees, key=lambda t: -t[1]): spruce(p, x, y, r, sid)
    for (x0, y0, x1, y1) in icons:
        ix = (x0+x1)/2-24; iy = y1-14
        p.rgba(0x000000, .18); p.c.rectangle(ix+1.5, iy-50, 48, 48); p.c.fill()
        p.rgba(0x4A86E8); p.c.rectangle(ix, iy-48, 48, 48); p.c.fill()
    surf.flush(); return surf

if __name__ == '__main__':
    W, H = 1600, 900
    render(W, H, D.layout(W, H)).write_to_png(sys.argv[1] + '/zima.png'); print('ok')
