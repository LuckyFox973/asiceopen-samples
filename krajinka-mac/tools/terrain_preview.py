# Krajinka v2 – dizajnový náhľad (numpy = pixelová fáza, cairo = vektorová fáza ≈ CoreGraphics)
# Súradnice: body, os y HORE (ako CoreGraphics), počiatok vľavo dole.
import numpy as np, cairo, math, sys

U = np.uint32
def hash2(x, y, seed):
    x = np.asarray(x).astype(np.int64).astype(np.uint32); y = np.asarray(y).astype(np.int64).astype(np.uint32)
    with np.errstate(over='ignore'):
        h = x * U(374761393); h = h + y * U(668265263); h = h + U(seed) * U(2246822519)
        h = (h ^ (h >> U(13))) * U(1274126177); h ^= h >> U(16)
    return (h & U(0xFFFFFF)).astype(np.float32) / np.float32(0xFFFFFF)
def h1(i, j, seed): return float(hash2(i, j, seed))
def smooth(t): return t*t*(3-2*t)
def sstep(a, b, x): return smooth(np.clip((x-a)/(b-a), 0, 1))
def vnoise(x, y, seed):
    xf = np.floor(x); yf = np.floor(y); tx = smooth(x-xf); ty = smooth(y-yf)
    ix = xf.astype(np.int64); iy = yf.astype(np.int64)
    a = hash2(ix, iy, seed); b = hash2(ix+1, iy, seed); c = hash2(ix, iy+1, seed); d = hash2(ix+1, iy+1, seed)
    top = a+(b-a)*tx; bot = c+(d-c)*tx; return top+(bot-top)*ty
def fbm(x, y, seed, octaves):
    s = 0; amp = .5; fr = 1; norm = 0
    for o in range(octaves):
        off = o*17.31; s = s + amp*vnoise(x*fr+off, y*fr-off, seed); norm += amp; amp *= .5; fr *= 2
    return s/norm
def rgb(h): return np.array([(h>>16)&255, (h>>8)&255, h&255], np.float32)/255

SHORE, CLEAR = .42, .74
ONLY_ICONS = True
CELL = 6.0

THEMES = {
 'beach': dict(seed=1, wl=.30,
   deepest=0x0B3A68, deep=0x1769A0, shallow=0x35BEC8, shelf=0x9BE7E1, lip=(0xFFFFFF, .80),
   sand=0xEDD5A1, wetSand=0xCDB079, beachW=15.0,
   land=0x7FB550, landAlt=0x5E9A3C, micro=.07, clearing=0xD9BD86, clearingEdge=0xB59A64, shadowTint=0x2F5D46,
   path=0xE2C891, pathEdge=0xA88C5A, wood=0xA5764A, woodDark=0x6E4B2C,
   trunk=0x9C7446, trunkDark=0x76532F, fDark=0x23703A, fMid=0x3F9444, fLight=0x8CCB5C,
   rock=0x77858C, rockLight=0xB9C3C6, accent=0xF0806E, flowers=[0xFFD166, 0xFF8FA3, 0xFFFFFF],
   ring=.55, shade=1.0),
 'meadow': dict(seed=2, wl=.50,
   deepest=0x173F5A, deep=0x245F7C, shallow=0x4A92A3, shelf=0x8CC6C7, lip=(0xE4F2EA, .40),
   sand=0x8E8F57, wetSand=0x6C7243, beachW=4.0,
   land=0x7DB352, landAlt=0x5F9741, micro=.07, clearing=0x9DC76D, clearingEdge=0x739F4B, shadowTint=0x24502E,
   path=0xD1B57F, pathEdge=0x8E7548, wood=0x9A6E45, woodDark=0x654527,
   trunk=0x7A5533, trunkDark=0x5A3D23, fDark=0x285F2B, fMid=0x41853A, fLight=0x93CD62,
   rock=0x7D7F77, rockLight=0xB0B2A8, accent=0xE86A92, flowers=[0xFFFFFF, 0xF7D046, 0xE86A92, 0xB58CE0],
   ring=.28, shade=1.0),
 'winter': dict(seed=3, wl=.46,
   deepest=0x6283A4, deep=0x84A5C3, shallow=0xAFCBDF, shelf=0xD6E7F2, lip=(0xFFFFFF, .55),
   sand=0xE6EEF5, wetSand=0xCCD9E6, beachW=5.0,
   land=0xF1F5F9, landAlt=0xE0E9F1, micro=.035, clearing=0xBACADB, clearingEdge=0x9EB2C7, shadowTint=0x7F97B8,
   path=0xD5DFEA, pathEdge=0xA3B4C7, wood=0x8C6A4E, woodDark=0x5C432F,
   trunk=0x5E4A3A, trunkDark=0x46362A, fDark=0x1C4639, fMid=0x2B5E4D, fLight=0x4C8A70,
   rock=0x6B7684, rockLight=0xA4AFBC, accent=0xFFFFFF, flowers=[],
   ring=0.0, shade=1.25),
}

