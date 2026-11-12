# Krajinka v3 – dizajn: „oblasť dokumentov“ × „okolie“ pre viac svetov.
# numpy = pixely, cairo = vektory (≈ CoreGraphics). Os y hore, jednotky = body.
import numpy as np, cairo, math, sys

U = np.uint32
def hash2(x, y, seed):
    x = np.asarray(x).astype(np.int64).astype(np.uint32); y = np.asarray(y).astype(np.int64).astype(np.uint32)
    with np.errstate(over='ignore'):
        h = x * U(374761393); h = h + y * U(668265263); h = h + U(seed) * U(2246822519)
        h = (h ^ (h >> U(13))) * U(1274126177); h ^= h >> U(16)
    return (h & U(0xFFFFFF)).astype(np.float32) / np.float32(0xFFFFFF)
def h1(i, j, s): return float(hash2(i, j, s))
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
def worley(x, y, cell, seed, jitter=.8):
    """F1, F2 a hash bunky najbližšieho bodu (pre parcely, koruny stromov, kopy oblakov)."""
    cx = np.floor(x/cell).astype(np.int64); cy = np.floor(y/cell).astype(np.int64)
    F1 = np.full(x.shape, 1e9, np.float32); F2 = np.full(x.shape, 1e9, np.float32); ID = np.zeros(x.shape, np.float32)
    for dj in (-1, 0, 1):
        for di in (-1, 0, 1):
            i = cx+di; j = cy+dj
            fx = (i + .5 + (hash2(i, j, seed)-.5)*jitter)*cell; fy = (j + .5 + (hash2(i, j, seed+1)-.5)*jitter)*cell
            d = np.sqrt((x-fx)**2 + (y-fy)**2)
            closer = d < F1
            F2 = np.where(closer, F1, np.minimum(F2, d)); ID = np.where(closer, hash2(i, j, seed+2), ID); F1 = np.minimum(F1, d)
    return F1, F2, ID
def rgb(h): return np.array([(h>>16)&255, (h>>8)&255, h&255], np.float32)/255
def mix(a, b, t): return a + (b-a)*np.asarray(t, np.float32)[..., None]
def C(h, shape): return np.broadcast_to(rgb(h), shape+(3,))

CELL = 6.0
LIGHT = np.array([-.5, .6, .62]); LIGHT = LIGHT/np.linalg.norm(LIGHT)

THEMES = {
  'plaz':    dict(seed=11, R=(44, 70), wob=40, k=50, style='sea',    region='sand'),
  'ostrovy': dict(seed=1,  R=(30, 46), wob=30, k=36, style='sea',    region='island'),
  'les':     dict(seed=5,  R=(34, 52), wob=30, k=44, style='forest', region='cloud'),
  'polia':   dict(seed=7,  R=(34, 52), wob=26, k=40, style='fields', region='pasture'),
  'mesto':   dict(seed=9,  R=(30, 44), wob=22, k=36, style='city',   region='park'),
  'zima':    dict(seed=3,  R=(36, 56), wob=30, k=44, style='snowforest', region='snow'),
}

# ---------------------------------------------------------------- pole oblasti
def smin(a, b, k):
    h = np.clip(.5 + .5*(b-a)/k, 0, 1); return b*(1-h) + a*h - k*h*(1-h)

def build_field(W, H, icons, th):
    cols = int(np.ceil(W/CELL))+2; rows = int(np.ceil(H/CELL))+2
    X, Y = np.meshgrid(np.arange(cols, dtype=np.float32)*CELL, np.arange(rows, dtype=np.float32)*CELL)
    s = th['seed']; wob = fbm(X/60, Y/60, s+555, 3)
    e = np.full_like(X, 1e4); d1 = np.full_like(X, 1e4)
    for k, (x0, y0, x1, y1) in enumerate(icons):
        cr = min(32, (x1-x0)/2, (y1-y0)/2)
        ox = (h1(k, 1, 901)-.5)*16; oy = (h1(k, 2, 901)-.5)*16
        dx = np.maximum(np.maximum(x0+cr+ox-X, 0), X-(x1-cr+ox)); dy = np.maximum(np.maximum(y0+cr+oy-Y, 0), Y-(y1-cr+oy))
        R = th['R'][0] + (th['R'][1]-th['R'][0])*h1(k, 3, 901)
        ek = np.maximum(np.sqrt(dx*dx+dy*dy)-cr, 0) - R
        e = smin(e, ek, th['k'])                                   # oblasti sa pri blízkosti plynulo spoja
        dxx = np.maximum(np.maximum(x0-X, 0), X-x1); dyy = np.maximum(np.maximum(y0-Y, 0), Y-y1)
        d1 = np.minimum(d1, np.sqrt(dxx*dxx+dyy*dyy))
    sdf = -(e + (wob-.5)*th['wob'])                                  # + vnútri oblasti
    sdf = np.where(d1 < 2, np.maximum(sdf, 6), sdf)                  # dokument nikdy mimo oblasti
    hgt = np.clip(sdf, 0, 40)*.012
    gx = np.zeros_like(hgt); gy = np.zeros_like(hgt)
    gx[:, 1:-1] = (hgt[:, 2:]-hgt[:, :-2])/(2*CELL); gy[1:-1, :] = (hgt[2:, :]-hgt[:-2, :])/(2*CELL)
    return dict(sdf=sdf.astype(np.float32), calm=(1-sstep(0, 14, d1)).astype(np.float32),
                detail=fbm(X/90, Y/90, s+977, 2), micro=fbm(X/16, Y/16, s+313, 2), large=fbm(X/240, Y/240, s+71, 3),
                gx=gx, gy=gy)

