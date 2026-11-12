import Foundation

/// Hodnoty poľa v jednom pixeli (body, os y hore).
struct Sample {
    var x: Float, y: Float, H: Float
    var sdf: Float, calm: Float, detail: Float, micro: Float, large: Float
    var gx: Float, gy: Float
}

/// Pixeloví „maliari“: okolie (more, les, polia, mesto, zasnežený les) a povrch
/// pod dokumentmi (piesok, ostrov, oblaky, pastvina, park, sneh). 1:1 s
/// tools/terrain_preview.py.
enum Painters {
    static func compose(_ s: inout Sample, theme t: Theme, field: TerrainField) -> RGB {
        switch t.ground {
        case .cloud:
            var bg = backdrop(s, t, field)
            // tieň oblakov na krajine (posunutý doprava dole)
            let sx = s.x - 16, sy = s.y + 20
            let shiftedSdf = field.value(TerrainField.sdf, sx, sy)
            if shiftedSdf > -22 {
                let shadow = cloudShape(sx, sy, shiftedSdf)
                bg = bg.scaled(1 - 0.32 * sstep(-2, 6, shadow))
            }
            guard s.sdf > -22 else { return bg }
            let cs = cloudShape(s.x, s.y, s.sdf)
            let a = clamp01(cs / 0.8 + 0.5)
            guard a > 0 else { return bg }
            return bg.mix(cloudColor(s, cs), a)
        default:
            let a = clamp01(s.sdf / 0.6 + 0.5)
            var bg = RGB.black
            if a < 1 {
                bg = backdrop(s, t, field)
                if t.ground == .snow { bg = bg.scaled(1 - 0.2 * clamp01(1 + s.sdf / 10)) }
            }
            guard a > 0 else { return bg }
            let rc: RGB
            switch t.ground {
            case .sand: rc = sand(s)
            case .island: rc = island(s)
            case .pasture: rc = pasture(s)
            case .park: rc = park(s)
            case .snow, .cloud: rc = snow(s)
            }
            return a >= 1 ? rc : bg.mix(rc, a)
        }
    }

    // MARK: - Okolie

    private static func backdrop(_ s: Sample, _ t: Theme, _ field: TerrainField) -> RGB {
        switch t.backdrop {
        case .sea: return sea(s, t)
        case .forest: return forest(s, t, field)
        case .fields: return fields(s, t)
        case .city: return city(s)
        case .snowForest:
            return RGB(0xD9E3ED).mix(RGB(0xCBD7E4), sstep(0.35, 0.65, s.detail)).offset((s.micro - 0.5) * 0.04)
        }
    }

    private static func sea(_ s: Sample, _ t: Theme) -> RGB {
        let depth = max(-s.sdf, 0)
        let t1 = 1 - expf(-depth / t.seaFalloff)
        let t2 = 1 - expf(-depth / 260)
        var c = t.shallow.mix(t.deep, t1).mix(t.deepest, t2 * 0.85)
        c = c.offset((s.micro - 0.5) * 0.06 * (1 - t1)).offset((s.detail - 0.5) * 0.05 * t1)
        let z = (depth - 24) / 3.2
        c = c.mix(t.shelf, expf(-z * z) * 0.22)
        c = c.mix(.white, clamp01(1 - depth / 2.6) * 0.8)
        return c.mix(t.shelf, (s.y / s.H) * 0.07)
    }

    private static let meadow = RGB(0x9CC56A), meadowAlt = RGB(0x86B457)
    private static let crownGap = RGB(0x1E3F1D), crownDark = RGB(0x2E5E2C)
    private static let crownMid = RGB(0x4C8239), crownLight = RGB(0x86BD55)

    /// Les zhora: háje veľkých tieňovaných korún, medzi nimi lúky.
    private static func forest(_ s: Sample, _ t: Theme, _ field: TerrainField) -> RGB {
        let grove = sstep(0.47, 0.53, s.large + (s.detail - 0.5) * 0.25)
        var c = meadow.mix(meadowAlt, sstep(0.35, 0.65, s.detail)).offset((s.micro - 0.5) * 0.06)
        var tree: Float = 0
        var tc = crownDark
        if grove > 0.001 {
            let w = worley(s.x, s.y, cell: 17, seed: t.seed &+ 40, jitter: 0.75)
            let crown = clamp01(1 - w.f1 / (8.5 + w.id * 3.5))
            let wl = worley(s.x + 3, s.y - 3, cell: 17, seed: t.seed &+ 40, jitter: 0.75)
            let lit = clamp01(1 - wl.f1 / (8.5 + wl.id * 3.5))
            tc = crownGap.mix(crownDark, sstep(0, 0.25, crown))
            tc = tc.mix(crownMid, smooth(lit) * sstep(0, 0.2, crown))
            tc = tc.mix(crownLight, sstep(0.7, 1, lit) * 0.55)
            tc = tc.offset((s.micro - 0.5) * 0.04)
            tree = grove * max(sstep(0, 0.15, crown), sstep(0.55, 0.62, grove))
        }
        // tieň hája na lúku (vpravo dole)
        let gs = sstep(0.47, 0.53, field.value(TerrainField.large, s.x - 6, s.y + 6)
                       + (field.value(TerrainField.detail, s.x - 6, s.y + 6) - 0.5) * 0.25)
        c = c.scaled(1 - 0.3 * gs * (1 - tree))
        return c.mix(tc, clamp01(tree))
    }