# ---------------------------------------------------------------- pole
def build_field(W, H, icons, th):
    cols = int(np.ceil(W/CELL))+2; rows = int(np.ceil(H/CELL))+2
    X, Y = np.meshgrid(np.arange(cols, dtype=np.float32)*CELL, np.arange(rows, dtype=np.float32)*CELL)
    if ONLY_ICONS:
        f = .16 + (fbm(X/140, Y/140, th['seed'], 3)-.5)*.34
    else:
        f = th['wl'] + (fbm(X/280, Y/280, th['seed'], 4)-.5)*.95
    # e = vzdialenosť od pobrežia ostrova daného dokumentu (záporná = na ostrove)
    e1 = np.full_like(f, np.inf); d1 = np.full_like(f, np.inf); d2 = np.full_like(f, np.inf)
    wob = fbm(X/55, Y/55, th['seed']+555, 3)
    for k, (x0,y0,x1,y1) in enumerate(icons):
        cr = min(32, (x1-x0)/2, (y1-y0)/2)                          # zaoblené rohy
        ox = (h1(k, 1, 901)-.5)*16; oy = (h1(k, 2, 901)-.5)*16      # ostrov mierne mimo stredu
        dx = np.maximum(np.maximum(x0+cr+ox-X, 0), X-(x1-cr+ox)); dy = np.maximum(np.maximum(y0+cr+oy-Y, 0), Y-(y1-cr+oy))
        dr = np.maximum(np.sqrt(dx*dx+dy*dy)-cr, 0)
        dxx = np.maximum(np.maximum(x0-X, 0), X-x1); dyy = np.maximum(np.maximum(y0-Y, 0), Y-y1)
        draw_ = np.sqrt(dxx*dxx+dyy*dyy); d2 = np.where(draw_ < d1, d1, np.minimum(d2, draw_)); d1 = np.minimum(d1, draw_)
        R = 30 + 16*h1(k, 3, 901)                                   # každý ostrov inak veľký
        e = dr - (R + (wob-.5)*30)
        e1 = np.minimum(e1, e)
    clear = np.zeros_like(f)
    if icons:
        isl = SHORE - e1*.012
        sep = np.where(np.isfinite(d2), d2-d1, 1e9)                  # os medzi rámikmi susedov
        isl = isl - .6*smooth(np.clip(1-sep/28, 0, 1))             # prieliv medzi susedmi
        isl = np.maximum(isl, np.where(d1 < 2, SHORE+.05, -1))      # dokument nikdy nestojí vo vode
        f = np.maximum(f, isl)
        clear = (1 - sstep(0, 14, d1)).astype(np.float32)
    detail = fbm(X/90, Y/90, th['seed']+977, 2)
    micro = fbm(X/16, Y/16, th['seed']+313, 2)
    # gradient (na bod)
    gx = np.zeros_like(f); gy = np.zeros_like(f)
    gx[:,1:-1] = (f[:,2:]-f[:,:-2])/(2*CELL); gy[1:-1,:] = (f[2:,:]-f[:-2,:])/(2*CELL)
    sdf = signed_distance(f-SHORE, gx, gy)
    return dict(f=f, clear=clear.astype(np.float32), detail=detail, micro=micro, gx=gx, gy=gy, sdf=sdf, cols=cols, rows=rows)

def signed_distance(s, gx, gy):
    """Vzdialenosť od pobrežia v bodoch (+ pevnina, – voda): subpixelové semienka + 2-priechodový chamfer."""
    rows, cols = s.shape; INF = 1e9
    g = np.sqrt(gx*gx+gy*gy); g = np.maximum(g, 1e-4)
    pos = s >= 0
    edge = np.zeros_like(pos)
    edge[:,1:] |= pos[:,1:] != pos[:,:-1]; edge[:,:-1] |= pos[:,:-1] != pos[:,1:]
    edge[1:,:] |= pos[1:,:] != pos[:-1,:]; edge[:-1,:] |= pos[:-1,:] != pos[1:,:]
    d = np.where(edge, np.minimum(np.abs(s)/g, CELL*1.5), INF).astype(np.float64)
    a = CELL; b = CELL*math.sqrt(2)
    def rowsweep(r):
        idx = np.arange(cols)*a
        r = np.minimum(r, np.minimum.accumulate(r - idx) + idx)         # zľava
        ridx = idx[::-1]
        rr = r[::-1]; rr = np.minimum(rr, np.minimum.accumulate(rr - idx) + idx); return rr[::-1]
    for j in range(rows):                                               # dopredu
        if j > 0:
            p = d[j-1]; cand = p + a
            cand[1:] = np.minimum(cand[1:], p[:-1]+b); cand[:-1] = np.minimum(cand[:-1], p[1:]+b)
            d[j] = np.minimum(d[j], cand)
        d[j] = rowsweep(d[j])
    for j in range(rows-2, -1, -1):                                     # dozadu
        p = d[j+1]; cand = p + a
        cand[1:] = np.minimum(cand[1:], p[:-1]+b); cand[:-1] = np.minimum(cand[:-1], p[1:]+b)
        d[j] = rowsweep(np.minimum(d[j], cand))
    return np.where(pos, d, -d).astype(np.float32)

def bil(A, x, y):
    rows, cols = A.shape
    gx = np.clip(np.asarray(x)/CELL, 0, cols-1.001); gy = np.clip(np.asarray(y)/CELL, 0, rows-1.001)
    ix = gx.astype(int); iy = gy.astype(int); fx = gx-ix; fy = gy-iy
    a = A[iy, ix]; b = A[iy, ix+1]; c = A[iy+1, ix]; d = A[iy+1, ix+1]
    t = a+(b-a)*fx; return t + ((c+(d-c)*fx) - t)*fy

LIGHT = np.array([-.5, .6, .62]); LIGHT = LIGHT/np.linalg.norm(LIGHT)