def bil(A, x, y):
    rows, cols = A.shape
    gx = np.clip(np.asarray(x)/CELL, 0, cols-1.001); gy = np.clip(np.asarray(y)/CELL, 0, rows-1.001)
    ix = gx.astype(int); iy = gy.astype(int); fx = gx-ix; fy = gy-iy
    a = A[iy, ix]; b = A[iy, ix+1]; c = A[iy+1, ix]; d = A[iy+1, ix+1]
    t = a+(b-a)*fx; return t + ((c+(d-c)*fx) - t)*fy

def relief(gx, gy, k=22):
    nx, ny = -gx*k, -gy*k; nl = np.sqrt(nx*nx+ny*ny+1)
    return (nx*LIGHT[0]+ny*LIGHT[1]+LIGHT[2])/nl - LIGHT[2]

# ---------------------------------------------------------------- okolia (pozadie)
def bg_sea(P, th):
    depth = np.maximum(-P['sdf'], 0); sh = P['X'].shape
    pal = dict(plaz=(0x0E7FA6, 0x14A3BF, 0x3FD3D3, 0xA8F0E6), ostrovy=(0x0B3A68, 0x1769A0, 0x35BEC8, 0x9BE7E1))[th['name']]
    deepest, deep, shallow, shelf = pal
    t1 = 1-np.exp(-depth/(60 if th['name'] == 'plaz' else 45)); t2 = 1-np.exp(-depth/260)
    c = mix(C(shallow, sh), C(deep, sh), t1); c = mix(c, C(deepest, sh), t2*.85)
    c = c + ((P['m']-.5)*.06*(1-t1))[..., None] + ((P['d']-.5)*.05*t1)[..., None]
    c = mix(c, C(shelf, sh), np.exp(-((depth-24)/3.2)**2)*.22)
    c = mix(c, C(0xFFFFFF, sh), np.clip(1-depth/2.6, 0, 1)*.8)
    return mix(c, C(shelf, sh), (P['Y']/P['H'])*.07)

def bg_forest(P, th, snowy=False):
    """Les zhora: veľké tieňované koruny (Worley); Les má aj lúky, Zima je súvislý les."""
    X, Y = P['X'], P['Y']; sh = X.shape
    if snowy:
        meadow, meadowAlt, gap, dark, mid, light = 0xDDE6EF, 0xCFDAE6, 0x1B3A33, 0x24493E, 0x3A6B59, 0xF4F8FB
        grove = np.ones(sh, np.float32)
    else:
        meadow, meadowAlt, gap, dark, mid, light = 0x9CC56A, 0x86B457, 0x1E3F1D, 0x2E5E2C, 0x4C8239, 0x86BD55
        grove = sstep(.47, .53, P['L'] + (P['d']-.5)*.25)
    c = mix(C(meadow, sh), C(meadowAlt, sh), sstep(.35, .65, P['d'])) + ((P['m']-.5)*.06)[..., None]
    cell = 17.0
    F1, F2, ID = worley(X, Y, cell, th['seed']+40, jitter=.75)
    r = 8.5 + ID*3.5
    crown = np.clip(1 - F1/r, 0, 1)
    F1s, _, IDs = worley(X+3, Y-3, cell, th['seed']+40, jitter=.75)
    lit = np.clip(1 - F1s/(8.5+IDs*3.5), 0, 1)
    tc = mix(C(gap, sh), C(dark, sh), sstep(0, .25, crown))
    tc = mix(tc, C(mid, sh), smooth(lit)*sstep(0, .2, crown))
    if snowy:
        # zasnežené koruny: sneh s modrastým tieňovaním, zelená len v tieni a v medzerách
        snowc = mix(C(0xA9BDD3, sh), C(0xF6F9FC, sh), sstep(.0, .75, lit))
        greenish = np.clip((1-lit)*sstep(0, .3, crown)*1.1 - .25, 0, 1)
        snowc = mix(snowc, C(dark, sh), greenish*.75)
        gapm = 1 - sstep(1.2, 4.0, F2-F1)
        tc = mix(snowc, C(gap, sh), np.maximum(gapm*.85, 1-sstep(0, .12, crown)))
    else:
        tc = mix(tc, C(light, sh), sstep(.7, 1, lit)*.55)
    tc = tc + ((P['m']-.5)*.04)[..., None]
    edge_bump = sstep(.0, .15, crown)
    tree = grove*np.maximum(edge_bump, sstep(.55, .62, grove))      # okraj lesa = jednotlivé koruny
    # tieň lesa na lúku (vpravo dole)
    gs = bil(P['F']['large'], X-6, Y+6); gs = sstep(.47, .53, gs + (bil(P['F']['detail'], X-6, Y+6)-.5)*.25)
    if not snowy:
        c = c*(1 - .3*gs*(1-tree))[..., None]
    return mix(c, tc, np.clip(tree, 0, 1))

def bg_snowground(P, th):
    sh = P['X'].shape
    c = mix(C(0xD9E3ED, sh), C(0xCBD7E4, sh), sstep(.35, .65, P['d'])) + ((P['m']-.5)*.04)[..., None]
    return c

