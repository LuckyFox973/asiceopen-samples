# Krajinka v4 – maľovaný štýl. Pixely (numpy) + mäkké vektory (cairo ≈ CoreGraphics).
import numpy as np, cairo, math, sys
from preview_base import hash2, h1, smooth, sstep, fbm, worley, rgb, mix, C, build_field, bil, relief, Pen, smin

THEMES = {
  'plaz':    dict(seed=11, R=(46, 72), wob=40, k=52),
  'ostrovy': dict(seed=1,  R=(34, 52), wob=30, k=40),
  'les':     dict(seed=5,  R=(36, 54), wob=30, k=44),
  'polia':   dict(seed=7,  R=(36, 54), wob=26, k=40),
  'mesto':   dict(seed=9,  R=(32, 46), wob=22, k=36),
  'zima':    dict(seed=3,  R=(38, 58), wob=30, k=44),
}

# ---------------------------------------------------------------- maliarske pomôcky
def warp(X, Y, s, amp, scale):
    return (X + (fbm(X/scale, Y/scale, s, 2)-.5)*amp, Y + (fbm(X/scale, Y/scale, s+19, 2)-.5)*amp)

def mottle(X, Y, s, scale=140):
    wx, wy = warp(X, Y, s, 60, 90)
    return fbm(wx/scale, wy/scale, s+7, 3)

def hatch(X, Y, s, cw=8.0, chh=6.0, p=.6, L=1.7, th=.42, slant=.18):
    """Krátke ťahy štetca (tráva) – v každej bunke jeden ťah s náhodnou polohou."""
    ci = np.floor(X/cw); cj = np.floor(Y/chh)
    ox = (hash2(ci, cj, s)*.6+.2)*cw; oy = (hash2(ci, cj, s+1)*.6+.2)*chh
    dx = X - (ci*cw+ox); dy = Y - (cj*chh+oy)
    sl = (hash2(ci, cj, s+2)-.5)*slant*2
    dyy = dy - dx*sl
    on = hash2(ci, cj, s+3) < p
    a = (1-sstep(L-.6, L+.4, np.abs(dx))) * (1-sstep(th-.25, th+.25, np.abs(dyy)))
    return a*on

def dots(X, Y, s, cell, p, r):
    ci = np.floor(X/cell); cj = np.floor(Y/cell)
    ox = hash2(ci, cj, s)*cell; oy = hash2(ci, cj, s+1)*cell
    d = np.sqrt((X-(ci*cell+ox))**2 + (Y-(cj*cell+oy))**2)
    on = hash2(ci, cj, s+2) < p
    return (1-sstep(r*.6, r*1.2, d))*on

def grain(X, Y):
    g = (hash2(np.floor(X*2), np.floor(Y*2), 3)-.5)*.035
    fib = (fbm(X/1.3, Y/5, 77, 1)-.5)*.03
    return g + fib

# ---------------------------------------------------------------- okolia
LIVE = False
SEA = dict(plaz=[(0, 0xCFF1E8), (8, 0x9BE5DC), (24, 0x55CFD2), (70, 0x23B4C8), (150, 0x1499BD), (320, 0x0E82AC)],
           ostrovy=[(0, 0xC2ECE2), (8, 0x86DDD5), (24, 0x3FBFCB), (70, 0x1E9CC0), (150, 0x1579AE), (320, 0x10609A)])

def ramp(stops, v):
    out = np.zeros(v.shape+(3,), np.float32)
    xs = [s[0] for s in stops]; cols = [rgb(s[1]) for s in stops]
    for k in range(3):
        out[..., k] = np.interp(v, xs, [c[k] for c in cols])
    return out