# ---------------------------------------------------------------- pixely
def shade_pixels(F, W, H, scale, th):
    w, h = int(W*scale), int(H*scale)
    xs = (np.arange(w)+.5)/scale; ys = (np.arange(h)[::-1]+.5)/scale
    X, Y = np.meshgrid(xs, ys)
    f = bil(F['f'], X, Y); d = bil(F['detail'], X, Y); m = bil(F['micro'], X, Y)
    gx = bil(F['gx'], X, Y); gy = bil(F['gy'], X, Y); sdf = bil(F['sdf'], X, Y)
    C = {k: rgb(th[k]) for k in ['deepest','deep','shallow','shelf','sand','wetSand','land','landAlt','clearing','clearingEdge','shadowTint']}
    mix = lambda a, b, t: a + (b-a)*np.asarray(t)[..., None]
    out = np.zeros((h, w, 3), np.float32)

    # voda
    depth = np.maximum(-sdf, 0)
    t1 = 1-np.exp(-depth/45); t2 = 1-np.exp(-depth/230)
    wc = mix(C['shallow'], C['deep'], t1); wc = mix(wc, C['deepest'], t2*.85)
    wc = wc + ((m-.5)*.06*(1-t1))[..., None]                         # piesočné dno na plytčine
    wc = wc + ((d-.5)*.05*t1)[..., None]                              # žíhanie hlbokej vody
    shelf = np.exp(-((depth-24)/3.2)**2)*.22                           # podvodný prah
    wc = mix(wc, C['shelf'], shelf)
    wc = mix(wc, np.broadcast_to(rgb(th['lip'][0]), wc.shape), np.clip(1-depth/2.6, 0, 1)*th['lip'][1])
    wc = mix(wc, np.broadcast_to(C['shelf'], wc.shape), (Y/H)*.07)    # odraz oblohy hore

    # pevnina
    inland = np.maximum(sdf, 0)
    patch = sstep(.36, .64, d)
    calm = bil(F['clear'], X, Y)
    lc = mix(C['land'], C['landAlt'], patch*(1-.6*calm)) + ((m-.5)*th['micro']*(1-.6*calm))[..., None]
    sandc = mix(C['sand'], C['wetSand'], np.clip(1-inland/3.5, 0, 1)*.55) + ((m-.5)*.03)[..., None]
    lc = mix(sandc, lc, sstep(th['beachW']-2, th['beachW']+3, inland))
    calm = bil(F['clear'], X, Y)
    tc = np.zeros_like(calm)
    edge = np.zeros_like(calm)
    lc = mix(lc, C['clearingEdge'], edge*(1-tc))
    lc = mix(lc, C['clearing'] + ((d-.5)*.03)[..., None], tc)
    # tieňovanie reliéfu
    K = 22
    nx, ny = -gx*K, -gy*K; nz = np.ones_like(nx); nl = np.sqrt(nx*nx+ny*ny+1)
    sh = (nx*LIGHT[0]+ny*LIGHT[1]+nz*LIGHT[2])/nl - LIGHT[2]
    sh = np.clip(sh*th['shade'], -.35, .35)*(1-tc*.75)
    lc = lc*(1+np.maximum(sh, 0)*1.1)[..., None]
    lc = mix(lc, lc*C['shadowTint']*1.6, np.maximum(-sh, 0)*1.6)

    land = sdf >= 0
    aa = np.clip(sdf/.6+.5, 0, 1)                                       # antialias pobrežia
    out = mix(wc, lc, aa)
    # vinetácia (elipsa podľa pomeru strán) + zrno
    vx = (X/W-.5)*2; vy = (Y/H-.5)*2; r = np.sqrt(vx*vx+vy*vy)/1.2
    out = out*(1-.10*sstep(.55, 1.15, r))[..., None]
    grain = (hash2(np.arange(w)[None, :]+np.zeros((h, 1)), np.arange(h)[:, None]+np.zeros((1, w)), 7)-.5)*.018
    out = out + grain[..., None]
    return np.clip(out, 0, 1), dict(sdf=sdf, depth=depth)

# ---------------------------------------------------------------- vektor
class Pen:
    def __init__(self, surf, H, scale):
        self.c = cairo.Context(surf); self.c.translate(0, H*scale); self.c.scale(scale, -scale)
    def rgba(self, hexv, a=1.0):
        r, g, b = rgb(hexv); self.c.set_source_rgba(float(r), float(g), float(b), a)

def soft_shadow(p, cx, cy, rx, ry, alpha):
    c = p.c; c.save(); c.translate(cx, cy); c.scale(1, ry/rx)
    g = cairo.RadialGradient(0, 0, 0, 0, 0, rx); g.add_color_stop_rgba(0, 0, 0, 0, alpha); g.add_color_stop_rgba(.55, 0, 0, 0, alpha*.7); g.add_color_stop_rgba(1, 0, 0, 0, 0)
    c.set_source(g); c.arc(0, 0, rx, 0, 2*math.pi); c.fill(); c.restore()

def shaded_blob(p, cx, cy, r, dark, light, mid=None):
    c = p.c
    g = cairo.RadialGradient(cx-r*.38, cy+r*.42, r*.05, cx, cy, r*1.05)
    L = rgb(light); D = rgb(dark); M = rgb(mid) if mid is not None else (L+D)/2
    g.add_color_stop_rgb(0, *map(float, L)); g.add_color_stop_rgb(.45, *map(float, M)); g.add_color_stop_rgb(1, *map(float, D))
    c.set_source(g); c.arc(cx, cy, r, 0, 2*math.pi); c.fill()