def bg_fields(P, th):
    """Polia: parcely (Voronoi), plodiny so smerovými riadkami, živé ploty na hraniciach."""
    X, Y = P['X'], P['Y']; sh = X.shape
    F1, F2, ID = worley(X, Y, 170, th['seed']+60, jitter=.9)
    crops = [  # (farba, farba riadkov, sila riadkov)
        (0xE3C46A, 0xC9A64B, .5),   # pšenica
        (0x7FA346, 0x6A8C38, .55),  # mladé obilie (tmavšie)
        (0x9B7652, 0x7E5C3E, .9),   # oranisko
        (0xA48ACB, 0x7E64A8, .8),   # levanduľa
        (0xF2D84A, 0xD8BC2E, .3),   # repka
        (0xD9A35A, 0xBF8A44, .6),   # kukurica
        (0xC9B27A, 0xB09662, .6),   # strnisko
    ]
    k = np.minimum((ID*len(crops)).astype(int), len(crops)-1)
    base = np.zeros(sh+(3,), np.float32); row = np.zeros(sh+(3,), np.float32); st = np.zeros(sh, np.float32)
    for i, (a, b, s) in enumerate(crops):
        m = k == i; base[m] = rgb(a); row[m] = rgb(b); st[m] = s
    ang = ID*np.pi*7.3
    u = X*np.cos(ang) + Y*np.sin(ang)
    stripes = (np.sin(u/2.2)*.5+.5)**2
    c = mix(base, row, stripes*st) + ((P['m']-.5)*.05)[..., None]
    edge = F2 - F1
    hedge = 1 - sstep(2.5, 5.5, edge)
    HF1, _, HID = worley(X, Y, 5, th['seed']+61)
    bump = np.clip(1 - HF1/3.5, 0, 1)
    hc = mix(C(0x2F5E2A, sh), C(0x5C8F3E, sh), bump)
    # tieň živého plotu
    c = c*(1-.25*(1-sstep(3.5, 9, edge)))[..., None]
    c = mix(c, hc, hedge*sstep(.4, .45, P['L']*0+np.clip(ID*3 % 1, 0, 1)*0+.5))
    return c

def bg_city(P, th):
    """Mesto: mriežka ulíc (asfalt, chodníky, prerušované čiary); bloky – budovy kreslí vektor."""
    X, Y = P['X'], P['Y']; sh = X.shape
    bw, bh, sw = CITY['bw'], CITY['bh'], CITY['street']
    u = np.mod(X, bw); v = np.mod(Y, bh)
    ds = np.minimum(np.minimum(u, bw-u), np.minimum(v, bh-v))        # vzdialenosť od osi ulice
    street = ds < sw/2
    walk = (ds >= sw/2) & (ds < sw/2+3)
    c = C(0xC9C4BA, sh).copy() + ((P['m']-.5)*.05)[..., None]         # dvor bloku
    c[walk] = rgb(0xDAD6CE)
    asph = C(0x4A4E55, sh) + ((P['m']-.5)*.05)[..., None]
    c = np.where(street[..., None], asph, c)
    # stredová prerušovaná čiara
    dash_h = (np.abs(np.minimum(v, bh-v)) < .55) & (np.mod(X, 14) < 7) & (np.minimum(u, bw-u) > sw/2)
    dash_v = (np.abs(np.minimum(u, bw-u)) < .55) & (np.mod(Y, 14) < 7) & (np.minimum(v, bh-v) > sw/2)
    c[dash_h | dash_v] = rgb(0xE8E2C8)
    return c
CITY = dict(bw=150.0, bh=118.0, street=14.0)

# ---------------------------------------------------------------- oblasti dokumentov
def region_sand(P, th):
    sh = P['X'].shape; inl = np.maximum(P['sdf'], 0)
    c = mix(C(0xF2E7CF, sh), C(0xEADBBB, sh), sstep(.35, .65, P['d'])*(1-.6*P['calm']))
    c = c + ((P['m']-.5)*.035)[..., None]
    # zrnká piesku
    g = hash2(np.floor(P['X']*1.7), np.floor(P['Y']*1.7), 5)
    c = c - ((g > .985)*.10)[..., None] + ((g < .012)*.06)[..., None]
    c = mix(c, C(0xD9C59B, sh), np.clip(1-inl/7, 0, 1)*.55)            # mokrý piesok
    return c

def region_island(P, th):
    sh = P['X'].shape; inl = np.maximum(P['sdf'], 0); calm = 1-.6*P['calm']
    lc = mix(C(0x7FB550, sh), C(0x5E9A3C, sh), sstep(.36, .64, P['d'])*calm) + ((P['m']-.5)*.07*calm)[..., None]
    sandc = mix(C(0xEDD5A1, sh), C(0xCDB079, sh), np.clip(1-inl/3.5, 0, 1)*.55)
    lc = mix(sandc, lc, sstep(13, 18, inl))
    s = np.clip(relief(P['gx'], P['gy']), -.35, .35)
    lc = lc*(1+np.maximum(s, 0)*1.1)[..., None]
    return mix(lc, lc*C(0x2F5D46, sh)*1.6, np.maximum(-s, 0)*1.6)

def cloud_sdf(P, ox=0, oy=0):
    """Oblaková oblasť s kopovitým okrajom: sdf + kopy (Worley)."""
    X, Y = P['X']+ox, P['Y']+oy
    sdf = bil(P['F']['sdf'], X, Y) if (ox or oy) else P['sdf']
    F1, _, ID = worley(X, Y, 24, 333, jitter=.7)
    puff = (11 + ID*6) - F1
    return sdf + .9*np.clip(puff, -12, 20), F1, ID

def region_cloud(P, th):
    sh = P['X'].shape
    s, F1, ID = cloud_sdf(P)
    rr = 11 + ID*6
    dome = np.clip(1 - F1/rr, 0, 1)
    F1l, _, IDl = worley(P['X']+3, P['Y']-3, 24, 333, jitter=.7)
    lit = np.clip(1 - F1l/(11+IDl*6), 0, 1)
    c = mix(C(0xC9D8EA, sh), C(0xF7FAFD, sh), sstep(0, .7, lit*.8 + sstep(0, 30, s)*.6))
    c = mix(c, C(0xFFFFFF, sh), sstep(.5, 1, lit)*.6*(1-sstep(10, 30, s)))
    return c, s

