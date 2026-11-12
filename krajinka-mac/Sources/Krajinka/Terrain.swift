import CoreGraphics
import Foundation

/// Výsledok jedného prekreslenia jednej obrazovky.
struct RenderedScene {
    let terrain: CGImage        // nepriehľadný podklad vrátane chodníkov a dekorácií
    let waterMask: CGImage      // alfa = voda (iskry, záblesky)
    let foamMask: CGImage       // alfa = pena tesne pri brehu
    let waveMasks: [CGImage]    // prstence v rôznych hĺbkach → vlny bežiace k brehu
    let swimSpots: [CGPoint]    // miesta s dostatkom vody pre lodičky/kačky
}

/// Konštanty vĺn – zdieľa ich render (masky) aj SceneView (časovanie).
enum Waves {
    static let depths: [Float] = [6, 12, 19, 27]          // body od brehu
    static let phases: [Double] = [0.6, 0.4, 0.2, 0.0]    // vonkajší prstenec ide prvý
    static let width: Float = 2.4
    static let foamPhase: Double = 0.78
}

/// Hrubá mriežka (6 bodov) s viacerými kanálmi, prekladaná v jednom poli
/// kvôli rýchlemu bilineárnemu vzorkovaniu.
struct TerrainField {
    static let stride = 8
    static let height = 0, detail = 1, micro = 2, gradX = 3, gradY = 4, sdf = 5, clear = 6

    let cols: Int
    let rows: Int
    let cell: Float
    var data: [Float]

    @inline(__always)
    func value(_ ch: Int, _ x: Float, _ y: Float) -> Float {
        let gx = min(max(x / cell, 0), Float(cols - 1) - 0.001)
        let gy = min(max(y / cell, 0), Float(rows - 1) - 0.001)
        let ix = Int(gx), iy = Int(gy)
        let fx = gx - Float(ix), fy = gy - Float(iy)
        let s = TerrainField.stride
        let b00 = (iy * cols + ix) * s + ch
        let b10 = b00 + s
        let b01 = b00 + cols * s
        let b11 = b01 + s
        let t = data[b00] + (data[b10] - data[b00]) * fx
        let u = data[b01] + (data[b11] - data[b01]) * fx
        return t + (u - t) * fy
    }

    func height(_ p: CGPoint) -> Float { value(Self.height, Float(p.x), Float(p.y)) }
    /// Vzdialenosť od pobrežia v bodoch: + pevnina, – voda.
    func sdf(_ p: CGPoint) -> Float { value(Self.sdf, Float(p.x), Float(p.y)) }
    /// 0 = mimo čistiny, 1 = čistinka pod ikonou.
    func clear(_ p: CGPoint) -> Float { value(Self.clear, Float(p.x), Float(p.y)) }
    func detail(_ p: CGPoint) -> Float { value(Self.detail, Float(p.x), Float(p.y)) }
}

enum TerrainRenderer {
    static let cell: Float = 6
    /// Smer svetla (zhora zľava), normalizovaný.
    static let light: (x: Float, y: Float, z: Float) = {
        let v: (Float, Float, Float) = (-0.5, 0.6, 0.62)
        let l = (v.0 * v.0 + v.1 * v.1 + v.2 * v.2).squareRoot()
        return (v.0 / l, v.1 / l, v.2 / l)
    }()

    // MARK: - Pole