def draw_tree(p, x, y, s, th, seed):
    R = s*.44
    p.rgba(th['trunkDark']); p.c.rectangle(x-s*.06, y, s*.12, s*.32); p.c.fill()
    blobs = [(-.48, -.22, .68), (.46, -.18, .7), (.05, .32, .66), (0, 0, 1.0)]
    for k, (ox, oy, rr) in enumerate(blobs):
        j = h1(k, seed, 5)*.15
        shaded_blob(p, x+ox*R, y+s*.62+oy*R+j*R, R*rr*(.92+j), th['fDark'], th['fLight'], th['fMid'])
    p.rgba(th['fLight'], .55)
    for k in range(3):
        a = 2.2 + k*.35; rr = R*(.45+.12*k)
        p.c.arc(x+math.cos(a)*rr*.6, y+s*.62+math.sin(a)*rr*.6, s*.035, 0, 2*math.pi); p.c.fill()

def draw_palm(p, x, y, s, th, seed):
    c = p.c; lean = (h1(seed, 1, 9)-.5)*.5
    top = (x+s*(.18+lean*.3), y+s*.95); ctrl = (x+s*(.02+lean*.2), y+s*.55)
    n = 8
    for k in range(n):                                                  # kmeň z prstencov
        t0 = k/n; t1 = (k+1)/n
        def q(t): return ((1-t)**2*x+2*(1-t)*t*ctrl[0]+t*t*top[0], (1-t)**2*y+2*(1-t)*t*ctrl[1]+t*t*top[1])
        a, b = q(t0), q(t1); wdt = s*(.14-.06*t0)
        p.rgba(th['trunk'] if k % 2 == 0 else th['trunkDark']); c.set_line_width(wdt); c.set_line_cap(cairo.LINE_CAP_ROUND)
        c.move_to(*a); c.line_to(*b); c.stroke()
    leaves = 7; base = h1(seed, 2, 9)*6.28
    for layer in (0, 1):
        for k in range(leaves):
            if (k % 2) != layer: continue
            ang = base + k/leaves*2*math.pi; L = s*.62*(.85+.3*h1(seed, k, 3)); w = s*.15; droop = s*.18
            c.save(); c.translate(*top); c.rotate(ang)
            # list: horná hrana, špička, dolná hrana (prehnutý dole = –y v lokálnych súradniciach po rotácii)
            c.move_to(0, 0); c.curve_to(L*.3, w*1.1, L*.75, w*.6, L, -droop*.5)
            c.curve_to(L*.7, -w*.2, L*.3, -w*.7, 0, 0); c.close_path()
            g = cairo.LinearGradient(0, 0, L, 0)
            D = rgb(th['fDark']); M = rgb(th['fMid']); Lc = rgb(th['fLight'])
            if layer == 0: g.add_color_stop_rgb(0, *map(float, D)); g.add_color_stop_rgb(1, *map(float, M))
            else: g.add_color_stop_rgb(0, *map(float, M)); g.add_color_stop_rgb(.6, *map(float, Lc)); g.add_color_stop_rgb(1, *map(float, M))
            c.set_source(g); c.fill()
            p.rgba(th['fDark'], .5); c.set_line_width(s*.018); c.move_to(s*.03, 0); c.curve_to(L*.4, w*.25, L*.75, 0, L*.97, -droop*.48); c.stroke()
            c.restore()
    p.rgba(0x5A3A1E)
    for k in range(3):
        a = base + k*2.1; c.arc(top[0]+math.cos(a)*s*.06, top[1]+math.sin(a)*s*.05, s*.05, 0, 2*math.pi); c.fill()

def draw_pine(p, x, y, s, th, seed, snowy):
    c = p.c
    p.rgba(th['trunkDark']); c.rectangle(x-s*.05, y, s*.1, s*.22); c.fill()
    tiers = 4
    for k in range(tiers):
        by = y+s*(.12+.24*k); half = s*(.46-.095*k); ay = by+s*.46
        sag = s*.05
        # ľavá (osvetlená) a pravá polovica
        for side, col in ((-1, th['fMid']), (1, th['fDark'])):
            c.move_to(x, ay); c.curve_to(x+side*half*.35, ay-s*.2, x+side*half*.8, by+s*.08, x+side*half, by)
            c.curve_to(x+side*half*.5, by-sag*.2, x+side*half*.2, by+sag, x, by+sag*.6); c.close_path()
            p.rgba(col); c.fill()
        if snowy:
            p.rgba(0xFFFFFF, .95)
            c.move_to(x-half*.98, by+s*.01); c.curve_to(x-half*.6, by+s*.07, x-half*.25, by+s*.06, x-half*.02, by+s*.08)
            c.curve_to(x-half*.3, by+s*.02, x-half*.65, by-s*.015, x-half*.98, by+s*.01); c.fill()
    if snowy:
        ay = y+s*(.12+.24*(tiers-1))+s*.46
        p.rgba(0xFFFFFF); c.move_to(x, ay); c.curve_to(x-s*.06, ay-s*.06, x-s*.1, ay-s*.11, x-s*.11, ay-s*.13)
        c.curve_to(x-s*.02, ay-s*.1, x+s*.05, ay-s*.12, x+s*.1, ay-s*.13); c.curve_to(x+s*.07, ay-s*.08, x+s*.03, ay-s*.04, x, ay); c.fill()