def region_pasture(P, th):
    sh = P['X'].shape; inl = np.maximum(P['sdf'], 0); calm = 1-.6*P['calm']
    c = mix(C(0xB9E07E, sh), C(0xA6D46C, sh), sstep(.35, .65, P['d'])*calm) + ((P['m']-.5)*.06*calm)[..., None]
    # kvietky v tráve (mimo ikon)
    F1, _, ID = worley(P['X'], P['Y'], 9, 404)
    fl = (F1 < 1.1) & (ID > .55) & (P['calm'] < .3)
    cols = np.stack([rgb(0xFFFFFF), rgb(0xF7D046), rgb(0xE86A92)])
    c = np.where(fl[..., None], cols[(ID*7).astype(int) % 3], c)
    # drevený plot: dvojitá žrď + stĺpiky
    fence = np.exp(-((inl-4)/.55)**2)
    post = (np.mod(P['X']+P['Y'], 9) < 1.4) & (np.abs(inl-4) < 1.3)
    c = mix(c, C(0x8A6440, sh), fence*.9)
    c = np.where(post[..., None], rgb(0x6B4A2E), c)
    return mix(c, c*.82, np.clip(1-np.abs(inl-2.2)/1.2, 0, 1)*.6)    # tieň plotu

def region_park(P, th):
    sh = P['X'].shape; inl = np.maximum(P['sdf'], 0); calm = 1-.6*P['calm']
    c = mix(C(0x86C25C, sh), C(0x74B04E, sh), sstep(.35, .65, P['d'])*calm) + ((P['m']-.5)*.06*calm)[..., None]
    # pruhy kosenia
    stripes = (np.mod(P['X']+P['Y']*.0, 24) < 12)*.035*calm
    c = c + stripes[..., None]
    c = mix(c, C(0x3F7A33, sh), np.clip(1-np.abs(inl-3)/2.2, 0, 1))  # živý plot okolo parku
    return mix(c, C(0xD8D2C4, sh), np.clip(1-inl/1.2, 0, 1))          # obrubník

def region_snow(P, th):
    sh = P['X'].shape; calm = 1-.6*P['calm']
    c = mix(C(0xF7FAFD, sh), C(0xE6EEF6, sh), sstep(.35, .65, P['d'])*calm) + ((P['m']-.5)*.03*calm)[..., None]
    s = np.clip(relief(P['gx'], P['gy'])*1.3, -.35, .35)
    c = c*(1+np.maximum(s, 0)*.5)[..., None]
    return mix(c, c*C(0x8FA6C4, sh)*1.25, np.maximum(-s, 0)*1.6)

# ---------------------------------------------------------------- pixely
def shade_pixels(F, W, H, scale, th, name):
    w, h = int(W*scale), int(H*scale)
    X, Y = np.meshgrid((np.arange(w)+.5)/scale, (np.arange(h)[::-1]+.5)/scale)
    X = X.astype(np.float32); Y = Y.astype(np.float32)
    P = dict(X=X, Y=Y, W=W, H=H, F=F, sdf=bil(F['sdf'], X, Y), calm=bil(F['calm'], X, Y), d=bil(F['detail'], X, Y),
             m=bil(F['micro'], X, Y), L=bil(F['large'], X, Y), gx=bil(F['gx'], X, Y), gy=bil(F['gy'], X, Y))
    th = dict(th, name=name)
    style, region = th['style'], th['region']
    bg = {'sea': bg_sea, 'forest': bg_forest, 'fields': bg_fields, 'city': bg_city,
          'snowforest': bg_snowground}[style](P, th)
    if region == 'cloud':
        rc, s = region_cloud(P, th)
        sshadow, _, _ = cloud_sdf(P, -16, 20)                          # tieň oblakov na lese
        bg = bg*(1 - .32*sstep(-2, 6, sshadow))[..., None]
        out = mix(bg, rc, np.clip(s/.8+.5, 0, 1))
        P['sdf_eff'] = s
    else:
        rc = {'sand': region_sand, 'island': region_island, 'pasture': region_pasture,
              'park': region_park, 'snow': region_snow}[region](P, th)
        if region == 'snow':     # mäkký tieň okraja snehovej pláne v lese
            bg = bg*(1-.2*np.clip(1+P['sdf']/10, 0, 1))[..., None]
        out = mix(bg, rc, np.clip(P['sdf']/.6+.5, 0, 1))
        P['sdf_eff'] = P['sdf']
    vx = (X/W-.5)*2; vy = (Y/H-.5)*2; r = np.sqrt(vx*vx+vy*vy)/1.2
    out = out*(1-.10*sstep(.55, 1.15, r))[..., None]
    grain = (hash2(np.arange(w)[None, :]+np.zeros((h, 1)), np.arange(h)[:, None]+np.zeros((1, w)), 7)-.5)*.018
    return np.clip(out + grain[..., None], 0, 1), P

# ---------------------------------------------------------------- vektor
class Pen:
    def __init__(s, surf, H, scale):
        s.c = cairo.Context(surf); s.c.translate(0, H*scale); s.c.scale(scale, -scale)
    def rgba(s, hx, a=1.0):
        r, g, b = rgb(hx); s.c.set_source_rgba(float(r), float(g), float(b), a)

def soft_shadow(p, cx, cy, rx, ry, a=.3):
    c = p.c; c.save(); c.translate(cx, cy); c.scale(1, ry/rx)
    g = cairo.RadialGradient(0, 0, 0, 0, 0, rx); g.add_color_stop_rgba(0, 0, 0, 0, a); g.add_color_stop_rgba(.55, 0, 0, 0, a*.7); g.add_color_stop_rgba(1, 0, 0, 0, 0)
    c.set_source(g); c.arc(0, 0, rx, 0, 6.2832); c.fill(); c.restore()