def sea(P, name):
    X, Y, sdf = P['X'], P['Y'], P['sdf']; sh = X.shape; s = 11
    nz = fbm(X/40, Y/40, s+5, 2)
    depth = np.maximum(-sdf, 0) + (nz-.5)*6
    c = ramp(SEA[name], depth)
    m = mottle(X, Y, s+30, 200)
    c = mix(c, c*1.08 + .04, sstep(.52, .72, m)*sstep(10, 40, depth)*.8)        # svetlé škvrny
    c = mix(c, c*.93, sstep(.6, .8, 1-m)*sstep(40, 120, depth)*.5)
    # pena: lem pri brehu s prerušeniami + spŕška
    brk = sstep(.3, .55, fbm(X/22, Y/22, s+8, 2))
    lip = (1-sstep(1.2, 3.2, depth))*(.75+.25*brk)
    spray = dots(X, Y, s+40, 4.2, .55*np.exp(-depth/5), .6)
    # druhá a tretia línia peny v diaľke
    l1 = np.exp(-((depth-(13+(nz-.5)*10))/.9)**2)*sstep(.42, .6, fbm(X/55, Y/55, s+9, 2))*.55
    l2 = np.exp(-((depth-(28+(nz-.5)*16))/.8)**2)*sstep(.5, .66, fbm(X/70, Y/70, s+10, 2))*.32
    if LIVE: return c
    foam = np.clip(np.maximum.reduce([lip, spray, l1, l2]), 0, 1)
    c = mix(c, C(0xFFFFFF, sh), foam)
    spark = dots(X, Y, s+50, 22, .35, .75)*sstep(20, 60, depth)
    c = mix(c, C(0xFFFFFF, sh), spark*.75)
    return c

def grassfield(P, s, base=(0xBCD47E, 0xA8C86C, 0xCADF91), mark=0x5E8A3A, markA=.5):
    X, Y = P['X'], P['Y']; sh = X.shape
    m = mottle(X, Y, s)
    c = mix(C(base[0], sh), C(base[1], sh), sstep(.4, .65, m))
    c = mix(c, C(base[2], sh), sstep(.55, .75, fbm(X/70, Y/70, s+3, 2))*.6)
    h = hatch(X, Y, s+60)
    return mix(c, C(mark, sh), h*markA*(0.6+0.4*sstep(.3, .7, m)))

def forest_mask(P, s, thr=.5, wid=.012):
    X, Y = P['X'], P['Y']
    F1, _, ID = worley(X, Y, 26, s+44, .8)
    bump = np.clip(1 - F1/(14+ID*6), 0, 1)
    g = P['L'] + (P['d']-.5)*.25 + bump*.035
    return sstep(thr-wid, thr+wid, g), g

def forest(P, s, snowy=False):
    X, Y = P['X'], P['Y']; sh = X.shape
    if snowy:
        bgc = grassfield(P, s, (0xE4ECF3, 0xD6E1EC, 0xEEF3F8), 0x9FB4CB, .55)
        fc0, fc1, fc2, rim = 0x2E5A55, 0x24494A, 0x3B6B63, 0x1D3B3C
    else:
        bgc = grassfield(P, s)
        fc0, fc1, fc2, rim = 0x4E7F3B, 0x3E6C30, 0x5E9246, 0x2F5A26
    tree, g = forest_mask(P, s, .22 if snowy else .5)
    # tieň lesa na lúku
    Ps = dict(P); Ps['X'] = X-7; Ps['Y'] = Y+7
    Ps['L'] = bil(P['F']['large'], X-7, Y+7); Ps['d'] = bil(P['F']['detail'], X-7, Y+7)
    tshadow, _ = forest_mask(Ps, s, .22 if snowy else .5)
    bgc = bgc*(1-.22*tshadow*(1-tree))[..., None]
    m = mottle(X, Y, s+90, 60)
    fc = mix(C(fc0, sh), C(fc1, sh), sstep(.45, .7, m))
    fc = mix(fc, C(fc2, sh), sstep(.6, .8, fbm(X/30, Y/30, s+91, 2))*.7)
    F1, _, ID = worley(X, Y, 9, s+92, .9)                      # drobná štruktúra korún
    fc = fc*(1 - .10*sstep(.4, 1, F1/6))[..., None]
    fc = mix(fc, C(rim, sh), (1-sstep(.0, .03, g-(.22 if snowy else .5)))*.5)   # tmavší okraj
    if snowy:
        fc = mix(fc, C(0xDCE6EE, sh), .22 + .18*sstep(.4, .7, fbm(X/50, Y/50, s+95, 2)))   # ojíněné koruny
        fc = mix(fc, C(0xF4F8FB, sh), dots(X, Y, s+93, 4.5, .45, .95)*.75)
        fc = mix(fc, C(0xF4F8FB, sh), dots(X, Y, s+94, 7, .3, 1.4)*.5)
    return mix(bgc, fc, tree)

FIELD_COLS = [(0xE2CD8A, 0xD2B96E), (0xA9C672, 0x93B35E), (0xB89371, 0x9C7A5A), (0xB7A6D2, 0x9C8ABD),
              (0xE9D874, 0xD8C55A), (0xD8C79C, 0xC6B385), (0x9FBF6A, 0x8AAA58)]

