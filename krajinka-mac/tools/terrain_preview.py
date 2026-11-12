# Verný prepis Terrain.swift / Theme.swift / Decorator.swift do numpy – len na náhľad.
import numpy as np
from PIL import Image, ImageDraw
import sys

U = np.uint32
def hash2(x, y, seed):
    x = np.asarray(x).astype(np.int64).astype(np.uint32); y = np.asarray(y).astype(np.int64).astype(np.uint32)
    with np.errstate(over='ignore'):
        h = x * U(374761393)
        h = h + y * U(668265263)
        h = h + U(seed) * U(2246822519)
        h = (h ^ (h >> U(13))) * U(1274126177)
        h ^= h >> U(16)
    return (h & U(0xFFFFFF)).astype(np.float32) / np.float32(0xFFFFFF)

def smooth(t): return t*t*(3-2*t)
def vnoise(x, y, seed):
    xf = np.floor(x); yf = np.floor(y)
    tx = smooth(x-xf); ty = smooth(y-yf)
    ix = xf.astype(np.int64); iy = yf.astype(np.int64)
    a = hash2(ix, iy, seed); b = hash2(ix+1, iy, seed); c = hash2(ix, iy+1, seed); d = hash2(ix+1, iy+1, seed)
    top = a+(b-a)*tx; bot = c+(d-c)*tx
    return top+(bot-top)*ty
def fbm(x, y, seed, octaves):
    s = 0; amp = .5; fr = 1; norm = 0
    for o in range(octaves):
        off = o*17.31
        s = s + amp*vnoise(x*fr+off, y*fr-off, seed); norm += amp; amp *= .5; fr *= 2
    return s/norm

def rgb(h): return np.array([(h>>16)&255, (h>>8)&255, h&255], np.float32)/255
THEMES = {
 'beach': dict(seed=1, wl=.30, deepest=0x1D5A86, deep=0x2A7DAE, shallow=0x46B4C6, shore=0xD6C08A, land=0xD2B77C, landAlt=0xC6A96B, clearing=0xC2A66C, outline=0xF2EFE2, foam=(0xFFFFFF,.6),
   trunk=0x8A6A3F, fd=0x3B8A38, fl=0x67AE4D, rock=0x8C8981, rl=0xB3AEA3, accent=0xF4E9C8),
 'meadow': dict(seed=2, wl=.50, deepest=0x214D66, deep=0x2E6A86, shallow=0x4E8DA4, shore=0x7A8F52, land=0x6C9E48, landAlt=0x5D8F3F, clearing=0x7BA954, outline=0x3E6638, foam=(0xD3EBDB,.25),
   trunk=0x6B4F2E, fd=0x3A7633, fl=0x5C9C44, rock=0x7F8079, rl=0xA6A79E, accent=0xF2B8C6),
 'winter': dict(seed=3, wl=.46, deepest=0x3B5A77, deep=0x527796, shallow=0x7CA0BC, shore=0xD3DEE8, land=0xC7D3E0, landAlt=0xBCC9D7, clearing=0x9AADC2, outline=0xE6EDF3, foam=(0xFFFFFF,.2),
   trunk=0x5E4A3A, fd=0x2E5949, fl=0x3E725F, rock=0x7A8593, rl=0xA9B3BF, accent=0xF2F6FA),
}
DEEP, SHORE, LAND, CLEAR = .30, .42, .47, .74

def field(W, H, icons, th, cell=6.0):
    cols = int(np.ceil(W/cell))+2; rows = int(np.ceil(H/cell))+2
    px = np.arange(cols, dtype=np.float32)*cell; py = np.arange(rows, dtype=np.float32)*cell
    X, Y = np.meshgrid(px, py)
    n = fbm(X/280, Y/280, th['seed'], 4)
    f = th['wl'] + (n-.5)*.95
    inf = np.zeros_like(f); minD = np.full_like(f, np.inf)
    for (x0,y0,x1,y1) in icons:
        dx = np.maximum.reduce([x0-X, np.zeros_like(X), X-x1]); dy = np.maximum.reduce([y0-Y, np.zeros_like(Y), Y-y1])
        d = np.sqrt(dx*dx+dy*dy); minD = np.minimum(minD, d)
        t = np.clip(1-d/120, 0, 1); inf += np.where(d<120, smooth(t), 0)
    f += np.minimum(inf, 1.3)*.62
    if icons:
        boost = .86 - np.maximum(0, minD-8)/70
        f = np.maximum(f, boost)
    det = fbm(X/90, Y/90, th['seed']+977, 2)
    return f, det, cell