def blob(p, cx, cy, r, dark, light, mid):
    g = cairo.RadialGradient(cx-r*.38, cy+r*.42, r*.05, cx, cy, r*1.05)
    for o, col in ((0, light), (.45, mid), (1, dark)): g.add_color_stop_rgb(o, *map(float, rgb(col)))
    p.c.set_source(g); p.c.arc(cx, cy, r, 0, 6.2832); p.c.fill()

def tree(p, x, y, s, dark=0x2E6B2E, mid=0x4A8C3A, light=0x8CC860, trunk=0x5A3D23):
    p.rgba(trunk); p.c.rectangle(x-s*.06, y, s*.12, s*.3); p.c.fill()
    R = s*.44
    for (ox, oy, rr) in [(-.48, -.22, .68), (.46, -.18, .7), (.05, .32, .66), (0, 0, 1)]:
        blob(p, x+ox*R, y+s*.62+oy*R, R*rr, dark, light, mid)

def palm(p, x, y, s, seed):
    c = p.c; top = (x+s*.2, y+s*.95)
    c.set_line_cap(cairo.LINE_CAP_ROUND)
    for k in range(8):
        t0, t1 = k/8, (k+1)/8
        q = lambda t: ((1-t)**2*x+2*(1-t)*t*(x+s*.03)+t*t*top[0], (1-t)**2*y+2*(1-t)*t*(y+s*.55)+t*t*top[1])
        p.rgba(0x9C7446 if k % 2 == 0 else 0x76532F); c.set_line_width(s*(.14-.06*t0)); c.move_to(*q(t0)); c.line_to(*q(t1)); c.stroke()
    base = h1(seed, 2, 9)*6.28
    for layer in (0, 1):
        for k in range(7):
            if k % 2 != layer: continue
            ang = base+k/7*6.2832; L = s*.62*(.85+.3*h1(seed, k, 3)); w = s*.15; dr = s*.18
            c.save(); c.translate(*top); c.rotate(ang)
            c.move_to(0, 0); c.curve_to(L*.3, w*1.1, L*.75, w*.6, L, -dr*.5); c.curve_to(L*.7, -w*.2, L*.3, -w*.7, 0, 0)
            g = cairo.LinearGradient(0, 0, L, 0)
            cols = (0x23703A, 0x3F9444) if layer == 0 else (0x3F9444, 0x8CCB5C)
            g.add_color_stop_rgb(0, *map(float, rgb(cols[0]))); g.add_color_stop_rgb(1, *map(float, rgb(cols[1]))); c.set_source(g); c.fill()
            c.restore()

def pine(p, x, y, s):
    c = p.c
    p.rgba(0x46362A); c.rectangle(x-s*.05, y, s*.1, s*.22); c.fill()
    apex = y
    for k in range(4):
        by = y+s*(.12+.24*k); half = s*(.46-.095*k); ay = by+s*.46; sag = s*.05; apex = ay
        for side, col in ((-1, 0x2B5E4D), (1, 0x1C4639)):
            c.move_to(x, ay); c.curve_to(x+side*half*.35, ay-s*.2, x+side*half*.8, by+s*.08, x+side*half, by)
            c.curve_to(x+side*half*.5, by-sag*.2, x+side*half*.2, by+sag, x, by+sag*.6); c.close_path()
            p.rgba(col); c.fill()
        p.rgba(0xFFFFFF, .95)
        c.move_to(x-half*.98, by+s*.01); c.curve_to(x-half*.6, by+s*.07, x-half*.25, by+s*.06, x-half*.02, by+s*.08)
        c.curve_to(x-half*.3, by+s*.02, x-half*.65, by-s*.015, x-half*.98, by+s*.01); c.fill()
    p.rgba(0xFFFFFF); ay = apex
    c.move_to(x, ay); c.curve_to(x-s*.06, ay-s*.06, x-s*.1, ay-s*.11, x-s*.11, ay-s*.13)
    c.curve_to(x-s*.02, ay-s*.1, x+s*.05, ay-s*.12, x+s*.1, ay-s*.13); c.curve_to(x+s*.07, ay-s*.08, x+s*.03, ay-s*.04, x, ay); c.fill()

def umbrella(p, x, y, s, seed):
    c = p.c; cols = [0xE4574B, 0xF2B33D, 0x3E8ED0, 0x2BB39B][int(h1(seed, 1, 1)*4) % 4]
    # uterák
    c.save(); c.translate(x+s*.55, y-s*.35); c.rotate((h1(seed, 2, 2)-.5)*.8)
    p.rgba([0xFFFFFF, 0xF6C744, 0x7FC8F8][int(h1(seed, 3, 3)*3) % 3]); c.rectangle(-s*.22, -s*.45, s*.44, s*.9); c.fill()
    p.rgba(0xE4574B, .8)
    for k in range(3): c.rectangle(-s*.22, -s*.3+k*s*.3, s*.44, s*.08); c.fill()
    c.restore()
    soft_shadow(p, x+s*.25, y-s*.2, s*.62, s*.5, .28)
    n = 8
    for k in range(n):
        a0 = k/n*6.2832; a1 = (k+1)/n*6.2832
        c.move_to(x, y); c.arc(x, y, s*.5, a0, a1); c.close_path()
        p.rgba(cols if k % 2 == 0 else 0xFFFFFF); c.fill()
    p.rgba(0x000000, .15); c.arc(x, y, s*.5, 0, 6.2832); c.set_line_width(.6); c.stroke()
    p.rgba(0x5A3A1E); c.arc(x, y, s*.06, 0, 6.2832); c.fill()