def draw_bush(p, x, y, s, th, seed, flowers):
    for k, (ox, oy, rr) in enumerate([(-.24, .18, .3), (.24, .2, .3), (0, .34, .36)]):
        shaded_blob(p, x+ox*s, y+oy*s, s*rr, th['fDark'], th['fLight'], th['fMid'])
    if flowers:
        for k in range(4):
            a = h1(seed, k, 4)*6.28; r = s*.28*h1(seed, k, 6)
            p.rgba(flowers[int(h1(seed, 0, 8)*len(flowers)) % len(flowers)])
            p.c.arc(x+math.cos(a)*r, y+s*.32+math.sin(a)*r, s*.045, 0, 2*math.pi); p.c.fill()

def draw_rock(p, x, y, s, th, seed, snowy, foam):
    c = p.c; n = 7; pts = []
    for k in range(n):
        a = k/n*2*math.pi; r = (.85+.3*h1(seed, k, 21))
        pts.append((x+math.cos(a)*s*.5*r, y+math.sin(a)*s*.34*r))
    if foam:
        p.rgba(0xFFFFFF, .45); c.set_line_width(1.6); c.save(); c.translate(x, y-s*.04); c.scale(1, .62)
        c.arc(0, 0, s*.66, 0, 2*math.pi); c.restore(); c.stroke()
    p.rgba(th['rock']); c.move_to(*pts[0]); [c.line_to(*q) for q in pts[1:]]; c.close_path(); c.fill()
    p.rgba(0xFFFFFF if snowy else th['rockLight'], .95 if snowy else 1)
    c.move_to(x+s*.06, y+s*.06); [c.line_to(*q) for q in pts[2:6]]; c.close_path(); c.fill()

def draw_tuft(p, x, y, s, th, seed):
    c = p.c; p.rgba(th['fDark'], .75); c.set_line_width(.9); c.set_line_cap(cairo.LINE_CAP_ROUND)
    for k in range(4):
        a = math.pi/2 + (k-1.5)*.35 + (h1(seed, k, 2)-.5)*.2; L = s*(.7+.4*h1(seed, k, 3))
        c.move_to(x+(k-1.5)*.8, y); c.line_to(x+math.cos(a)*L, y+math.sin(a)*L); c.stroke()

def draw_flowers(p, x, y, s, th, seed):
    fl = th['flowers']
    for k in range(6):
        a = h1(seed, k, 4)*6.28; r = s*h1(seed, k, 6)
        cx, cy = x+math.cos(a)*r, y+math.sin(a)*r*.7
        p.rgba(0x000000, .18); p.c.arc(cx+.5, cy-.5, 1.6, 0, 6.28); p.c.fill()
        p.rgba(fl[int(h1(seed, k, 8)*len(fl)) % len(fl)]); p.c.arc(cx, cy, 1.5, 0, 6.28); p.c.fill()

def draw_starfish(p, x, y, s, th, seed):
    c = p.c; rot = h1(seed, 1, 1)*6.28; p.rgba(th['accent'])
    for k in range(10):
        a = rot + k/10*2*math.pi; r = s*(.5 if k % 2 == 0 else .2)
        (c.move_to if k == 0 else c.line_to)(x+math.cos(a)*r, y+math.sin(a)*r)
    c.close_path(); c.fill()

def draw_reeds(p, x, y, s, th, seed):
    c = p.c; c.set_line_cap(cairo.LINE_CAP_ROUND)
    for k in range(5):
        ox = (k-2)*s*.12 + (h1(seed, k, 1)-.5)*s*.1; L = s*(.7+.5*h1(seed, k, 2)); bend = (h1(seed, k, 3)-.5)*s*.25
        p.rgba(th['fMid']); c.set_line_width(1.0); c.move_to(x+ox, y); c.curve_to(x+ox, y+L*.5, x+ox+bend, y+L*.8, x+ox+bend, y+L); c.stroke()
        if h1(seed, k, 4) < .5:
            p.rgba(0x6B4426); c.save(); c.translate(x+ox+bend*.95, y+L*.88); c.scale(.45, 1); c.arc(0, 0, s*.1, 0, 6.28); c.restore(); c.fill()

def draw_lily(p, x, y, s, th, seed):
    c = p.c; a = h1(seed, 1, 1)*6.28
    p.rgba(0x000000, .12); c.save(); c.translate(x+1, y-1); c.scale(1, .8); c.arc(0, 0, s*.5, 0, 6.28); c.restore(); c.fill()
    c.save(); c.translate(x, y); c.scale(1, .8)
    c.move_to(0, 0); c.arc(0, 0, s*.5, a+.35, a-.35+2*math.pi); c.close_path()
    g = cairo.RadialGradient(-s*.15, s*.15, 0, 0, 0, s*.5)
    g.add_color_stop_rgb(0, *map(float, rgb(th['fLight']))); g.add_color_stop_rgb(1, *map(float, rgb(th['fMid']))); c.set_source(g); c.fill()
    c.restore()
    if h1(seed, 2, 2) < .35:
        for k in range(6):
            b = k/6*6.28; p.rgba(0xF6B6C8); c.save(); c.translate(x+math.cos(b)*s*.12, y+math.sin(b)*s*.1); c.rotate(b); c.scale(1, .55); c.arc(0, 0, s*.13, 0, 6.28); c.restore(); c.fill()
        p.rgba(0xFFD166); c.arc(x, y, s*.07, 0, 6.28); c.fill()