    /// (farba, farba riadkov, sila riadkov)
    private static let crops: [(RGB, RGB, Float)] = [
        (RGB(0xE3C46A), RGB(0xC9A64B), 0.5),    // pšenica
        (RGB(0x7FA346), RGB(0x6A8C38), 0.55),   // mladé obilie
        (RGB(0x9B7652), RGB(0x7E5C3E), 0.9),    // oranisko
        (RGB(0xA48ACB), RGB(0x7E64A8), 0.8),    // levanduľa
        (RGB(0xF2D84A), RGB(0xD8BC2E), 0.3),    // repka
        (RGB(0xD9A35A), RGB(0xBF8A44), 0.6),    // kukurica
        (RGB(0xC9B27A), RGB(0xB09662), 0.6),    // strnisko
    ]
    static let fieldCell: Float = 170

    /// Políčka: Voronoiove parcely, každá s plodinou a smerom riadkov, živé ploty na hraniciach.
    private static func fields(_ s: Sample, _ t: Theme) -> RGB {
        let w = worley(s.x, s.y, cell: fieldCell, seed: t.seed &+ 60, jitter: 0.9)
        let crop = crops[min(Int(w.id * Float(crops.count)), crops.count - 1)]
        let ang = w.id * .pi * 7.3
        let u = s.x * cosf(ang) + s.y * sinf(ang)
        let wave = sinf(u / 2.2) * 0.5 + 0.5
        var c = crop.0.mix(crop.1, wave * wave * crop.2).offset((s.micro - 0.5) * 0.05)
        let edge = w.f2 - w.f1
        if edge < 9 {
            c = c.scaled(1 - 0.25 * (1 - sstep(3.5, 9, edge)))
            let hedge = 1 - sstep(2.5, 5.5, edge)
            if hedge > 0 {
                let hw = worley(s.x, s.y, cell: 5, seed: t.seed &+ 61)
                let hc = RGB(0x2F5E2A).mix(RGB(0x5C8F3E), clamp01(1 - hw.f1 / 3.5))
                c = c.mix(hc, hedge)
            }
        }
        return c
    }

    /// Mesto: asfalt, chodníky, prerušované čiary. Budovy kreslí CityPainter vektorovo.
    private static func city(_ s: Sample) -> RGB {
        let bw = CityGrid.blockW, bh = CityGrid.blockH, half = CityGrid.street / 2
        let u = fmodf(s.x, bw), v = fmodf(s.y, bh)
        let hu = min(u, bw - u), hv = min(v, bh - v)
        let ds = min(hu, hv)
        if ds < half {
            if (hv < 0.55 && fmodf(s.x, 14) < 7 && hu > half) || (hu < 0.55 && fmodf(s.y, 14) < 7 && hv > half) {
                return RGB(0xE8E2C8)
            }
            return RGB(0x4A4E55).offset((s.micro - 0.5) * 0.05)
        }
        if ds < half + 3 { return RGB(0xDAD6CE) }
        return RGB(0xC9C4BA).offset((s.micro - 0.5) * 0.05)
    }

    // MARK: - Povrch pod dokumentmi

    @inline(__always)
    private static func relief(_ gx: Float, _ gy: Float, _ k: Float = 22) -> Float {
        let L = TerrainRenderer.light
        let nx = -gx * k, ny = -gy * k
        let nl = (nx * nx + ny * ny + 1).squareRoot()
        return (nx * L.x + ny * L.y + L.z) / nl - L.z
    }

    private static func sand(_ s: Sample) -> RGB {
        let inl = max(s.sdf, 0)
        let calm = 1 - 0.6 * s.calm
        var c = RGB(0xF2E7CF).mix(RGB(0xEADBBB), sstep(0.35, 0.65, s.detail) * calm).offset((s.micro - 0.5) * 0.035)
        let g = hash2(Int32((s.x * 1.7).rounded(.down)), Int32((s.y * 1.7).rounded(.down)), 5)
        if g > 0.985 { c = c.offset(-0.10) } else if g < 0.012 { c = c.offset(0.06) }
        return c.mix(RGB(0xD9C59B), clamp01(1 - inl / 7) * 0.55)          // mokrý piesok pri vode
    }