    static func buildField(size: CGSize, icons: [CGRect], theme: Theme) -> TerrainField {
        let cols = Int((Float(size.width) / cell).rounded(.up)) + 2
        let rows = Int((Float(size.height) / cell).rounded(.up)) + 2
        let S = TerrainField.stride
        var data = [Float](repeating: 0, count: cols * rows * S)

        let noise = ValueNoise(seed: theme.seed)
        let detailNoise = ValueNoise(seed: theme.seed &+ 977)
        let microNoise = ValueNoise(seed: theme.seed &+ 313)
        let clearNoise = ValueNoise(seed: theme.seed &+ 555)
        let rects = icons.map { (Float($0.minX), Float($0.minY), Float($0.maxX), Float($0.maxY)) }
        let hasIcons = !rects.isEmpty
        // Každý dokument má vlastný ostrov: zaoblený obdĺžnik okolo rámika, mierne
        // posunutý, inak veľký a so zvlneným pobrežím.
        let islands: [(x0: Float, y0: Float, x1: Float, y1: Float, cr: Float, r: Float)] =
            rects.enumerated().map { k, r in
                let kk = Int32(k)
                let ox = (hash2(kk, 1, 901) - 0.5) * 16, oy = (hash2(kk, 2, 901) - 0.5) * 16
                let cr = min(32, (r.2 - r.0) / 2, (r.3 - r.1) / 2)
                return (r.0 + cr + ox, r.1 + cr + oy, r.2 - cr + ox, r.3 - cr + oy, cr, 30 + 16 * hash2(kk, 3, 901))
            }

        for j in 0..<rows {
            let py = Float(j) * cell
            for i in 0..<cols {
                let px = Float(i) * cell
                var f: Float
                if theme.landOnlyAtIcons {
                    // všade pod hladinou; zem vytvárajú len dokumenty
                    f = 0.16 + (noise.fbm(px / 140, py / 140, octaves: 3) - 0.5) * 0.34
                } else {
                    f = theme.waterLevel + (noise.fbm(px / 280, py / 280, octaves: 4) - 0.5) * 0.95
                }

                var clear: Float = 0
                if hasIcons {
                    let wobble = (clearNoise.fbm(px / 55, py / 55, octaves: 3) - 0.5) * 30
                    var e1 = Float.greatestFiniteMagnitude          // vzdialenosť od pobrežia najbližšieho ostrova
                    var d1 = Float.greatestFiniteMagnitude          // vzdialenosť od najbližšieho rámika
                    var d2 = Float.greatestFiniteMagnitude          // …a od druhého najbližšieho
                    for (k, r) in rects.enumerated() {
                        let dx = max(r.0 - px, 0, px - r.2)
                        let dy = max(r.1 - py, 0, py - r.3)
                        let d = (dx * dx + dy * dy).squareRoot()
                        if d < d1 { d2 = d1; d1 = d } else if d < d2 { d2 = d }

                        let isl = islands[k]
                        let ix = max(isl.x0 - px, 0, px - isl.x1)
                        let iy = max(isl.y0 - py, 0, py - isl.y1)
                        let rounded = max((ix * ix + iy * iy).squareRoot() - isl.cr, 0)
                        e1 = min(e1, rounded - (isl.r + wobble))
                    }
                    var island = Band.shore - e1 * 0.012                  // kupola, najvyššie pod ikonou
                    if d2 < .greatestFiniteMagnitude {
                        // prieliv pozdĺž osi medzi dvoma susednými dokumentmi
                        island -= 0.6 * smooth(clamp01(1 - (d2 - d1) / 28))
                    }
                    if d1 < 2 { island = max(island, Band.shore + 0.05) }  // dokument nikdy nestojí vo vode
                    f = max(f, island)
                    clear = 1 - sstep(0, 14, d1)
                }

                let k = (j * cols + i) * S
                data[k + TerrainField.height] = f
                data[k + TerrainField.detail] = detailNoise.fbm(px / 90, py / 90, octaves: 2)
                data[k + TerrainField.micro] = microNoise.fbm(px / 16, py / 16, octaves: 2)
                data[k + TerrainField.clear] = clear
            }
        }

        // gradient výšky (na bod) – pre tieňovanie reliéfu a pre SDF
        for j in 0..<rows {
            for i in 0..<cols {
                let k = (j * cols + i) * S
                if i > 0 && i < cols - 1 {
                    data[k + TerrainField.gradX] = (data[k + S] - data[k - S]) / (2 * cell)
                }
                if j > 0 && j < rows - 1 {
                    data[k + TerrainField.gradY] = (data[k + cols * S] - data[k - cols * S]) / (2 * cell)
                }
            }
        }

        computeSignedDistance(&data, cols: cols, rows: rows)
        return TerrainField(cols: cols, rows: rows, cell: cell, data: data)
    }