def bilinear(A, x, y, cell):
    rows, cols = A.shape
    gx = np.clip(x/cell, 0, cols-1.001); gy = np.clip(y/cell, 0, rows-1.001)
    ix = gx.astype(int); iy = gy.astype(int); fx = gx-ix; fy = gy-iy
    a = A[iy, ix]; b = A[iy, ix+1]; c = A[iy+1, ix]; d = A[iy+1, ix+1]
    return (a+(b-a)*fx) + ((c+(d-c)*fx) - (a+(b-a)*fx))*fy

def color(f, d, th):
    C = {k: rgb(th[k]) for k in ['deepest','deep','shallow','shore','land','landAlt','clearing']}
    out = np.zeros(f.shape+(3,), np.float32)
    mix = lambda a,b,t: a+(b-a)*t[...,None]
    m = f < DEEP; t = np.clip(f/DEEP,0,1); out[m] = mix(C['deepest'], C['deep'], t)[m]
    m = (f>=DEEP)&(f<SHORE); t = smooth(np.clip((f-DEEP)/(SHORE-DEEP),0,1)); out[m] = mix(C['deep'],C['shallow'],t)[m]
    m = (f>=SHORE)&(f<LAND); t = np.clip((f-SHORE)/(LAND-SHORE),0,1); out[m] = mix(C['shore'],C['land'],t*t)[m]
    m = (f>=LAND)&(f<CLEAR); patch = smooth(np.clip((d-.38)/.24,0,1)); base = mix(C['land'],C['landAlt'],patch)
    t = np.clip((f-(CLEAR-.05))/.05,0,1); out[m] = mix(base, np.broadcast_to(C['clearing'], base.shape), t)[m]
    m = f>=CLEAR; out[m] = (C['clearing'][None,None,:] + ((d-.5)*.03)[...,None])[m]
    return out

def decorations(draw, W, H, F, cell, icons, th, name, s_px):
    # 1:1 s Decorator.swift (zjednodušené tvary), súradnice: y hore -> prevrátime
    g = 52; items = []
    def samp(x, y): return float(bilinear(F, np.array([x]), np.array([y]), cell)[0])
    for j in range(int(H/g)+1):
        for i in range(int(W/g)+1):
            jx = float(hash2(i,j,th['seed']+11)); jy = float(hash2(i,j,th['seed']+23)); roll = float(hash2(i,j,th['seed']+37)); sz = float(hash2(i,j,th['seed']+53))
            p = ((i+.15+.7*jx)*g, (j+.15+.7*jy)*g); f = samp(*p)
            onLand = .49 < f < .71; kind = None
            if name=='beach':
                if onLand and roll<.5: kind = 'palm' if roll<.36 else 'bush'
                elif .31<f<.39 and roll<.07: kind='rock'
            elif name=='meadow':
                if onLand and roll<.52: kind = 'tree' if roll<.42 else 'bush'
                elif .22<f<.38 and roll<.06: kind='lily'
            else:
                if onLand and roll<.62: kind='pine'
                elif onLand and roll<.68: kind='rock'
            if not kind: continue
            base = dict(palm=30,tree=34,pine=30,bush=16,rock=14,lily=12)[kind]; s = base*(.8+.45*sz)
            bx0, by0, bx1, by1 = p[0]-s*.75-14, p[1]-s*.3-14, p[0]+s*.75+14, p[1]+s*1.15+14
            if any(not (bx1<x0 or bx0>x1 or by1<y0 or by0>y1) for (x0,y0,x1,y1) in icons): continue
            items.append((p, kind, s))
    items.sort(key=lambda it: -it[0][1])
    col = lambda h,a=255: tuple(int(v*255) for v in rgb(h))+(a,)
    def P(x,y): return (x*s_px, (H-y)*s_px)
    def circ(c, r, fill): x,y=P(*c); r*=s_px; draw.ellipse([x-r,y-r,x+r,y+r], fill=fill)
    def ell(cx0, cy0, w, h, fill):
        x0,y1 = P(cx0, cy0); x1,y0 = P(cx0+w, cy0+h); draw.ellipse([x0,y0,x1,y1], fill=fill)
    for (x,y), k, s in items:
        if k!='lily': ell(x-s*.32, y-s*.2, s, s*.36, (0,0,0,40))
        if k=='palm':
            top=(x+s*.22,y+s*.95); draw.line([P(x,y),P(x+s*.1,y+s*.5),P(*top)], fill=col(th['trunk']), width=max(1,int(s*.13*s_px)))
            for a in range(6):
                ang = a/6*2*np.pi+.3; e=(top[0]+np.cos(ang)*s*.31, top[1]+np.sin(ang)*s*.31); circ(e, s*.13, col(th['fd']))
            circ(top, s*.1, col(th['fl']))
        elif k=='tree':
            x0,y0=P(x-s*.07,y+s*.4); x1,y1=P(x+s*.07,y); draw.rectangle([x0,y0,x1,y1], fill=col(th['trunk']))
            circ((x-s*.22,y+s*.5), s*.3, col(th['fd'])); circ((x+s*.22,y+s*.5), s*.3, col(th['fd'])); circ((x,y+s*.66), s*.42, col(th['fd'])); circ((x-s*.12,y+s*.78), s*.2, col(th['fl']))
        elif k=='pine':
            for kk in range(3):
                by=y+s*(.15+.28*kk); half=s*(.45-.11*kk); ay=by+s*.5
                draw.polygon([P(x-half,by),P(x+half,by),P(x,ay)], fill=col(th['fd']))
                ch=s*.18; chh=half*ch/(ay-by); draw.polygon([P(x-chh,ay-ch),P(x+chh,ay-ch),P(x,ay)], fill=col(th['accent']))
        elif k=='bush':
            circ((x-s*.22,y+s*.2), s*.28, col(th['fd'])); circ((x+s*.22,y+s*.2), s*.28, col(th['fd'])); circ((x,y+s*.36), s*.32, col(th['fd'])); circ((x-s*.06,y+s*.46), s*.13, col(th['fl']))
        elif k=='rock':
            ell(x-s*.45,y-s*.3,s*.9,s*.6,col(th['rock'])); ell(x-s*.3,y-s*.02,s*.5,s*.25,col(th['accent'] if name=='winter' else th['rl']))
        elif k=='lily':
            ell(x-s*.5,y-s*.38,s,s*.76,col(th['fl']))