    private static func island(_ s: Sample) -> RGB {
        let inl = max(s.sdf, 0)
        let calm = 1 - 0.6 * s.calm
        var lc = RGB(0x7FB550).mix(RGB(0x5E9A3C), sstep(0.36, 0.64, s.detail) * calm).offset((s.micro - 0.5) * 0.07 * calm)
        let sandc = RGB(0xEDD5A1).mix(RGB(0xCDB079), clamp01(1 - inl / 3.5) * 0.55)
        lc = sandc.mix(lc, sstep(13, 18, inl))
        let sh = min(max(relief(s.gx, s.gy), -0.35), 0.35)
        if sh > 0 { return lc.scaled(1 + sh * 1.1) }
        return lc.mix(lc.mul(RGB(0x2F5D46)).scaled(1.6), -sh * 1.6)
    }

    private static let flowerColors = [RGB(0xFFFFFF), RGB(0xF7D046), RGB(0xE86A92)]

    private static func pasture(_ s: Sample) -> RGB {
        let inl = max(s.sdf, 0)
        let calm = 1 - 0.6 * s.calm
        var c = RGB(0xB9E07E).mix(RGB(0xA6D46C), sstep(0.35, 0.65, s.detail) * calm).offset((s.micro - 0.5) * 0.06 * calm)
        if s.calm < 0.3 {                                                  // kvietky mimo ikon
            let w = worley(s.x, s.y, cell: 9, seed: 404)
            if w.f1 < 1.1 && w.id > 0.55 { c = flowerColors[Int(w.id * 7) % 3] }
        }
        let fz = (inl - 4) / 0.55
        c = c.mix(RGB(0x8A6440), expf(-fz * fz) * 0.9)                    // žrď plota
        if fmodf(s.x + s.y, 9) < 1.4 && abs(inl - 4) < 1.3 { c = RGB(0x6B4A2E) }   // stĺpiky
        return c.mix(c.scaled(0.82), clamp01(1 - abs(inl - 2.2) / 1.2) * 0.6)   // tieň plota
    }

    private static func park(_ s: Sample) -> RGB {
        let inl = max(s.sdf, 0)
        let calm = 1 - 0.6 * s.calm
        var c = RGB(0x86C25C).mix(RGB(0x74B04E), sstep(0.35, 0.65, s.detail) * calm).offset((s.micro - 0.5) * 0.06 * calm)
        if fmodf(s.x, 24) < 12 { c = c.offset(0.035 * calm) }             // pruhy po kosení
        c = c.mix(RGB(0x3F7A33), clamp01(1 - abs(inl - 3) / 2.2))         // živý plot
        return c.mix(RGB(0xD8D2C4), clamp01(1 - inl / 1.2))               // obrubník
    }

    private static func snow(_ s: Sample) -> RGB {
        let calm = 1 - 0.6 * s.calm
        var c = RGB(0xF7FAFD).mix(RGB(0xE6EEF6), sstep(0.35, 0.65, s.detail) * calm).offset((s.micro - 0.5) * 0.03 * calm)
        let sh = min(max(relief(s.gx, s.gy) * 1.3, -0.35), 0.35)
        if sh > 0 { c = c.scaled(1 + sh * 0.5) }
        return c.mix(c.mul(RGB(0x8FA6C4)).scaled(1.25), max(-sh, 0) * 1.6)
    }

    /// Okraj oblaku: vzdialenosť od okraja oblasti + kopy (Worley) → kopovitý obrys.
    @inline(__always)
    private static func cloudShape(_ x: Float, _ y: Float, _ sdf: Float) -> Float {
        let w = worley(x, y, cell: 24, seed: 333, jitter: 0.7)
        let puff = (11 + w.id * 6) - w.f1
        return sdf + 0.9 * min(max(puff, -12), 20)
    }

    private static func cloudColor(_ s: Sample, _ cs: Float) -> RGB {
        let wl = worley(s.x + 3, s.y - 3, cell: 24, seed: 333, jitter: 0.7)
        let lit = clamp01(1 - wl.f1 / (11 + wl.id * 6))
        var c = RGB(0xC9D8EA).mix(RGB(0xF7FAFD), sstep(0, 0.7, lit * 0.8 + sstep(0, 30, cs) * 0.6))
        c = c.mix(.white, sstep(0.5, 1, lit) * 0.6 * (1 - sstep(10, 30, cs)))
        return c
    }
}