    /// Vzdialenosť od pobrežia v bodoch: subpixelové semienka z gradientu
    /// a dvojpriechodový chamfer (8-okolie). Výsledok: rovnako široké pláže,
    /// plytčiny a vlny okolo každého ostrova.
    private static func computeSignedDistance(_ data: inout [Float], cols: Int, rows: Int) {
        let S = TerrainField.stride
        let n = cols * rows
        let inf: Float = 1e9
        var dist = [Float](repeating: inf, count: n)
        var positive = [Bool](repeating: false, count: n)
        for k in 0..<n { positive[k] = data[k * S] >= Band.shore }

        for j in 0..<rows {
            for i in 0..<cols {
                let k = j * cols + i
                let p = positive[k]
                let edge = (i > 0 && positive[k - 1] != p) || (i < cols - 1 && positive[k + 1] != p)
                    || (j > 0 && positive[k - cols] != p) || (j < rows - 1 && positive[k + cols] != p)
                if edge {
                    let s = data[k * S] - Band.shore
                    let gx = data[k * S + TerrainField.gradX], gy = data[k * S + TerrainField.gradY]
                    let g = max((gx * gx + gy * gy).squareRoot(), 1e-4)
                    dist[k] = min(abs(s) / g, cell * 1.5)
                }
            }
        }

        let a = cell, b = cell * 2.0.squareRoot().asFloat
        for j in 0..<rows {
            for i in 0..<cols {
                let k = j * cols + i
                var d = dist[k]
                if i > 0 { d = min(d, dist[k - 1] + a) }
                if j > 0 {
                    d = min(d, dist[k - cols] + a)
                    if i > 0 { d = min(d, dist[k - cols - 1] + b) }
                    if i < cols - 1 { d = min(d, dist[k - cols + 1] + b) }
                }
                dist[k] = d
            }
        }
        for j in stride(from: rows - 1, through: 0, by: -1) {
            for i in stride(from: cols - 1, through: 0, by: -1) {
                let k = j * cols + i
                var d = dist[k]
                if i < cols - 1 { d = min(d, dist[k + 1] + a) }
                if j < rows - 1 {
                    d = min(d, dist[k + cols] + a)
                    if i < cols - 1 { d = min(d, dist[k + cols + 1] + b) }
                    if i > 0 { d = min(d, dist[k + cols - 1] + b) }
                }
                dist[k] = d
            }
        }
        for k in 0..<n {
            data[k * S + TerrainField.sdf] = positive[k] ? dist[k] : -dist[k]
        }
    }

    // MARK: - Render

    static func render(size: CGSize, scale: CGFloat, icons: [CGRect], theme: Theme) -> RenderedScene? {
        guard size.width > 0, size.height > 0 else { return nil }
        let field = buildField(size: size, icons: icons, theme: theme)

        guard let terrainCtx = renderTerrain(field: field, size: size, scale: scale, theme: theme) else { return nil }

        // Chodníky, mosty a flóra – vektorovo v bodoch
        terrainCtx.saveGState()
        terrainCtx.scaleBy(x: scale, y: scale)
        let paths = theme.showPaths ? PathPlanner.plan(field: field, icons: icons) : []
        Decorator.drawPaths(paths, in: terrainCtx, theme: theme)
        Decorator.draw(in: terrainCtx, size: size, field: field, icons: icons, paths: paths, theme: theme)
        terrainCtx.restoreGState()

        guard let terrain = terrainCtx.makeImage() else { return nil }

        // Masky stačia v polovičnom rozlíšení (sú mäkké), šetrí to pamäť
        let maskScale = max(0.5, scale * 0.5)
        guard let masks = renderMasks(field: field, size: size, scale: maskScale) else { return nil }

        return RenderedScene(terrain: terrain, waterMask: masks.water, foamMask: masks.foam,
                             waveMasks: masks.waves,
                             swimSpots: findSwimSpots(field: field, size: size, icons: icons, theme: theme))
    }

