import Foundation

/// Maľovaný štýl: mäkké mramorované plochy, ťahy štetca v tráve, bodky spŕšky,
/// zrno papiera. Okolie (more, les, polia, mesto, zasnežený les) a povrch pod
/// dokumentmi (piesok, ostrov, oblaky, pastvina, park, snehová pláň).
struct Painters {
    let theme: Theme
    let field: TerrainField
    private let s: UInt32
    private let fiber = ValueNoise(seed: 77)

    init(theme: Theme, field: TerrainField) {
        self.theme = theme
        self.field = field
        self.s = theme.seed
    }

    // MARK: - Pomôcky

    /// Krátky ťah štetca v každej bunke (tráva).
    @inline(__always)
    private func hatch(_ x: Float, _ y: Float, _ seed: UInt32, cw: Float = 8, chh: Float = 6, p: Float = 0.6,
                       len: Float = 1.7, th: Float = 0.42, slant: Float = 0.18) -> Float {
        let ci = (x / cw).rounded(.down), cj = (y / chh).rounded(.down)
        let i = Int32(ci), j = Int32(cj)
        guard hash2(i, j, seed &+ 3) < p else { return 0 }
        let ox = (hash2(i, j, seed) * 0.6 + 0.2) * cw
        let oy = (hash2(i, j, seed &+ 1) * 0.6 + 0.2) * chh
        let dx = x - (ci * cw + ox), dy = y - (cj * chh + oy)
        let sl = (hash2(i, j, seed &+ 2) - 0.5) * slant * 2
        let dyy = dy - dx * sl
        return (1 - sstep(len - 0.6, len + 0.4, abs(dx))) * (1 - sstep(th - 0.25, th + 0.25, abs(dyy)))
    }

    /// Bodky (spŕška, trblietky, zrnká piesku, kvietky).
    @inline(__always)
    private func dots(_ x: Float, _ y: Float, _ seed: UInt32, cell: Float, p: Float, r: Float) -> Float {
        let ci = (x / cell).rounded(.down), cj = (y / cell).rounded(.down)
        let i = Int32(ci), j = Int32(cj)
        guard hash2(i, j, seed &+ 2) < p else { return 0 }
        let dx = x - (ci + hash2(i, j, seed)) * cell
        let dy = y - (cj + hash2(i, j, seed &+ 1)) * cell
        return 1 - sstep(r * 0.6, r * 1.2, (dx * dx + dy * dy).squareRoot())
    }

    @inline(__always)
    private func grain(_ x: Float, _ y: Float) -> Float {
        (hash2(Int32((x * 2).rounded(.down)), Int32((y * 2).rounded(.down)), 3) - 0.5) * 0.035
            + (fiber.sample(x / 1.3, y / 5) - 0.5) * 0.03
    }

    // MARK: - Pixel

    func pixel(x: Float, y: Float, _ ch: [Float]) -> RGB {
        let sdf = ch[TerrainField.sdf]
        let edge = sstep(-1, 1, sdf)
        var c: RGB
        switch theme.id {
        case .plaz, .ostrovy:
            let bg = edge < 1 ? sea(x, y, ch) : .black
            let fg = edge > 0 ? (theme.id == .plaz ? sand(x, y, ch) : island(x, y, ch)) : .black
            c = bg.mix(fg, edge)
        case .les:
            c = clouds(x, y, ch, over: forest(x, y, ch, snowy: false))
        case .zima:
            var bg = RGB.black
            if edge < 1 {
                bg = forest(x, y, ch, snowy: true)
                bg = bg.scaled(1 - 0.12 * (1 - sstep(-12, 0, sdf)) * sstep(-24, -12, sdf))   // mäkký tieň okolo pláne
            }
            c = bg.mix(edge > 0 ? snowfield(x, y, ch) : .black, edge)
        case .polia:
            var bg = RGB.black
            if edge < 1 { bg = fields(x, y, ch).scaled(1 - 0.15 * clamp01(1 + sdf / 8)) }
            c = bg.mix(edge > 0 ? pasture(x, y, ch) : .black, edge)
        case .mesto:
            c = (edge < 1 ? city(x, y, ch) : .black).mix(edge > 0 ? park(x, y, ch) : .black, edge)
        }
        return c.offset(grain(x, y))
    }