def draw_crack(p, x, y, s, seed):
    c = p.c; pts = [(x, y)]; a = h1(seed, 0, 1)*6.28
    for k in range(5):
        a += (h1(seed, k, 2)-.5)*1.4; L = s*(.4+.6*h1(seed, k, 3))
        pts.append((pts[-1][0]+math.cos(a)*L, pts[-1][1]+math.sin(a)*L))
    for col, off, wd in ((0x3E5F80, (.6, -.6), 1.0), (0xFFFFFF, (0, 0), .8)):
        p.rgba(col, .35 if col != 0xFFFFFF else .7); c.set_line_width(wd)
        c.move_to(pts[0][0]+off[0], pts[0][1]+off[1]); [c.line_to(q[0]+off[0], q[1]+off[1]) for q in pts[1:]]; c.stroke()

# ---------------------------------------------------------------- chodníky a mosty
def mst(points):
    n = len(points); used = [False]*n; best = [(1e18, -1)]*n; best[0] = (0, -1); edges = []
    for _ in range(n):
        u = min((i for i in range(n) if not used[i]), key=lambda i: best[i][0]); used[u] = True
        if best[u][1] >= 0: edges.append((best[u][1], u))
        for v in range(n):
            if not used[v]:
                d = math.dist(points[u], points[v])
                if d < best[v][0]: best[v] = (d, u)
    return edges

def plan_paths(F, icons, th):
    """Vráti zoznam polyline úsekov: (typ 'land'|'bridge', [body])."""
    cs = [((a+c)/2, (b+d)/2) for (a, b, c, d) in icons]
    out = []
    if len(cs) < 2: return out
    for (i, j) in mst(cs):
        A, B = cs[i], cs[j]; L = math.dist(A, B)
        if L < 150 or L > 900: continue
        mx, my = (A[0]+B[0])/2, (A[1]+B[1])/2; px, py = -(B[1]-A[1])/L, (B[0]-A[0])/L
        bend = (h1(i, j, 77)-.5)*.35*L; C = (mx+px*bend, my+py*bend)
        n = max(2, int(L/3)); pts = []
        for k in range(n+1):
            t = k/n; pts.append(((1-t)**2*A[0]+2*(1-t)*t*C[0]+t*t*B[0], (1-t)**2*A[1]+2*(1-t)*t*C[1]+t*t*B[1]))
        P = np.array(pts); cv = bil(F['clear'], P[:, 0], P[:, 1]); sv = bil(F['sdf'], P[:, 0], P[:, 1])
        inside = np.where(cv < .5)[0]
        if len(inside) < 3: continue
        P = P[inside[0]:inside[-1]+1]; sv = sv[inside[0]:inside[-1]+1]
        water = sv < -.5
        # dĺžky vodných úsekov
        runs = []; k = 0
        while k < len(P):
            w = water[k]; e = k
            while e+1 < len(P) and water[e+1] == w: e += 1
            runs.append((w, k, e)); k = e+1
        if any(w and (e-k)*3 > 220 for (w, k, e) in runs): continue
        for (w, k, e) in runs:
            if w: k = max(0, k-2); e = min(len(P)-1, e+2)
            out.append(('bridge' if w else 'land', P[k:e+1]))
    return out

def draw_paths(p, segs, th):
    c = p.c; c.set_line_cap(cairo.LINE_CAP_ROUND); c.set_line_join(cairo.LINE_JOIN_ROUND)
    def poly(P):
        c.move_to(*P[0]); [c.line_to(*q) for q in P[1:]]
    for kind, P in segs:
        if kind != 'land' or len(P) < 2: continue
        p.rgba(th['pathEdge'], .45); c.set_line_width(10); poly(P); c.stroke()
        p.rgba(th['path']); c.set_line_width(7); poly(P); c.stroke()
        for k in range(0, len(P), 3):
            if h1(k, len(P), 41) < .5:
                q = P[k]; o = (h1(k, 1, 42)-.5)*5; p.rgba(th['pathEdge'], .5); c.arc(q[0]+o, q[1]-o*.5, .9, 0, 6.28); c.fill()
    for kind, P in segs:
        if kind != 'bridge' or len(P) < 2: continue
        p.rgba(0x000000, .22); c.set_line_width(12); c.move_to(P[0][0]+3, P[0][1]-3); [c.line_to(q[0]+3, q[1]-3) for q in P[1:]]; c.stroke()
        p.rgba(th['wood']); c.set_line_width(9); poly(P); c.stroke()
        p.rgba(th['woodDark'], .8); c.set_line_width(.8)
        for k in range(1, len(P)-1):
            if k % 1 == 0:
                q0, q1 = P[k-1], P[k+1]; tx, ty = q1[0]-q0[0], q1[1]-q0[1]; tl = math.hypot(tx, ty) or 1; nx, ny = -ty/tl*4.5, tx/tl*4.5
                c.move_to(P[k][0]-nx, P[k][1]-ny); c.line_to(P[k][0]+nx, P[k][1]+ny); c.stroke()
        for side in (-1, 1):
            p.rgba(th['woodDark']); c.set_line_width(1.3); first = True
            for k in range(len(P)):
                q0, q1 = P[max(0, k-1)], P[min(len(P)-1, k+1)]; tx, ty = q1[0]-q0[0], q1[1]-q0[1]; tl = math.hypot(tx, ty) or 1
                pt = (P[k][0]-ty/tl*5*side, P[k][1]+tx/tl*5*side)
                (c.move_to if first else c.line_to)(*pt); first = False
            c.stroke()