def starfish(p, x, y, s, seed):
    c = p.c; rot = h1(seed, 1, 1)*6.28; p.rgba(0xF0806E)
    for k in range(10):
        a = rot + k/10*6.2832; r = s*(.5 if k % 2 == 0 else .2)
        (c.move_to if k == 0 else c.line_to)(x+math.cos(a)*r, y+math.sin(a)*r)
    c.close_path(); c.fill()

def shell(p, x, y, s, seed):
    c = p.c; a = h1(seed, 1, 1)*6.28
    c.save(); c.translate(x, y); c.rotate(a)
    p.rgba(0xF6E7D2); c.move_to(0, 0); c.arc(0, 0, s*.5, 0, math.pi); c.close_path(); c.fill()
    p.rgba(0xC9A27E, .8); c.set_line_width(.5)
    for k in range(5):
        b = (k+.5)/5*math.pi; c.move_to(0, 0); c.line_to(math.cos(b)*s*.48, math.sin(b)*s*.48); c.stroke()
    c.restore()

def haybale(p, x, y, s):
    c = p.c; soft_shadow(p, x+s*.3, y-s*.1, s*.6, s*.3, .3)
    g = cairo.RadialGradient(x-s*.15, y+s*.15, 0, x, y, s*.5)
    g.add_color_stop_rgb(0, *map(float, rgb(0xF1D98B))); g.add_color_stop_rgb(1, *map(float, rgb(0xC9A44E)))
    c.set_source(g); c.arc(x, y, s*.5, 0, 6.2832); c.fill()
    p.rgba(0xA9873B, .8); c.set_line_width(.7)
    for r in (.15, .28, .4): c.arc(x, y, s*r, 0, 5.5); c.stroke()

def house(p, x, y, s, seed, roofc=None):
    """Domček zhora-spredu: sedlová strecha s dvoma tónmi, stena s oknami, tieň."""
    c = p.c; w = s; d = s*.7; wall = s*.3
    roofc = roofc or [0xB5523B, 0x8C5A3C, 0x5E6B7A, 0xC0703F][int(h1(seed, 1, 5)*4) % 4]
    soft_shadow(p, x+w*.35, y-wall*.1, w*.75, d*.45, .35)
    # predná stena
    p.rgba(0xEDE3D0); c.rectangle(x-w/2, y, w, wall); c.fill()
    p.rgba(0x6E8FB0)
    for k in range(3): c.rectangle(x-w/2+w*(.15+k*.28), y+wall*.3, w*.12, wall*.4); c.fill()
    p.rgba(0x7A5533); c.rectangle(x-w*.06, y, w*.12, wall*.6); c.fill()
    # strecha: dve plochy
    top = y+wall; ridge = top+d*.5
    r0 = rgb(roofc); r1 = r0*.78
    c.move_to(x-w/2-1, top); c.line_to(x+w/2+1, top); c.line_to(x+w/2+1, ridge); c.line_to(x-w/2-1, ridge); c.close_path()
    c.set_source_rgb(*map(float, r1)); c.fill()
    c.move_to(x-w/2-1, ridge); c.line_to(x+w/2+1, ridge); c.line_to(x+w/2+1, top+d); c.line_to(x-w/2-1, top+d); c.close_path()
    c.set_source_rgb(*map(float, r0)); c.fill()
    p.rgba(0x000000, .15); c.set_line_width(.6); c.move_to(x-w/2-1, ridge); c.line_to(x+w/2+1, ridge); c.stroke()
    # komín
    p.rgba(0x8A6A5A); c.rectangle(x+w*.22, ridge+d*.08, w*.1, d*.22); c.fill()

def building(p, x0, y0, x1, y1, hgt, seed, kind):
    """Budova v miernej perspektíve: strecha posunutá hore o h*0.22, viditeľná južná stena s oknami."""
    c = p.c; lift = hgt*.22
    roof = {'glass': 0x6F9CC4, 'concrete': 0xB9B6AE, 'brick': 0xB0714F, 'dark': 0x6B7078, 'beige': 0xD8C9A8}[kind]
    rc = rgb(roof)
    # stena
    c.move_to(x0, y0); c.line_to(x1, y0); c.line_to(x1, y0+lift); c.line_to(x0, y0+lift); c.close_path()
    c.set_source_rgb(*map(float, rc*.62)); c.fill()
    # okná na stene
    if lift > 6:
        p.rgba(0xF4E7B8 if kind != 'glass' else 0xBFD9F0, .55)
        rows = int(lift/5); cols = max(1, int((x1-x0)/5))
        for r in range(rows):
            for k in range(cols):
                if h1(seed+r, k, 77) < .7:
                    c.rectangle(x0+1.2+k*(x1-x0-2.4)/cols, y0+1.5+r*5, (x1-x0-2.4)/cols*.55, 2.2); c.fill()
    # strecha
    ry0 = y0+lift; ry1 = y1+lift
    if kind == 'glass':
        g = cairo.LinearGradient(x0, ry1, x1, ry0)
        g.add_color_stop_rgb(0, *map(float, rc*1.15)); g.add_color_stop_rgb(.5, *map(float, rc)); g.add_color_stop_rgb(1, *map(float, rc*.85))
        c.set_source(g)
    else:
        c.set_source_rgb(*map(float, rc))
    c.rectangle(x0, ry0, x1-x0, ry1-ry0); c.fill()
    p.rgba(0x000000, .12); c.set_line_width(.8); c.rectangle(x0+.4, ry0+.4, x1-x0-.8, ry1-ry0-.8); c.stroke()
    # strešné detaily
    w, h = x1-x0, ry1-ry0
    if kind != 'glass' and w > 14 and h > 14:
        for k in range(int(h1(seed, 3, 3)*3)+1):
            ax = x0+3+h1(seed, k, 4)*(w-9); ay = ry0+3+h1(seed, k, 5)*(h-9)
            p.rgba(0x000000, .18); c.rectangle(ax+1, ay-1, 5, 4); c.fill()
            p.rgba(0xD9D9D6); c.rectangle(ax, ay, 5, 4); c.fill()
    if hgt > 70 and w > 20 and h > 20 and h1(seed, 9, 9) < .5:          # heliport
        cx, cy = x0+w/2, ry0+h/2; p.rgba(0x4B4F55); c.arc(cx, cy, min(w, h)*.3, 0, 6.2832); c.fill()
        p.rgba(0xFFFFFF); c.set_line_width(1)
        c.move_to(cx-2.5, cy-3); c.line_to(cx-2.5, cy+3); c.move_to(cx+2.5, cy-3); c.line_to(cx+2.5, cy+3); c.move_to(cx-2.5, cy); c.line_to(cx+2.5, cy); c.stroke()
    if kind == 'concrete' and h1(seed, 8, 8) < .25 and w > 16 and h > 16:   # strešná záhrada
        p.rgba(0x7DB352); c.rectangle(x0+3, ry0+3, w-6, h-6); c.fill()
        for k in range(3): blob(p, x0+5+h1(seed, k, 11)*(w-10), ry0+5+h1(seed, k, 12)*(h-10), 2.6, 0x2E6B2E, 0x8CC860, 0x4A8C3A)