    // MARK: - More

    private static let seaPlaz: [(Float, RGB)] = [(0, RGB(0xCFF1E8)), (8, RGB(0x9BE5DC)), (24, RGB(0x55CFD2)),
                                                  (70, RGB(0x23B4C8)), (150, RGB(0x1499BD)), (320, RGB(0x0E82AC))]
    private static let seaIslands: [(Float, RGB)] = [(0, RGB(0xC2ECE2)), (8, RGB(0x86DDD5)), (24, RGB(0x3FBFCB)),
                                                     (70, RGB(0x1E9CC0)), (150, RGB(0x1579AE)), (320, RGB(0x10609A))]

    @inline(__always)
    private func ramp(_ stops: [(Float, RGB)], _ v: Float) -> RGB {
        if v <= stops[0].0 { return stops[0].1 }
        for k in 1..<stops.count where v < stops[k].0 {
            let a = stops[k - 1], b = stops[k]
            return a.1.mix(b.1, (v - a.0) / (b.0 - a.0))
        }
        return stops[stops.count - 1].1
    }

    /// Pena v danom bode: lem a spŕška pri brehu, línie ďalej od brehu (0–1).
    func foam(x: Float, y: Float) -> (shore: Float, lines: Float) {
        let sdf = field.value(TerrainField.sdf, x, y)
        guard sdf < 2 else { return (0, 0) }
        let nz = field.value(TerrainField.n40, x, y)
        let depth = max(-sdf, 0) + (nz - 0.5) * 6
        let brk = sstep(0.3, 0.55, field.value(TerrainField.n22, x, y))
        let lip = (1 - sstep(1.2, 3.2, depth)) * (0.75 + 0.25 * brk)
        let spray = depth < 25 ? dots(x, y, s &+ 40, cell: 4.2, p: 0.55 * expf(-depth / 5), r: 0.6) : 0
        let z1 = (depth - (13 + (nz - 0.5) * 10)) / 0.9
        let l1 = expf(-z1 * z1) * sstep(0.42, 0.6, field.value(TerrainField.n55, x, y)) * 0.55
        let z2 = (depth - (28 + (nz - 0.5) * 16)) / 0.8
        let l2 = expf(-z2 * z2) * sstep(0.5, 0.66, field.value(TerrainField.n70, x, y)) * 0.32
        let water = clamp01(-sdf + 0.5)
        return (min(max(lip, spray), 1) * water, max(l1, l2) * water)
    }

    private func sea(_ x: Float, _ y: Float, _ ch: [Float]) -> RGB {
        let nz = ch[TerrainField.n40]
        let depth = max(-ch[TerrainField.sdf], 0) + (nz - 0.5) * 6
        var c = ramp(theme.id == .plaz ? Self.seaPlaz : Self.seaIslands, depth)
        let m = ch[TerrainField.mot200]
        c = c.mix(c.scaled(1.08).offset(0.04), sstep(0.52, 0.72, m) * sstep(10, 40, depth) * 0.8)   // svetlé škvrny
        c = c.mix(c.scaled(0.93), sstep(0.6, 0.8, 1 - m) * sstep(40, 120, depth) * 0.5)
        // pena je v podklade z polovice – zvyšok „dýcha“ v animácii
        let f = foam(x: x, y: y)
        c = c.mix(.white, max(f.shore, f.lines) * 0.55)
        let spark = depth > 20 ? dots(x, y, s &+ 50, cell: 22, p: 0.35, r: 0.75) * sstep(20, 60, depth) : 0
        return c.mix(.white, spark * 0.6)
    }

    // MARK: - Lúka a les