# ---------------------------------------------------------------- rozmiestnenie
def place(F, W, H, icons, segs, th, name):
    pathpts = np.concatenate([P for _, P in segs]) if segs else np.zeros((0, 2))
    def blocked(x, y, rad):
        for (x0, y0, x1, y1) in icons:
            if x0-rad < x < x1+rad and y0-rad < y < y1+rad: return True
        if len(pathpts):
            d = np.min((pathpts[:, 0]-x)**2+(pathpts[:, 1]-y)**2)
            if d < (rad*.8+6)**2: return True
        return False
    items, ground = [], []
    seed = th['seed']
    g = 30
    for j in range(int(H/g)+2):
        for i in range(int(W/g)+2):
            x = (i+.1+.8*h1(i, j, seed+11))*g; y = (j+.1+.8*h1(i, j, seed+23))*g
            roll = h1(i, j, seed+37); sz = h1(i, j, seed+53); sid = int(h1(i, j, seed+61)*1e6)
            cl = float(bil(F['clear'], x, y)); sd = float(bil(F['sdf'], x, y)); grove = float(bil(F['detail'], x, y))
            land = sd > 0; inland = sd
            dens = sstep(.38, .62, grove)
            kind = None
            if name == 'beach':
                if land and inland > th['beachW']*.6 and roll < .35+.5*dens: kind = 'palm' if roll < .1+.45*dens else 'bush'
                elif land and inland > 3 and roll < .5: kind = 'bush'
                elif -40 < sd < -8 and roll < .05: kind = 'rockw'
            elif name == 'meadow':
                if land and inland > 5 and roll < .35+.5*dens: kind = 'tree' if roll < .05+.55*dens else 'bushf'
                elif -30 < sd < -6 and roll < .12: kind = 'lily'
                elif land and roll < .03: kind = 'rock'
            else:
                if land and inland > 4 and roll < .35+.5*dens: kind = 'pine'
                elif land and roll < .16+.72*dens: kind = 'rocks'
            if not kind: continue
            base = dict(palm=32, tree=36, pine=32, bush=17, bushf=17, rock=14, rocks=15, rockw=13, lily=13)[kind]
            s = base*(.8+.45*sz)
            bx0, by0, bx1, by1 = x-s*.6-5, y-s*.3-5, x+s*.6+5, y+s*1.15+5
            if any(not (bx1 < a0 or bx0 > a1 or by1 < b0 or by0 > b1) for (a0, b0, a1, b1) in icons): continue
            if blocked(x, y, 0): continue
            items.append((x, y, kind, s, sid))
    g = 20
    for j in range(int(H/g)+2):
        for i in range(int(W/g)+2):
            x = (i+.1+.8*h1(i, j, seed+71))*g; y = (j+.1+.8*h1(i, j, seed+83))*g
            roll = h1(i, j, seed+97); sid = int(h1(i, j, seed+101)*1e6)
            cl = float(bil(F['clear'], x, y)); sd = float(bil(F['sdf'], x, y))
            kind = None
            if name == 'beach':
                if 2 < sd < th['beachW']-3 and roll < .035: kind = 'star'
                elif sd > th['beachW']+4 and roll < .22: kind = 'tuft'
            elif name == 'meadow':
                if 0 < sd < 7 and roll < .35: kind = 'reeds'
                elif -6 < sd <= 0 and roll < .25: kind = 'reeds'
                elif sd > 8 and roll < .16: kind = 'flowers'
                elif sd > 8 and roll < .42: kind = 'tuft'
            else:
                if sd < -20 and roll < .012: kind = 'crack'
            if not kind: continue
            s = dict(star=6, tuft=6, reeds=12, flowers=7, crack=14)[kind]
            if blocked(x, y, 3 if kind != 'crack' else 2): continue
            ground.append((x, y, kind, s, sid))
    return items, ground

def draw_decor(p, items, ground, th, name):
    for (x, y, k, s, sid) in ground:
        {'tuft': lambda: draw_tuft(p, x, y, s, th, sid), 'flowers': lambda: draw_flowers(p, x, y, s, th, sid),
         'star': lambda: draw_starfish(p, x, y, s, th, sid), 'reeds': lambda: draw_reeds(p, x, y, s, th, sid),
         'crack': lambda: draw_crack(p, x, y, s, sid)}[k]()
    items = sorted(items, key=lambda it: -it[1])
    for (x, y, k, s, sid) in items:                                    # všetky tiene najprv
        if k in ('lily',): continue
        rx = s*(.62 if k in ('tree', 'pine', 'palm') else .45)
        dx = s*(.38 if k in ('tree', 'pine', 'palm') else .12)
        soft_shadow(p, x+dx, y-s*.08, rx, rx*.42, .30)
    for (x, y, k, s, sid) in items:
        if k == 'tree': draw_tree(p, x, y, s, th, sid)
        elif k == 'palm': draw_palm(p, x, y, s, th, sid)
        elif k == 'pine': draw_pine(p, x, y, s, th, sid, name == 'winter')
        elif k == 'bush': draw_bush(p, x, y, s, th, sid, None)
        elif k == 'bushf': draw_bush(p, x, y, s, th, sid, th['flowers'])
        elif k in ('rock', 'rocks', 'rockw'): draw_rock(p, x, y, s, th, sid, k == 'rocks', k == 'rockw')
        elif k == 'lily': draw_lily(p, x, y, s, th, sid)