def draw_city(p, F, W, H, th, icons):
    bw, bh, sw = CITY['bw'], CITY['bh'], CITY['street']
    downtown = lambda x, y: float(bil(F['large'], x, y))
    shadows, builds = [], []
    for bj in range(int(H/bh)+1):
        for bi in range(int(W/bw)+1):
            x0 = bi*bw + sw/2 + 3; y0 = bj*bh + sw/2 + 3; x1 = (bi+1)*bw - sw/2 - 3; y1 = (bj+1)*bh - sw/2 - 3
            nx = 1 + int(h1(bi, bj, 21)*2.99); ny = 1 + int(h1(bi, bj, 22)*1.99)
            for a in range(nx):
                for b in range(ny):
                    lx0 = x0 + (x1-x0)*a/nx + 1.5; lx1 = x0 + (x1-x0)*(a+1)/nx - 1.5
                    ly0 = y0 + (y1-y0)*b/ny + 1.5; ly1 = y0 + (y1-y0)*(b+1)/ny - 1.5
                    sd = h1(bi*7+a, bj*5+b, 31)
                    dt = downtown((lx0+lx1)/2, (ly0+ly1)/2)
                    hgt = 8 + 30*sd + 160*sstep(.5, .8, dt)*h1(bi*7+a, bj*5+b, 32)
                    lift = hgt*.22
                    corners = [(lx0, ly0), (lx1, ly0), (lx0, ly1+lift), (lx1, ly1+lift), ((lx0+lx1)/2, (ly0+ly1)/2)]
                    if any(bil(F['sdf'], x, y) > -8 for x, y in corners): continue
                    kind = 'glass' if hgt > 90 else ['concrete', 'brick', 'dark', 'beige', 'concrete'][int(sd*4.99)]
                    builds.append((lx0, ly0, lx1, ly1, hgt, int(sd*1e5), kind))
    for (lx0, ly0, lx1, ly1, hgt, sd, kind) in builds:                  # tiene (doprava dole)
        L = hgt*.35
        p.rgba(0x000000, .22); c = p.c
        c.move_to(lx0, ly1); c.line_to(lx1, ly1); c.line_to(lx1+L, ly1-L*.6); c.line_to(lx1+L, ly0-L*.6); c.line_to(lx0+L, ly0-L*.6); c.line_to(lx0, ly0); c.close_path(); c.fill()
    for b in sorted(builds, key=lambda b: -b[1]):
        building(p, *b)

def place_decor(p, F, W, H, icons, th, name, P):
    """Vektorové prvky podľa témy."""
    seed = th['seed']; items = []
    def free(x, y, box):
        bx0, by0, bx1, by1 = box
        return not any(not (bx1 < a0 or bx0 > a1 or by1 < b0 or by0 > b1) for (a0, b0, a1, b1) in icons)
    g = 21 if name == 'zima' else 30
    for j in range(int(H/g)+2):
        for i in range(int(W/g)+2):
            x = (i+.1+.8*h1(i, j, seed+11))*g; y = (j+.1+.8*h1(i, j, seed+23))*g
            roll = h1(i, j, seed+37); sz = h1(i, j, seed+53); sid = int(h1(i, j, seed+61)*1e6)
            sd = float(bil(F['sdf'], x, y)); dens = float(sstep(.38, .62, bil(F['detail'], x, y)))
            kind = None
            if name == 'plaz':
                if 18 < sd and roll < .05: kind = 'umbrella'
                elif 2 < sd < 14 and roll < .1: kind = 'star' if roll < .05 else 'shell'
                elif 10 < sd < 40 and .9 < roll: kind = 'palm'
            elif name == 'ostrovy':
                if sd > 9 and roll < .35+.5*dens: kind = 'palm' if roll < .1+.45*dens else 'bush'
            elif name == 'polia':
                if -60 < sd < -14 and roll < .035: kind = 'house'
                elif sd < -10 and roll < .06:
                    _, _, ID = worley(np.array([x], np.float32), np.array([y], np.float32), 170, seed+60, jitter=.9)
                    if int(ID[0]*7) in (0, 6): kind = 'hay'
                elif sd > 6 and roll < .08: kind = 'tree'
            elif name == 'mesto':
                if sd > 6 and roll < .3+.3*dens: kind = 'tree'
            elif name == 'les':
                if sd < -16 and roll < .02 and float(bil(F['large'], x, y)) < .45: kind = 'tree'
            elif name == 'zima':
                if sd < -3 and roll < .93: kind = 'pine'
            if not kind: continue
            base = dict(pine=26, umbrella=16, star=6, shell=5, palm=30, bush=16, house=20, hay=9, tree=22)[kind]
            s = base*(.8+.45*sz)
            box = (x-s*.7-5, y-s*.5-5, x+s*.7+5, y+s*1.2+5)
            if not free(x, y, box): continue
            items.append((x, y, kind, s, sid))
    items.sort(key=lambda it: -it[1])
    for (x, y, k, s, sid) in items:
        if k in ('tree', 'palm', 'bush', 'pine'): soft_shadow(p, x+s*.38, y-s*.08, s*.62, s*.26, .3 if k != 'pine' else .22)
    for (x, y, k, s, sid) in items:
        if k == 'umbrella': umbrella(p, x, y, s, sid)
        elif k == 'star': starfish(p, x, y, s, sid)
        elif k == 'shell': shell(p, x, y, s, sid)
        elif k == 'palm': palm(p, x, y, s, sid)
        elif k == 'bush':
            for (ox, oy, rr) in [(-.24, .18, .3), (.24, .2, .3), (0, .34, .36)]: blob(p, x+ox*s, y+oy*s, s*rr, 0x23703A, 0x8CCB5C, 0x3F9444)
        elif k == 'house': house(p, x, y, s, sid)
        elif k == 'hay': haybale(p, x, y, s)
        elif k == 'tree': tree(p, x, y, s)
        elif k == 'pine': pine(p, x, y, s)