    private func grassfield(_ x: Float, _ y: Float, _ ch: [Float], seed: UInt32,
                            _ c0: RGB, _ c1: RGB, _ c2: RGB, mark: RGB, markA: Float) -> RGB {
        let m = ch[TerrainField.mot140]
        var c = c0.mix(c1, sstep(0.4, 0.65, m))
        c = c.mix(c2, sstep(0.55, 0.75, ch[TerrainField.n70]) * 0.6)
        let h = hatch(x, y, seed &+ 60)
        return h > 0 ? c.mix(mark, h * markA * (0.6 + 0.4 * sstep(0.3, 0.7, m))) : c
    }

    private func forest(_ x: Float, _ y: Float, _ ch: [Float], snowy: Bool) -> RGB {
        let thr: Float = snowy ? 0.22 : 0.5
        var bg = snowy
            ? grassfield(x, y, ch, seed: s, RGB(0xE4ECF3), RGB(0xD6E1EC), RGB(0xEEF3F8), mark: RGB(0x9FB4CB), markA: 0.55)
            : grassfield(x, y, ch, seed: s, RGB(0xBCD47E), RGB(0xA8C86C), RGB(0xCADF91), mark: RGB(0x5E8A3A), markA: 0.5)
        var g = ch[TerrainField.large] + (ch[TerrainField.detail] - 0.5) * 0.25
        if abs(g - thr) < 0.06 {                                       // kopovitý okraj lesa
            let w = worley(x, y, cell: 26, seed: s &+ 44, jitter: 0.8)
            g += clamp01(1 - w.f1 / (14 + w.id * 6)) * 0.035
        }
        let tree = sstep(thr - 0.012, thr + 0.012, g)
        // tieň lesa na lúku (vpravo dole)
        if tree < 1 {
            let gs = field.value(TerrainField.large, x - 7, y + 7) + (field.value(TerrainField.detail, x - 7, y + 7) - 0.5) * 0.25
            bg = bg.scaled(1 - 0.22 * sstep(thr - 0.012, thr + 0.012, gs) * (1 - tree))
        }
        guard tree > 0 else { return bg }

        let (c0, c1, c2, rim) = snowy
            ? (RGB(0x2E5A55), RGB(0x24494A), RGB(0x3B6B63), RGB(0x1D3B3C))
            : (RGB(0x4E7F3B), RGB(0x3E6C30), RGB(0x5E9246), RGB(0x2F5A26))
        var fc = c0.mix(c1, sstep(0.45, 0.7, ch[TerrainField.mot60]))
        fc = fc.mix(c2, sstep(0.6, 0.8, ch[TerrainField.n30]) * 0.7)
        let crowns = worley(x, y, cell: 9, seed: s &+ 92, jitter: 0.9)          // drobná štruktúra korún
        fc = fc.scaled(1 - 0.10 * sstep(0.4, 1, crowns.f1 / 6))
        fc = fc.mix(rim, (1 - sstep(0, 0.03, g - thr)) * 0.5)
        if snowy {                                                            // ojínené koruny
            fc = fc.mix(RGB(0xDCE6EE), 0.22 + 0.18 * sstep(0.4, 0.7, ch[TerrainField.n50]))
            fc = fc.mix(RGB(0xF4F8FB), dots(x, y, s &+ 93, cell: 4.5, p: 0.45, r: 0.95) * 0.75)
            fc = fc.mix(RGB(0xF4F8FB), dots(x, y, s &+ 94, cell: 7, p: 0.3, r: 1.4) * 0.5)
        }
        return bg.mix(fc, tree)
    }

    // MARK: - Polia

    private static let fieldColors: [(RGB, RGB)] = [
        (RGB(0xE2CD8A), RGB(0xD2B96E)), (RGB(0xA9C672), RGB(0x93B35E)), (RGB(0xB89371), RGB(0x9C7A5A)),
        (RGB(0xB7A6D2), RGB(0x9C8ABD)), (RGB(0xE9D874), RGB(0xD8C55A)), (RGB(0xD8C79C), RGB(0xC6B385)),
        (RGB(0x9FBF6A), RGB(0x8AAA58)),
    ]