    private static func renderTerrain(field: TerrainField, size: CGSize, scale: CGFloat, theme t: Theme) -> CGContext? {
        let width = max(1, Int((size.width * scale).rounded()))
        let height = max(1, Int((size.height * scale).rounded()))
        guard let ctx = makeContext(width, height),
              let px = ctx.data?.assumingMemoryBound(to: UInt8.self)
        else { return nil }

        let rowBytes = ctx.bytesPerRow
        let inv = 1 / Float(scale)
        let W = Float(size.width), H = Float(size.height)
        let cols = field.cols, rows = field.rows, cell = field.cell
        let S = TerrainField.stride
        let L = light
        let bw = t.beachWidth

        // predpočítané stĺpce
        var colIndex = [Int](repeating: 0, count: width)
        var colFrac = [Float](repeating: 0, count: width)
        for c in 0..<width {
            let gx = min(max((Float(c) + 0.5) * inv / cell, 0), Float(cols - 1) - 0.001)
            colIndex[c] = Int(gx)
            colFrac[c] = gx - Float(Int(gx))
        }

        field.data.withUnsafeBufferPointer { d in
            for row in 0..<height {
                // riadok 0 v pamäti = horný okraj obrázka
                let y = (Float(height - 1 - row) + 0.5) * inv
                let gy = min(max(y / cell, 0), Float(rows - 1) - 0.001)
                let iy = Int(gy), fy = gy - Float(iy)
                let vy = (y / H - 0.5) * 2
                let base = row * rowBytes

                for col in 0..<width {
                    let x = (Float(col) + 0.5) * inv
                    let ix = colIndex[col], fx = colFrac[col]
                    let b00 = (iy * cols + ix) * S
                    let b10 = b00 + S
                    let b01 = b00 + cols * S
                    let b11 = b01 + S
                    @inline(__always) func ch(_ c: Int) -> Float {
                        let p = d[b00 + c] + (d[b10 + c] - d[b00 + c]) * fx
                        let q = d[b01 + c] + (d[b11 + c] - d[b01 + c]) * fx
                        return p + (q - p) * fy
                    }
                    let sdf = ch(TerrainField.sdf)
                    let det = ch(TerrainField.detail)
                    let m = ch(TerrainField.micro)

                    // --- voda
                    let depth = max(-sdf, 0)
                    let t1 = 1 - expf(-depth / 45)
                    let t2 = 1 - expf(-depth / 230)
                    var wc = t.shallow.mix(t.deep, t1).mix(t.deepest, t2 * 0.85)
                    wc = wc.offset((m - 0.5) * 0.06 * (1 - t1))
                    wc = wc.offset((det - 0.5) * 0.05 * t1)          // jemné žíhanie hlbokej vody
                    let sd = (depth - 24) / 3.2
                    wc = wc.mix(t.shelf, expf(-sd * sd) * 0.22)
                    wc = wc.mix(t.lip, clamp01(1 - depth / 2.6) * t.lipAlpha)
                    wc = wc.mix(t.shelf, (y / H) * 0.07)

                    // --- pevnina
                    var c = wc
                    let landA = clamp01(sdf / 0.6 + 0.5)
                    if landA > 0 {
                        let inland = max(sdf, 0)
                        // pod ikonou sa textúra utlmí, farba ostáva – žiadne „vyrezané“ plochy
                        let calm = 1 - 0.6 * ch(TerrainField.clear)
                        var lc = t.land.mix(t.landAlt, sstep(0.36, 0.64, det) * calm).offset((m - 0.5) * t.microAmount * calm)
                        let sandc = t.sand.mix(t.wetSand, clamp01(1 - inland / 3.5) * 0.55).offset((m - 0.5) * 0.03)
                        lc = sandc.mix(lc, sstep(bw - 2, bw + 3, inland))


                        // tieňovanie reliéfu
                        let nx = -ch(TerrainField.gradX) * 22, ny = -ch(TerrainField.gradY) * 22
                        let nl = (nx * nx + ny * ny + 1).squareRoot()
                        var sh = (nx * L.x + ny * L.y + L.z) / nl - L.z
                        sh = min(max(sh * t.shadeStrength, -0.35), 0.35)
                        if sh > 0 {
                            lc = lc.scaled(1 + sh * 1.1)
                        } else {
                            lc = lc.mix(lc.mul(t.shadowTint).scaled(1.6), -sh * 1.6)
                        }
                        c = wc.mix(lc, landA)
                    }

                    // vinetácia podľa pomeru strán + zrno
                    let vx = (x / W - 0.5) * 2
                    let r = (vx * vx + vy * vy).squareRoot() / 1.2
                    c = c.scaled(1 - 0.10 * sstep(0.55, 1.15, r))
                    c = c.offset((hash2(Int32(col), Int32(row), 7) - 0.5) * 0.018)

                    let o = base + col * 4
                    px[o] = byte(c.r)
                    px[o + 1] = byte(c.g)
                    px[o + 2] = byte(c.b)
                    px[o + 3] = 255
                }
            }
        }
        return ctx
    }