def draw_anim_hint(p, W, H, name, th):
    """Statický snímok pohyblivých vecí (autá, oblaky), len pre náhľad."""
    c = p.c; rng = np.random.default_rng(th['seed'])
    if name == 'mesto':
        bw, bh = CITY['bw'], CITY['bh']
        for k in range(int(W/90)):
            horiz = rng.random() < .5
            if horiz:
                y = (rng.integers(1, int(H/bh)+1))*bh + (3 if rng.random() < .5 else -3); x = rng.uniform(0, W); ang = 0
            else:
                x = (rng.integers(1, int(W/bw)+1))*bw + (3 if rng.random() < .5 else -3); y = rng.uniform(0, H); ang = math.pi/2
            col = [0xE4574B, 0xF2F2F2, 0x2E6FD0, 0xF2C230, 0x333840, 0x5DBB63][rng.integers(0, 6)]
            c.save(); c.translate(x, y); c.rotate(ang)
            p.rgba(0x000000, .25); c.rectangle(-4.5, -2.8, 10, 5); c.fill()
            p.rgba(col); c.rectangle(-5, -2.4, 10, 4.8); c.fill()
            p.rgba(0x1E2A38, .8); c.rectangle(.5, -1.9, 2.4, 3.8); c.fill()
            c.restore()
    if name in ('les',):
        for k in range(int(W/400)):
            cx = rng.uniform(0, W); cy = rng.uniform(0, H)
            for b in range(7):
                bx = cx+rng.uniform(-50, 50); by = cy+rng.uniform(-15, 15); r = rng.uniform(18, 32)
                g = cairo.RadialGradient(bx, by, 0, bx, by, r); g.add_color_stop_rgba(0, 1, 1, 1, .5); g.add_color_stop_rgba(1, 1, 1, 1, 0)
                c.set_source(g); c.arc(bx, by, r, 0, 6.29); c.fill()

def render(W, H, icons, name, scale):
    th = THEMES[name]
    F = build_field(W, H, icons, th)
    col, P = shade_pixels(F, W, H, scale, th, name)
    h, w = col.shape[:2]
    arr = np.zeros((h, w, 4), np.uint8)
    arr[..., 0] = (col[..., 2]*255).astype(np.uint8); arr[..., 1] = (col[..., 1]*255).astype(np.uint8)
    arr[..., 2] = (col[..., 0]*255).astype(np.uint8); arr[..., 3] = 255
    surf = cairo.ImageSurface.create_for_data(memoryview(arr), cairo.FORMAT_ARGB32, w, h, w*4)
    p = Pen(surf, H, scale)
    if th['style'] == 'city': draw_city(p, F, W, H, th, icons)
    place_decor(p, F, W, H, icons, th, name, P)
    draw_anim_hint(p, W, H, name, th)
    for (x0, y0, x1, y1) in icons:       # simulované ikony (64 pt + popisok)
        ix = (x0+x1)/2-32; iy = y1-14
        p.rgba(0x000000, .25); p.c.rectangle(ix+2, iy-66, 64, 64); p.c.fill()
        p.rgba(0x4A86E8); p.c.rectangle(ix, iy-64, 64, 64); p.c.fill()
        p.rgba(0xFFFFFF, .95); p.c.rectangle(ix+8, iy-80, 48, 7); p.c.fill()
    surf.flush(); return surf

def layout(W, H):
    def icon(x, y): return (x-6, y-110-6, x+80+6, y+6)
    ic = []
    for i in range(2):
        for j in range(4): ic.append(icon(20+i*100, H-14-j*112))
    for (x, y) in [(560, 640), (660, 640), (560, 528), (660, 528), (1150, 780), (1500, 360)]:
        ic.append(icon(x, y))
    return ic

if __name__ == '__main__':
    out = sys.argv[1]; names = sys.argv[2:] or list(THEMES)
    W, H = 1800, 900
    for n in names:
        render(W, H, layout(W, H), n, 1.0).write_to_png(f'{out}/{n}.png')
    print('ok')
