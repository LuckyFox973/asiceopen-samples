import CoreGraphics
import Foundation

/// Výsledok jedného prekreslenia jednej obrazovky.
struct RenderedScene {
    let terrain: CGImage        // nepriehľadný podklad: okolie, oblasti dokumentov, budovy, flóra
    let waterMask: CGImage      // alfa = voda (iskry, záblesky) – len pri mori
    let foamMask: CGImage       // alfa = pena tesne pri brehu
    let waveMasks: [CGImage]    // prstence v rôznych hĺbkach → vlny bežiace k brehu
    let swimSpots: [CGPoint]    // miesta pre lodičky
    let carRoutes: [(CGPoint, CGPoint)]   // úseky ulíc pre autá (Mesto)
}

/// Konštanty vĺn – zdieľa ich render (masky) aj SceneView (časovanie).
enum Waves {
    static let depths: [Float] = [6, 12, 19, 27]          // body od brehu
    static let phases: [Double] = [0.6, 0.4, 0.2, 0.0]    // vonkajší prstenec ide prvý
    static let width: Float = 2.4
    static let foamPhase: Double = 0.78
}

/// Mriežka ulíc v téme Mesto (body).
enum CityGrid {
    static let blockW: Float = 150
    static let blockH: Float = 118
    static let street: Float = 14
}

/// Hrubá mriežka (6 bodov) s viacerými kanálmi, prekladaná v jednom poli.
struct TerrainField {
    static let stride = 8
    static let sdf = 0, calm = 1, detail = 2, micro = 3, large = 4, gradX = 5, gradY = 6

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

    /// Vzdialenosť od okraja oblasti dokumentov v bodoch: + vnútri, – mimo.
    func sdf(_ p: CGPoint) -> Float { value(Self.sdf, Float(p.x), Float(p.y)) }
    /// 1 = priamo pod ikonou (textúra sa tu tlmí), 0 = ďalej od nej.
    func clear(_ p: CGPoint) -> Float { value(Self.calm, Float(p.x), Float(p.y)) }
    func detail(_ p: CGPoint) -> Float { value(Self.detail, Float(p.x), Float(p.y)) }
    func large(_ p: CGPoint) -> Float { value(Self.large, Float(p.x), Float(p.y)) }
}

enum TerrainRenderer {
    static let cell: Float = 6
    /// Smer svetla (zhora zľava), normalizovaný.
    static let light: (x: Float, y: Float, z: Float) = {
        let v: (Float, Float, Float) = (-0.5, 0.6, 0.62)
        let l = (v.0 * v.0 + v.1 * v.1 + v.2 * v.2).squareRoot()
        return (v.0 / l, v.1 / l, v.2 / l)
    }()

    // MARK: - Pole oblastí

    /// Plynulé minimum – blízke oblasti sa zlejú, vzdialené ostanú samostatné.
    @inline(__always)
    private static func smin(_ a: Float, _ b: Float, _ k: Float) -> Float {
        let h = clamp01(0.5 + 0.5 * (b - a) / k)
        return b * (1 - h) + a * h - k * h * (1 - h)
    }