    private static func renderMasks(field: TerrainField, size: CGSize, scale: CGFloat)
        -> (water: CGImage, foam: CGImage, waves: [CGImage])? {
        let width = max(1, Int((size.width * scale).rounded()))
        let height = max(1, Int((size.height * scale).rounded()))
        let count = 2 + Waves.depths.count
        var contexts: [CGContext] = []
        var buffers: [UnsafeMutablePointer<UInt8>] = []
        for _ in 0..<count {
            guard let c = makeContext(width, height), let p = c.data?.assumingMemoryBound(to: UInt8.self) else { return nil }
            contexts.append(c)
            buffers.append(p)
        }
        let rowBytes = contexts[0].bytesPerRow
        let inv = 1 / Float(scale)

        for row in 0..<height {
            let y = (Float(height - 1 - row) + 0.5) * inv
            for col in 0..<width {
                let x = (Float(col) + 0.5) * inv
                let sdf = field.value(TerrainField.sdf, x, y)
                let o = row * rowBytes + col * 4
                let water = clamp01(-sdf / 0.6 + 0.5)
                guard water > 0 else {
                    for b in buffers { b[o] = 0; b[o + 1] = 0; b[o + 2] = 0; b[o + 3] = 0 }
                    continue
                }
                let depth = max(-sdf, 0)
                // biela, premultiplikovaná → všetky štyri zložky rovnaké
                @inline(__always) func put(_ i: Int, _ v: Float) {
                    let a = byte(v)
                    let b = buffers[i]
                    b[o] = a; b[o + 1] = a; b[o + 2] = a; b[o + 3] = a
                }
                put(0, water)
                put(1, clamp01(1 - depth / 5) * water)
                for k in 0..<Waves.depths.count {
                    let z = (depth - Waves.depths[k]) / Waves.width
                    put(2 + k, expf(-z * z) * water * (1 - Float(k) * 0.18))
                }
            }
        }
        let images = contexts.compactMap { $0.makeImage() }
        guard images.count == count else { return nil }
        return (images[0], images[1], Array(images[2...]))
    }

    /// Miesta pre lodičky (Pláž) a kačky (Lúka): obrazovka sa rozdelí na rovnako
    /// široké pásy a v každom sa vyberie najotvorenejšia voda. Okraje (Dock dole,
    /// menu bar hore, Dock po bokoch) sa vynechávajú.
    private static func findSwimSpots(field: TerrainField, size: CGSize, icons: [CGRect], theme: Theme) -> [CGPoint] {
        let radius: CGFloat
        switch theme.swimmers {
        case .none: return []
        case .sailboats: radius = 46
        case .ducks: radius = 30
        }
        let count = max(1, Int(size.width / 1500))
        let minX: CGFloat = 90, maxX = size.width - 90
        let minY: CGFloat = 160, maxY = size.height - 80
        guard maxX > minX, maxY > minY else { return [] }
        let band = (maxX - minX) / CGFloat(count)
        let step: CGFloat = 24
        var spots: [CGPoint] = []

        for c in 0..<count {
            var best: CGPoint?
            var bestScore: Float = 0
            var y = minY + radius
            while y <= maxY - radius {
                var x = minX + band * CGFloat(c) + radius
                while x <= minX + band * CGFloat(c + 1) - radius {
                    let p = CGPoint(x: x, y: y)
                    x += step
                    let center = field.sdf(p)
                    guard center < -(Float(radius) + 16) else { continue }
                    if icons.contains(where: { $0.insetBy(dx: -150, dy: -150).contains(p) }) { continue }
                    // najmenšia vzdialenosť od brehu na celej dráhe = skóre
                    var worst = -center
                    for k in 0..<16 {
                        let a = CGFloat(k) / 16 * 2 * .pi
                        let q = CGPoint(x: p.x + cos(a) * radius, y: p.y + sin(a) * radius * 0.65)
                        worst = min(worst, -field.sdf(q))
                    }
                    if worst > 14 && worst > bestScore {
                        bestScore = worst
                        best = p
                    }
                }
                y += step
            }
            if let b = best { spots.append(b) }
        }
        return spots
    }

    @inline(__always)
    static func byte(_ v: Float) -> UInt8 { UInt8(clamp01(v) * 255 + 0.5) }

    static func makeContext(_ w: Int, _ h: Int) -> CGContext? {
        guard let cs = CGColorSpace(name: CGColorSpace.sRGB) else { return nil }
        return CGContext(data: nil, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w * 4,
                         space: cs, bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)
    }
}

private extension Double {
    var asFloat: Float { Float(self) }
}