    private func fields(_ x: Float, _ y: Float, _ ch: [Float]) -> RGB {
        let wx = x + ch[TerrainField.warpX], wy = y + ch[TerrainField.warpY]   // zvlnené hranice parciel
        let w = worley(wx, wy, cell: 175, seed: s &+ 60, jitter: 0.9)
        let pair = Self.fieldColors[min(Int(w.id * Float(Self.fieldColors.count)), Self.fieldColors.count - 1)]
        let ang = w.id * .pi * 7.3
        let u = wx * cosf(ang) + wy * sinf(ang)
        let rows = powf(sinf(u / 2.4) * 0.5 + 0.5, 1.5)
        var c = pair.0.mix(pair.1, rows * 0.55)
        c = c.scaled(1 + (ch[TerrainField.mot140] - 0.5) * 0.12)
        let edge = w.f2 - w.f1
        guard edge < 12 else { return c }
        c = c.scaled(1 - 0.2 * (1 - sstep(4, 12, edge)))
        if edge < 9 {
            let hw = worley(x, y, cell: 7, seed: s &+ 61, jitter: 0.9)
            let bump = clamp01(1 - hw.f1 / (4 + hw.id * 2))
            let hedge = 1 - sstep(2, 6.5, edge + (1 - bump) * 2.5)
            c = c.mix(RGB(0x4E7F3B).mix(RGB(0x6E9C4E), bump), hedge * 0.95)
        }
        return c
    }

    // MARK: - Mesto

    private func city(_ x: Float, _ y: Float, _ ch: [Float]) -> RGB {
        let bw = CityGrid.blockW, bh = CityGrid.blockH, half = CityGrid.street / 2
        let u = fmodf(x, bw), v = fmodf(y, bh)
        let hu = min(u, bw - u), hv = min(v, bh - v), ds = min(hu, hv)
        let m = sstep(0.4, 0.65, ch[TerrainField.mot140])
        let ground = RGB(0xDDD6C9).mix(RGB(0xD2CABB), m)
        let road = RGB(0x9A9DA2).mix(RGB(0x8D9095), m)
        var c = road.mix(ground, sstep(half - 0.6, half + 0.6, ds))
        let z = (ds - (half + 1.6)) / 1.0
        c = c.mix(RGB(0xE9E4DA), expf(-z * z) * 0.7)                           // chodník
        if (hv < 0.6 && fmodf(x, 16) < 8 && hu > half) || (hu < 0.6 && fmodf(y, 16) < 8 && hv > half) {
            c = c.mix(RGB(0xF1EEE4), 0.6)
        }
        return c
    }

    // MARK: - Povrchy pod dokumentmi

    private func sand(_ x: Float, _ y: Float, _ ch: [Float]) -> RGB {
        let inl = max(ch[TerrainField.sdf], 0)
        var c = RGB(0xF5EEDF).mix(RGB(0xEDE2CC), sstep(0.4, 0.7, ch[TerrainField.mot140]) * (1 - 0.5 * ch[TerrainField.calm]))
        c = c.mix(RGB(0xD8C7A6), dots(x, y, s &+ 21, cell: 3.2, p: 0.35, r: 0.45) * 0.55)
        c = c.mix(.white, dots(x, y, s &+ 22, cell: 3.6, p: 0.2, r: 0.4) * 0.6)
        let nz = ch[TerrainField.n40]
        c = c.mix(RGB(0xE2D3B6), (1 - sstep(3, 14 + (nz - 0.5) * 8, inl)) * 0.7)          // mokrý piesok
        let z = (inl - (9 + (nz - 0.5) * 8)) / 0.8
        return c.mix(.white, expf(-z * z) * sstep(0.4, 0.6, ch[TerrainField.n30]) * 0.5)  // stopa ustupujúcej vlny
    }

    private func island(_ x: Float, _ y: Float, _ ch: [Float]) -> RGB {
        let inl = max(ch[TerrainField.sdf], 0)
        let grass = grassfield(x, y, ch, seed: s &+ 200, RGB(0x9CCB6A), RGB(0x86B957), RGB(0xB0D67C),
                               mark: RGB(0x4C7A32), markA: 0.45)
        let nz = (ch[TerrainField.n40] - 0.5) * 8
        let t = sstep(12 + nz, 15 + nz, inl)
        if t >= 1 { return grass }
        return sand(x, y, ch).mix(grass, t)
    }