    static func buildField(size: CGSize, icons: [CGRect], theme: Theme) -> TerrainField {
        let cols = Int((Float(size.width) / cell).rounded(.up)) + 2
        let rows = Int((Float(size.height) / cell).rounded(.up)) + 2
        let S = TerrainField.stride
        var data = [Float](repeating: 0, count: cols * rows * S)
        let s = theme.seed
        let wobbleNoise = ValueNoise(seed: s &+ 555)
        let detailNoise = ValueNoise(seed: s &+ 977)
        let microNoise = ValueNoise(seed: s &+ 313)
        let largeNoise = ValueNoise(seed: s &+ 71)
        let rects = icons.map { (Float($0.minX), Float($0.minY), Float($0.maxX), Float($0.maxY)) }

        // každá ikona: zaoblený obdĺžnik okolo rámika, mierne posunutý a inak veľký
        let rr = theme.regionRadius
        let shapes: [(x0: Float, y0: Float, x1: Float, y1: Float, cr: Float, r: Float)] =
            rects.enumerated().map { k, r in
                let kk = Int32(k)
                let ox = (hash2(kk, 1, 901) - 0.5) * 16, oy = (hash2(kk, 2, 901) - 0.5) * 16
                let cr = min(32, (r.2 - r.0) / 2, (r.3 - r.1) / 2)
                let radius = rr.min + (rr.max - rr.min) * hash2(kk, 3, 901)
                return (r.0 + cr + ox, r.1 + cr + oy, r.2 - cr + ox, r.3 - cr + oy, cr, radius)
            }

        for j in 0..<rows {
            let py = Float(j) * cell
            for i in 0..<cols {
                let px = Float(i) * cell
                var e: Float = 1e4          // vzdialenosť od okraja oblasti (záporná = vnútri)
                var d1: Float = 1e4         // vzdialenosť od najbližšieho rámika ikony
                for (k, r) in rects.enumerated() {
                    let sh = shapes[k]
                    let dx = max(sh.x0 - px, 0, px - sh.x1)
                    let dy = max(sh.y0 - py, 0, py - sh.y1)
                    let ek = max((dx * dx + dy * dy).squareRoot() - sh.cr, 0) - sh.r
                    e = smin(e, ek, theme.regionBlend)
                    let rx = max(r.0 - px, 0, px - r.2)
                    let ry = max(r.1 - py, 0, py - r.3)
                    d1 = min(d1, (rx * rx + ry * ry).squareRoot())
                }
                var sdf = -(e + (wobbleNoise.fbm(px / 60, py / 60, octaves: 3) - 0.5) * theme.regionWobble)
                if d1 < 2 { sdf = max(sdf, 6) }       // dokument nikdy nestojí mimo svojej oblasti

                let k = (j * cols + i) * S
                data[k + TerrainField.sdf] = sdf
                data[k + TerrainField.calm] = 1 - sstep(0, 14, d1)
                data[k + TerrainField.detail] = detailNoise.fbm(px / 90, py / 90, octaves: 2)
                data[k + TerrainField.micro] = microNoise.fbm(px / 16, py / 16, octaves: 2)
                data[k + TerrainField.large] = largeNoise.fbm(px / 240, py / 240, octaves: 3)
            }
        }
        // gradient „kupoly“ oblasti (pre tieňovanie reliéfu)
        @inline(__always) func hgt(_ k: Int) -> Float { min(max(data[k * S + TerrainField.sdf], 0), 40) * 0.012 }
        for j in 1..<(rows - 1) {
            for i in 1..<(cols - 1) {
                let k = j * cols + i
                data[k * S + TerrainField.gradX] = (hgt(k + 1) - hgt(k - 1)) / (2 * cell)
                data[k * S + TerrainField.gradY] = (hgt(k + cols) - hgt(k - cols)) / (2 * cell)
            }
        }
        return TerrainField(cols: cols, rows: rows, cell: cell, data: data)
    }

    // MARK: - Render

    static func render(size: CGSize, scale: CGFloat, icons: [CGRect], theme: Theme) -> RenderedScene? {
        guard size.width > 0, size.height > 0 else { return nil }
        let field = buildField(size: size, icons: icons, theme: theme)
        guard let ctx = renderPixels(field: field, size: size, scale: scale, theme: theme) else { return nil }

        // Vektorová vrstva v bodoch: budovy → chodníky → flóra a drobnosti
        ctx.saveGState()
        ctx.scaleBy(x: scale, y: scale)
        if theme.backdrop == .city {
            CityPainter.draw(in: ctx, size: size, field: field)
        }
        let paths = theme.showPaths ? PathPlanner.plan(field: field, icons: icons, overWater: theme.hasSea) : []
        Decorator.drawPaths(paths, in: ctx, theme: theme)
        Decorator.draw(in: ctx, size: size, field: field, icons: icons, paths: paths, theme: theme)
        ctx.restoreGState()
        guard let terrain = ctx.makeImage() else { return nil }

        var water: CGImage, foam: CGImage, waves: [CGImage]
        if theme.hasSea {
            guard let m = renderMasks(field: field, size: size, scale: max(0.5, scale * 0.5)) else { return nil }
            (water, foam, waves) = (m.water, m.foam, m.waves)
        } else {
            guard let empty = makeContext(1, 1)?.makeImage() else { return nil }
            (water, foam, waves) = (empty, empty, Array(repeating: empty, count: Waves.depths.count))
        }
        return RenderedScene(terrain: terrain, waterMask: water, foamMask: foam, waveMasks: waves,
                             swimSpots: theme.swimmers == .none ? [] : findSwimSpots(field: field, size: size, icons: icons),
                             carRoutes: theme.cars ? findCarRoutes(field: field, size: size) : [])
    }