def render(W, H, icons, name, scale):
    th = THEMES[name]
    F, D, cell = field(W, H, icons, th)
    w, h = int(W*scale), int(H*scale)
    xs = (np.arange(w)+.5)/scale; ys = (np.arange(h)[::-1]+.5)/scale  # riadok 0 = hore
    X, Y = np.meshgrid(xs, ys)
    f = bilinear(F, X, Y, cell); d = bilinear(D, X, Y, cell)
    c = color(f, d, th)
    dl = np.abs(f-SHORE); m = dl < .006
    c[m] = c[m] + (rgb(th['outline'])-c[m])*(.55*(1-dl[m]/.006))[:,None]
    # pena (statický snímok pri strednej opacite)
    wa = np.clip((SHORE-f)/.006+.5, 0, 1); t = (SHORE-f)/.05
    fa = np.where((f<SHORE)&(t<1), (1-t)**2*wa, 0)*th['foam'][1]
    c = c + (rgb(th['foam'][0])-c)*fa[...,None]
    img = Image.fromarray((np.clip(c,0,1)*255).astype(np.uint8)).convert('RGBA')
    dr = ImageDraw.Draw(img, 'RGBA')
    decorations(dr, W, H, F, cell, icons, th, name, scale)
    # ikony (simulácia: štvorček + popisok), aby bolo vidno čistinky
    for (x0,y0,x1,y1) in icons:
        ix0=(x0+14)*scale; iy0=(H-(y1-8))*scale
        dr.rounded_rectangle([ix0, iy0, ix0+64*scale, iy0+64*scale], radius=10*scale, fill=(70,130,230,255))
        dr.rectangle([ix0+6*scale, iy0+70*scale, ix0+58*scale, iy0+78*scale], fill=(255,255,255,230))
    return img.convert('RGB')

def mac_icons(W, H, n, col_x=None):
    # Finder: ikony od pravého horného rohu, stĺpec po stĺpci, mriežka ~100 pt
    icons = []; x = W-110 if col_x is None else col_x; y = H-40
    for i in range(n):
        icons.append((x-14, y-64-36, x+64+14, y+8)); y -= 100
        if y < 140: y = H-40; x -= 100
    return icons

if __name__ == '__main__':
    out = sys.argv[1]
    # ultrawide 5120x1440, 7 ikon vpravo + 3 rozhádzané naľavo
    ic = mac_icons(5120, 1440, 7) + [(400-14, 900-36, 400+78, 900+72), (1500-14, 420-36, 1500+78, 420+72), (2600-14, 1100-36, 2600+78, 1100+72)]
    for name in ['beach','meadow','winter']:
        render(5120, 1440, ic, name, .3).save(f'{out}/ultrawide-{name}.png')
    render(1512, 982, mac_icons(1512, 982, 5), 'beach', .6).save(f'{out}/macbook-beach.png')
    print('ok')