    @inline(__always)
    private func cloudShape(_ x: Float, _ y: Float, _ sdf: Float) -> (shape: Float, f1: Float, f2: Float, r: Float) {
        let w = worley(x, y, cell: 30, seed: 333, jitter: 0.7)
        let r = 14 + w.id * 7
        return (sdf + 0.85 * min(max(r - w.f1, -14), 22), w.f1, w.f2, r)
    }

    private func clouds(_ x: Float, _ y: Float, _ ch: [Float], over: RGB) -> RGB {
        var bg = over
        let ss = field.value(TerrainField.sdf, x - 10, y + 14)
        if ss > -30 {                                                         // mäkký tieň oblaku
            bg = bg.scaled(1 - 0.18 * sstep(-6, 10, cloudShape(x - 10, y + 14, ss).shape))
        }
        let sdf = ch[TerrainField.sdf]
        guard sdf > -24 else { return bg }
        let cs = cloudShape(x, y, sdf)
        let a = sstep(-1, 1, cs.shape)
        guard a > 0 else { return bg }
        let wl = worley(x + 6, y - 6, cell: 30, seed: 333, jitter: 0.7)
        let lit = clamp01(1 - wl.f1 / (14 + wl.id * 7))
        let inner = sstep(0, 46, cs.shape)
        var c = RGB(0xC5D4E6).mix(RGB(0xFBFDFF), sstep(0.05, 0.6, lit + inner * 0.45))
        let seam = (1 - sstep(0.6, 2.6, cs.f2 - cs.f1)) * (cs.f1 < cs.r * 1.1 ? 1 : 0) * (1 - inner * 0.7)
        c = c.mix(RGB(0xD3DFEC), seam * 0.55).offset(grain(x, y) * 0.5)
        return bg.mix(c, a)
    }

    private static let flowerColors = [RGB(0xFFFFFF), RGB(0xF6D55C), RGB(0xEE8FB0)]

    private func pasture(_ x: Float, _ y: Float, _ ch: [Float]) -> RGB {
        let inl = max(ch[TerrainField.sdf], 0)
        var c = grassfield(x, y, ch, seed: s &+ 300, RGB(0xC3DF8E), RGB(0xB2D47C), RGB(0xD3E8A2),
                           mark: RGB(0x6A9645), markA: 0.4)
        if ch[TerrainField.calm] < 0.3 {
            let f = dots(x, y, s &+ 301, cell: 7, p: 0.25, r: 0.9)
            if f > 0 {
                let pick = Int(hash2(Int32((x / 7).rounded(.down)), Int32((y / 7).rounded(.down)), s &+ 302) * 3) % 3
                c = c.mix(Self.flowerColors[pick], f * 0.9)
            }
        }
        return c.mix(c.scaled(0.85), (1 - sstep(1, 5, inl)) * 0.6)                // tmavší lem trávy
    }

    private func park(_ x: Float, _ y: Float, _ ch: [Float]) -> RGB {
        let c = grassfield(x, y, ch, seed: s &+ 400, RGB(0xA9CF7E), RGB(0x98C16C), RGB(0xB9DA8E),
                           mark: RGB(0x5A8A3C), markA: 0.4)
        return c.mix(RGB(0xEDE8DE), 1 - sstep(1, 3, max(ch[TerrainField.sdf], 0)))  // obrubník
    }

    private func snowfield(_ x: Float, _ y: Float, _ ch: [Float]) -> RGB {
        let inl = max(ch[TerrainField.sdf], 0)
        var c = RGB(0xFBFDFF).mix(RGB(0xEEF3F9), sstep(0.4, 0.7, ch[TerrainField.mot140]) * (1 - 0.5 * ch[TerrainField.calm]))
        c = c.mix(.white, dots(x, y, s &+ 501, cell: 5, p: 0.2, r: 0.5) * 0.8)
        return c.mix(RGB(0xD5E0EC), (1 - sstep(0, 9, inl)) * 0.75)               // modrastý okraj
    }
}