def fields(P, s):
    X, Y = P['X'], P['Y']; sh = X.shape
    wx, wy = warp(X, Y, s+70, 46, 160)
    F1, F2, ID = worley(wx, wy, 175, s+60, .9)
    k = np.minimum((ID*len(FIELD_COLS)).astype(int), len(FIELD_COLS)-1)
    a = np.zeros(sh+(3,), np.float32); b = np.zeros(sh+(3,), np.float32)
    for i, (ca, cb) in enumerate(FIELD_COLS):
        msk = k == i; a[msk] = rgb(ca); b[msk] = rgb(cb)
    ang = ID*np.pi*7.3
    u = wx*np.cos(ang) + wy*np.sin(ang)
    rows = (np.sin(u/2.4)*.5+.5)**1.5
    c = mix(a, b, rows*.55)
    m = mottle(X, Y, s+71, 90)
    c = c*(1 + (m-.5)*.12)[..., None]
    edge = F2-F1
    HF1, _, HID = worley(X, Y, 7, s+61, .9)
    bump = np.clip(1 - HF1/(4+HID*2), 0, 1)
    hedge = (1-sstep(2, 6.5, edge + (1-bump)*2.5))
    c = c*(1-.2*(1-sstep(4, 12, edge)))[..., None]
    hc = mix(C(0x4E7F3B, sh), C(0x6E9C4E, sh), bump)
    return mix(c, hc, hedge*.95)

def city(P, s):
    X, Y = P['X'], P['Y']; sh = X.shape
    bw, bh, sw = 150.0, 118.0, 14.0
    u = np.mod(X, bw); v = np.mod(Y, bh)
    hu = np.minimum(u, bw-u); hv = np.minimum(v, bh-v); ds = np.minimum(hu, hv)
    m = mottle(X, Y, s+80, 120)
    ground = mix(C(0xDDD6C9, sh), C(0xD2CABB, sh), sstep(.4, .65, m))
    road = mix(C(0x9A9DA2, sh), C(0x8D9095, sh), sstep(.4, .65, m))
    c = mix(road, ground, sstep(sw/2-.6, sw/2+.6, ds))
    c = mix(c, C(0xE9E4DA, sh), np.exp(-((ds-(sw/2+1.6))/1.0)**2)*.7)         # chodník
    dash = ((hv < .6) & (np.mod(X, 16) < 8) & (hu > sw/2)) | ((hu < .6) & (np.mod(Y, 16) < 8) & (hv > sw/2))
    c = mix(c, C(0xF1EEE4, sh), dash*.6)
    return c

# ---------------------------------------------------------------- povrchy pod dokumentmi
def sand(P, s):
    X, Y = P['X'], P['Y']; sh = X.shape; inl = np.maximum(P['sdf'], 0)
    m = mottle(X, Y, s+20, 110)
    c = mix(C(0xF5EEDF, sh), C(0xEDE2CC, sh), sstep(.4, .7, m)*(1-.5*P['calm']))
    c = mix(c, C(0xD8C7A6, sh), dots(X, Y, s+21, 3.2, .35, .45)*.55)
    c = mix(c, C(0xFFFFFF, sh), dots(X, Y, s+22, 3.6, .2, .4)*.6)
    nz = fbm(X/40, Y/40, s+5, 2)
    if LIVE: return c
    wet = 1-sstep(3, 14+(nz-.5)*8, inl)
    c = mix(c, C(0xE2D3B6, sh), wet*.7)
    wash = np.exp(-((inl-(9+(nz-.5)*8))/.8)**2)*sstep(.4, .6, fbm(X/30, Y/30, s+23, 2))
    return mix(c, C(0xFFFFFF, sh), wash*.5)

def island(P, s):
    X, Y = P['X'], P['Y']; sh = X.shape; inl = np.maximum(P['sdf'], 0)
    c = grassfield(P, s+200, (0x9CCB6A, 0x86B957, 0xB0D67C), 0x4C7A32, .45)
    sd = sand(P, s)
    nz = fbm(X/40, Y/40, s+5, 2)
    return mix(sd, c, sstep(12+(nz-.5)*8, 15+(nz-.5)*8, inl))