    private static func renderPixels(field: TerrainField, size: CGSize, scale: CGFloat, theme t: Theme) -> CGContext? {
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

        var colIndex = [Int](repeating: 0, count: width)
        var colFrac = [Float](repeating: 0, count: width)
        for c in 0..<width {
            let gx = min(max((Float(c) + 0.5) * inv / cell, 0), Float(cols - 1) - 0.001)
            colIndex[c] = Int(gx)
            colFrac[c] = gx - Float(Int(gx))
        }

        field.data.withUnsafeBufferPointer { d in
            for row in 0..<height {
                let y = (Float(height - 1 - row) + 0.5) * inv      // riadok 0 v pamäti = horný okraj
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
                    var s = Sample(x: x, y: y, H: H,
                                   sdf: ch(TerrainField.sdf), calm: ch(TerrainField.calm),
                                   detail: ch(TerrainField.detail), micro: ch(TerrainField.micro),
                                   large: ch(TerrainField.large),
                                   gx: ch(TerrainField.gradX), gy: ch(TerrainField.gradY))

                    var c = Painters.compose(&s, theme: t, field: field)

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

    /// Lodičky: obrazovka sa rozdelí na rovnako široké pásy a v každom sa vyberie
    /// najotvorenejšia voda. Okraje (Dock, menu bar) sa vynechávajú.
    private static func findSwimSpots(field: TerrainField, size: CGSize, icons: [CGRect]) -> [CGPoint] {
        let radius: CGFloat = 46
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

    /// Úseky ulíc mimo parkov (dosť dlhé na to, aby nimi prešlo auto).
    private static func findCarRoutes(field: TerrainField, size: CGSize) -> [(CGPoint, CGPoint)] {
        var routes: [(CGPoint, CGPoint)] = []
        let bw = CGFloat(CityGrid.blockW), bh = CGFloat(CityGrid.blockH)
        let step: CGFloat = 20
        func scan(_ points: [CGPoint]) {
            var start: CGPoint?
            var last: CGPoint?
            for p in points {
                if field.sdf(p) < -12 {
                    if start == nil { start = p }
                    last = p
                } else {
                    if let s = start, let l = last, hypot(l.x - s.x, l.y - s.y) >= 240 { routes.append((s, l)) }
                    start = nil
                }
            }
            if let s = start, let l = last, hypot(l.x - s.x, l.y - s.y) >= 240 { routes.append((s, l)) }
        }
        var y = bh
        while y < size.height - 40 {
            scan(stride(from: 0, through: size.width, by: step).map { CGPoint(x: $0, y: y) })
            y += bh
        }
        var x = bw
        while x < size.width {
            scan(stride(from: 0, through: size.height, by: step).map { CGPoint(x: x, y: $0) })
            x += bw
        }
        return routes
    }

    @inline(__always)
    static func byte(_ v: Float) -> UInt8 { UInt8(clamp01(v) * 255 + 0.5) }

    static func makeContext(_ w: Int, _ h: Int) -> CGContext? {
        guard let cs = CGColorSpace(name: CGColorSpace.sRGB) else { return nil }
        return CGContext(data: nil, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w * 4,
                         space: cs, bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)
    }
}