# ---------------------------------------------------------------- statický snímok animácií
def draw_anim_frame(p, F, px, W, H, th, name, icons):
    c = p.c
    rng = np.random.default_rng(th['seed'])
    # vlny (prstence) – snímok v strede cyklu
    if th['ring'] > 0:
        pass  # kreslí sa v pixelovej fáze nižšie
    # oblaky: tieň + biely oblak
    for k in range(max(3, int(W/650))):
        cx = rng.uniform(0, W); cy = rng.uniform(0, H); sc = rng.uniform(.8, 1.4)
        for (dx, dy, col, a) in ((0, 0, 0x000000, .16 if name != 'winter' else .10), (70, 110, 0xFFFFFF, .22)):
            for b in range(8):
                bx = cx+dx+rng.uniform(-120, 120)*sc; by = cy+dy+rng.uniform(-35, 35)*sc; r = rng.uniform(55, 95)*sc
                g = cairo.RadialGradient(bx, by, 0, bx, by, r); r0, g0, b0 = map(float, rgb(col))
                g.add_color_stop_rgba(0, r0, g0, b0, a*.55); g.add_color_stop_rgba(1, r0, g0, b0, 0)
                c.set_source(g); c.arc(bx, by, r, 0, 6.28); c.fill()

def ring_overlay(px, th, phase):
    """Vlnové prstence pri brehu v danej fáze (0–1) – rovnaké ako ringMask × opacity v Swifte."""
    depth = px['depth']; sdf = px['sdf']
    a = np.zeros_like(depth)
    if th['ring'] <= 0: return a
    for k, d0 in enumerate(RING_DEPTHS):
        ph = (phase - RING_PHASES[k]) % 1.0
        env = np.interp(ph, [0, .18, .42, 1], [0, 1, 0, 0])
        ring = np.exp(-((depth-d0)/2.0)**2)*(sdf < 0)*(1-k*.18)
        a = np.maximum(a, ring*env*th['ring'])
    foam_env = np.interp((phase-.78) % 1, [0, .12, .5, 1], [.35, 1, .35, .35])
    foam = np.clip(1-depth/5, 0, 1)*(sdf < 0)*foam_env*th['ring']*.9
    return np.maximum(a, foam)
RING_DEPTHS = [6.0, 12.0, 19.0, 27.0]
RING_PHASES = [.6, .4, .2, 0.0]   # vonkajší prstenec ide prvý → vlna beží k brehu

def render(W, H, icons, name, scale, anim=True, phase=.55):
    th = THEMES[name]
    F = build_field(W, H, icons, th)
    col, px = shade_pixels(F, W, H, scale, th)
    if anim:
        ra = ring_overlay(px, th, phase)[..., None]; col = col + (1-col)*ra*.85
    h, w = col.shape[:2]
    arr = np.zeros((h, w, 4), np.uint8)
    arr[..., 0] = (col[..., 2]*255).astype(np.uint8); arr[..., 1] = (col[..., 1]*255).astype(np.uint8)
    arr[..., 2] = (col[..., 0]*255).astype(np.uint8); arr[..., 3] = 255
    surf = cairo.ImageSurface.create_for_data(memoryview(arr), cairo.FORMAT_ARGB32, w, h, w*4)
    p = Pen(surf, H, scale)
    segs = []
    items, ground = place(F, W, H, icons, segs, th, name)
    draw_decor(p, items, ground, th, name)
    if anim: draw_anim_frame(p, F, px, W, H, th, name, icons)
    # simulované ikony
    for (x0, y0, x1, y1) in icons:
        ix = x0+14; iy = y1-8
        p.rgba(0x000000, .25); p.c.rectangle(ix+2, iy-66, 64, 64); p.c.fill()
        p.rgba(0x4A86E8); p.c.rectangle(ix, iy-64, 64, 64); p.c.fill()
        p.rgba(0xFFFFFF, .95); p.c.rectangle(ix+8, iy-80, 48, 8); p.c.fill()
    surf.flush()
    return surf

def mac_icons(W, H, n):
    icons = []; x = W-110; y = H-40
    for i in range(n):
        icons.append((x-14, y-64-36, x+64+14, y+8)); y -= 100
        if y < 140: y = H-40; x -= 100
    return icons

if __name__ == '__main__':
    out = sys.argv[1]; which = sys.argv[2:] or ['beach', 'meadow', 'winter', 'mb']
    ic = mac_icons(5120, 1440, 7) + [(400-14, 900-36, 400+78, 900+72), (1500-14, 420-36, 1500+78, 420+72), (2600-14, 1100-36, 2600+78, 1100+72), (3300-14, 700-36, 3300+78, 700+72)]
    for name in which:
        if name == 'mb':
            render(1512, 982, mac_icons(1512, 982, 5) + [(500, 300, 592, 408)], 'beach', 1.0).write_to_png(f'{out}/macbook-beach.png')
        elif name.startswith('real'):
            n = name.split('-')[1]; W, H = 2560, 1100; ic = []
            def icon(x, y): return (x-6, y-135-6, x+95+6, y+6)
            for i in range(4):
                for j in range(2): ic.append(icon(20+i*131, H-10-j*218))
            for (x, y) in [(900, 760), (1031, 760), (1600, 560), (2300, 980), (1350, 330)]:
                ic.append(icon(x, y))
            render(W, H, ic, n, .7).write_to_png(f'{out}/real-{n}.png')
        elif name.startswith('crop'):
            n = name.split('-')[1]; s = render(1800, 900, [(300, 380, 392, 488), (900, 520, 992, 628), (1300, 250, 1392, 358)], n, 1.5); s.write_to_png(f'{out}/crop-{n}.png')
        else:
            render(5120, 1440, ic, name, .3).write_to_png(f'{out}/ultrawide-{name}.png')
    print('ok')