def clouds(P, s, bg):
    X, Y = P['X'], P['Y']; sh = X.shape
    def shape(xx, yy, sdf):
        F1, _, ID = worley(xx, yy, 30, 333, .7)
        return sdf + .85*np.clip((14+ID*7) - F1, -14, 22)
    cs = shape(X, Y, P['sdf'])
    shadow = shape(X-10, Y+14, bil(P['F']['sdf'], X-10, Y+14))
    bg = bg*(1-.18*sstep(-6, 10, shadow))[..., None]
    F1, F2, ID = worley(X, Y, 30, 333, .7)
    F1l, _, IDl = worley(X+6, Y-6, 30, 333, .7)
    rr = 14+ID*7
    lit = np.clip(1 - F1l/(14+IDl*7), 0, 1)
    inner = sstep(0, 46, cs)                                            # hlboko v oblaku pokojnejšie
    # každá kopa: svetlá vľavo hore, modrosivá vpravo dole
    c = mix(C(0xC5D4E6, sh), C(0xFBFDFF, sh), sstep(.05, .6, lit + inner*.45))
    # jemné oblúky medzi kopami
    seam = (1-sstep(.6, 2.6, F2-F1))*(F1 < rr*1.1)*(1-inner*.7)
    c = mix(c, C(0xD3DFEC, sh), seam*.55)
    c = c + grain(X, Y)[..., None]*.5
    return mix(bg, c, sstep(-1, 1, cs))

def pasture(P, s):
    X, Y = P['X'], P['Y']; sh = X.shape; inl = np.maximum(P['sdf'], 0)
    c = grassfield(P, s+300, (0xC3DF8E, 0xB2D47C, 0xD3E8A2), 0x6A9645, .4)
    fl = dots(X, Y, s+301, 7, .25, .9)*(P['calm'] < .3)
    cols = np.stack([rgb(0xFFFFFF), rgb(0xF6D55C), rgb(0xEE8FB0)])
    pick = (hash2(np.floor(X/7), np.floor(Y/7), s+302)*3).astype(int) % 3
    c = mix(c, cols[pick], fl*.9)
    return mix(c, c*.85, (1-sstep(1, 5, inl))*.6)                       # tmavší lem

def park(P, s):
    X, Y = P['X'], P['Y']; inl = np.maximum(P['sdf'], 0)
    c = grassfield(P, s+400, (0xA9CF7E, 0x98C16C, 0xB9DA8E), 0x5A8A3C, .4)
    return mix(c, C(0xEDE8DE, X.shape), 1-sstep(1, 3, inl))

def snowfield(P, s):
    X, Y = P['X'], P['Y']; sh = X.shape; inl = np.maximum(P['sdf'], 0)
    m = mottle(X, Y, s+500, 120)
    c = mix(C(0xFBFDFF, sh), C(0xEEF3F9, sh), sstep(.4, .7, m)*(1-.5*P['calm']))
    c = mix(c, C(0xFFFFFF, sh), dots(X, Y, s+501, 5, .2, .5)*.8)
    return mix(c, C(0xD5E0EC, sh), (1-sstep(0, 9, inl))*.75)           # modrastý okraj

# ---------------------------------------------------------------- zloženie
def compose(F, W, H, scale, name, s):
    w, h = int(W*scale), int(H*scale)
    X, Y = np.meshgrid((np.arange(w)+.5)/scale, (np.arange(h)[::-1]+.5)/scale)
    X = X.astype(np.float32); Y = Y.astype(np.float32)
    P = dict(X=X, Y=Y, W=W, H=H, F=F, sdf=bil(F['sdf'], X, Y), calm=bil(F['calm'], X, Y), d=bil(F['detail'], X, Y),
             m=bil(F['micro'], X, Y), L=bil(F['large'], X, Y))
    sdf = P['sdf']
    edge = sstep(-1.0, 1.0, sdf)
    if name in ('plaz', 'ostrovy'):
        bg = sea(P, name); fg = sand(P, s) if name == 'plaz' else island(P, s)
        out = mix(bg, fg, edge)
    elif name == 'les':
        out = clouds(P, s, forest(P, s))
    elif name == 'zima':
        bg = forest(P, s, snowy=True)
        bg = bg*(1-.12*(1-sstep(-12, 0, sdf))*sstep(-24, -12, sdf))[..., None]     # mäkký tieň okolo pláne
        out = mix(bg, snowfield(P, s), edge)
    elif name == 'polia':
        bg = fields(P, s); bg = bg*(1-.15*np.clip(1+sdf/8, 0, 1))[..., None]
        out = mix(bg, pasture(P, s), edge)
    else:
        bg = city(P, s); out = mix(bg, park(P, s), edge)
    out = out + grain(X, Y)[..., None]
    vx = (X/W-.5)*2; vy = (Y/H-.5)*2; r = np.sqrt(vx*vx+vy*vy)/1.2
    out = out*(1-.08*sstep(.55, 1.15, r))[..., None]
    return np.clip(out, 0, 1)

# ---------------------------------------------------------------- mäkké vektory
def soft_blob_shadow(p, x, y, r, a=.18):
    c = p.c
    for k, (dx, dy, rr, aa) in enumerate([(r*.35, -r*.3, r*1.05, a*.6), (r*.25, -r*.2, r*.9, a*.6)]):
        g = cairo.RadialGradient(x+dx, y+dy, 0, x+dx, y+dy, rr)
        g.add_color_stop_rgba(0, 0, 0, 0, aa); g.add_color_stop_rgba(.6, 0, 0, 0, aa*.6); g.add_color_stop_rgba(1, 0, 0, 0, 0)
        c.set_source(g); c.arc(x+dx, y+dy, rr, 0, 6.2832); c.fill()

def soft_tree(p, x, y, r, cols=(0x3F6E30, 0x5E9246, 0x8DBE62)):
    c = p.c; dark, mid, light = [rgb(h) for h in cols]
    g = cairo.RadialGradient(x-r*.35, y+r*.35, r*.05, x, y, r)
    g.add_color_stop_rgb(0, *map(float, light)); g.add_color_stop_rgb(.5, *map(float, mid)); g.add_color_stop_rgb(1, *map(float, dark))
    c.set_source(g); c.arc(x, y, r, 0, 6.2832); c.fill()

def building(p, x0, y0, x1, y1, hgt, seed, roof):
    c = p.c; lift = hgt*.18; rc = rgb(roof)
    c.set_source_rgb(*map(float, rc*.72)); c.rectangle(x0, y0, x1-x0, lift); c.fill()
    if lift > 7:
        p.rgba(0xF6F0DC, .28)
        for r in range(int(lift/6)):
            c.rectangle(x0+1.5, y0+2+r*6, x1-x0-3, 1.6); c.fill()
    g = cairo.LinearGradient(x0, y1+lift, x1, y0+lift)
    g.add_color_stop_rgb(0, *map(float, np.clip(rc*1.06, 0, 1))); g.add_color_stop_rgb(1, *map(float, rc*.95))
    c.set_source(g); c.rectangle(x0, y0+lift, x1-x0, y1-y0); c.fill()

ROOFS = [0xC9C2B6, 0xBFA48E, 0xA9ADB3, 0xD9CCB0, 0xB8BEB0, 0x9FB4C8]

def draw_city(p, F, W, H, s):
    bw, bh, sw = 150.0, 118.0, 14.0
    builds = []
    for bj in range(int(H/bh)+1):
        for bi in range(int(W/bw)+1):
            x0 = bi*bw+sw/2+4; y0 = bj*bh+sw/2+4; x1 = (bi+1)*bw-sw/2-4; y1 = (bj+1)*bh-sw/2-4
            nx = 1+int(h1(bi, bj, 21)*2.99); ny = 1+int(h1(bi, bj, 22)*1.99)
            for a in range(nx):
                for b in range(ny):
                    lx0 = x0+(x1-x0)*a/nx+2; lx1 = x0+(x1-x0)*(a+1)/nx-2
                    ly0 = y0+(y1-y0)*b/ny+2; ly1 = y0+(y1-y0)*(b+1)/ny-2
                    sd = h1(bi*7+a, bj*5+b, 31); dt = float(bil(F['large'], (lx0+lx1)/2, (ly0+ly1)/2))
                    hgt = 8+26*sd+130*sstep(.52, .8, dt)*h1(bi*7+a, bj*5+b, 32)
                    lift = hgt*.18
                    pts = [(lx0, ly0), (lx1, ly0), (lx0, ly1+lift), (lx1, ly1+lift), ((lx0+lx1)/2, (ly0+ly1)/2)]
                    if any(bil(F['sdf'], x, y) > -10 for x, y in pts): continue
                    roof = ROOFS[int(sd*len(ROOFS)) % len(ROOFS)] if hgt < 80 else 0x8FAFCF
                    builds.append((lx0, ly0, lx1, ly1, hgt, int(sd*1e5), roof))
    c = p.c
    for (x0, y0, x1, y1, hgt, sd, roof) in builds:                       # mäkké tiene (dvojitá vrstva)
        L = hgt*.3
        for k, a in ((1.0, .10), (.6, .08)):
            c.move_to(x0, y1); c.line_to(x1, y1); c.line_to(x1+L*k, y1-L*.6*k); c.line_to(x1+L*k, y0-L*.6*k)
            c.line_to(x0+L*k, y0-L*.6*k); c.line_to(x0, y0); c.close_path(); p.rgba(0x2A3040, a); c.fill()
    for b in sorted(builds, key=lambda b: -b[1]): building(p, *b)
    # stromy pozdĺž ulíc
    for bj in range(int(H/bh)+2):
        for bi in range(int(W/bw)+2):
            for k in range(6):
                if h1(bi*11+k, bj, 51) > .55: continue
                horiz = k < 3
                x = bi*bw + (h1(bi, bj*13+k, 52)*.8+.1)*bw if horiz else bi*bw + sw/2 + 2.5
                y = bj*bh + sw/2 + 2.5 if horiz else bj*bh + (h1(bi*3+k, bj, 53)*.8+.1)*bh
                if bil(F['sdf'], x, y) > -8: continue
                soft_blob_shadow(p, x, y, 4.2); soft_tree(p, x, y, 4.2)

def vectors(p, F, W, H, icons, name, s):
    if name == 'mesto': draw_city(p, F, W, H, s)
    items = []
    g = 26
    for j in range(int(H/g)+2):
        for i in range(int(W/g)+2):
            x = (i+.1+.8*h1(i, j, s+11))*g; y = (j+.1+.8*h1(i, j, s+23))*g
            roll = h1(i, j, s+37); sz = h1(i, j, s+53)
            sd = float(bil(F['sdf'], x, y)); dens = float(sstep(.4, .6, bil(F['detail'], x, y)))
            r = None
            if name == 'ostrovy' and sd > 16 and roll < .25+.4*dens: r = 6+5*sz
            if name == 'mesto' and sd > 7 and roll < .3+.3*dens: r = 5+4*sz
            if name == 'polia' and -40 < sd < -8 and roll < .03: r = 5+3*sz
            if r is None: continue
            if any(x0-r-4 < x < x1+r+4 and y0-r-4 < y < y1+r+4 for (x0, y0, x1, y1) in icons): continue
            items.append((x, y, r))
    for (x, y, r) in items: soft_blob_shadow(p, x, y, r)
    for (x, y, r) in sorted(items, key=lambda t: -t[1]): soft_tree(p, x, y, r)

def render(W, H, icons, name, scale=1.0):
    th = THEMES[name]; s = th['seed']
    F = build_field(W, H, icons, th)
    col = compose(F, W, H, scale, name, s)
    h, w = col.shape[:2]
    arr = np.zeros((h, w, 4), np.uint8)
    arr[..., 0] = (col[..., 2]*255).astype(np.uint8); arr[..., 1] = (col[..., 1]*255).astype(np.uint8)
    arr[..., 2] = (col[..., 0]*255).astype(np.uint8); arr[..., 3] = 255
    surf = cairo.ImageSurface.create_for_data(memoryview(arr), cairo.FORMAT_ARGB32, w, h, w*4)
    p = Pen(surf, H, scale)
    vectors(p, F, W, H, icons, name, s)
    for (x0, y0, x1, y1) in icons:                                          # simulované ikony
        ix = (x0+x1)/2-24; iy = y1-14
        p.rgba(0x000000, .18); p.c.rectangle(ix+1.5, iy-50, 48, 48); p.c.fill()
        p.rgba(0x4A86E8); p.c.rectangle(ix, iy-48, 48, 48); p.c.fill()
        p.rgba(0x333333, .9); p.c.rectangle(ix+4, iy-64, 40, 6); p.c.fill()
    surf.flush(); return surf

def layout(W, H):
    def icon(x, y): return (x-6, y-80-6, x+64+6, y+6)
    ic = []
    for i in range(2):
        for j in range(5): ic.append(icon(16+i*80, H-12-j*96))
    for (x, y) in [(560, 640), (640, 640), (560, 544), (640, 544), (1150, 780), (1500, 360)]:
        ic.append(icon(x, y))
    return ic

if __name__ == '__main__':
    out = sys.argv[1]; names = sys.argv[2:] or list(THEMES)
    W, H = 1600, 900
    for n in names:
        render(W, H, layout(W, H), n).write_to_png(f'{out}/{n}.png')
    print('ok')
