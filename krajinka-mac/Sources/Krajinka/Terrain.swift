import CoreGraphics
import Foundation

/// Výsledok jedného prekreslenia jednej obrazovky.
struct RenderedScene {
    let terrain: CGImage        // nepriehľadný maľovaný podklad
    let waterMask: CGImage      // alfa = voda (svetlo na hladine, trblietky) – len pri mori
    let shoreFoamMask: CGImage  // alfa = pena a spŕška pri brehu
    let foamLinesMask: CGImage  // alfa = línie peny ďalej od brehu
}

/// Mriežka ulíc v téme Mesto (body).
enum CityGrid {
    static let blockW: Float = 150
    static let blockH: Float = 118
    static let street: Float = 14
}

/// Hrubá mriežka (6 bodov) s pomaly sa meniacimi kanálmi (šumy, vzdialenosť od
/// oblasti). Pixely ich len bilineárne čítajú – jemné ťahy a bodky sa počítajú
/// priamo v pixeli.
struct TerrainField {
    static let stride = 16
    static let sdf = 0, calm = 1, detail = 2, large = 3
    static let mot140 = 4, mot200 = 5, n40 = 6, n22 = 7, n55 = 8, n70 = 9, n30 = 10, n50 = 11
    static let warpX = 12, warpY = 13, mot60 = 14
    static let channels = 15

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
    /// 1 = priamo pod ikonou, 0 = ďalej od nej.
    func clear(_ p: CGPoint) -> Float { value(Self.calm, Float(p.x), Float(p.y)) }
    func detail(_ p: CGPoint) -> Float { value(Self.detail, Float(p.x), Float(p.y)) }
    func large(_ p: CGPoint) -> Float { value(Self.large, Float(p.x), Float(p.y)) }
}

enum TerrainRenderer {
    static let cell: Float = 6

    @inline(__always)
    private static func smin(_ a: Float, _ b: Float, _ k: Float) -> Float {
        let h = clamp01(0.5 + 0.5 * (b - a) / k)
        return b * (1 - h) + a * h - k * h * (1 - h)
    }

    // MARK: - Pole

    static func buildField(size: CGSize, icons: [CGRect], theme: Theme) -> TerrainField {
        let cols = Int((Float(size.width) / cell).rounded(.up)) + 2
        let rows = Int((Float(size.height) / cell).rounded(.up)) + 2
        let S = TerrainField.stride
        var data = [Float](repeating: 0, count: cols * rows * S)
        let s = theme.seed
        let n = (0..<16).map { ValueNoise(seed: s &+ UInt32(1000 + $0 * 37)) }
        let rects = icons.map { (Float($0.minX), Float($0.minY), Float($0.maxX), Float($0.maxY)) }

        // tvar oblasti každej ikony: zaoblený obdĺžnik, mierne posunutý a inak veľký
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
                var e: Float = 1e4
                var d1: Float = 1e4
                for (k, r) in rects.enumerated() {
                    let sh = shapes[k]
                    let dx = max(sh.x0 - px, 0, px - sh.x1)
                    let dy = max(sh.y0 - py, 0, py - sh.y1)
                    e = smin(e, max((dx * dx + dy * dy).squareRoot() - sh.cr, 0) - sh.r, theme.regionBlend)
                    let rx = max(r.0 - px, 0, px - r.2)
                    let ry = max(r.1 - py, 0, py - r.3)
                    d1 = min(d1, (rx * rx + ry * ry).squareRoot())
                }
                var sdf = -(e + (n[0].fbm(px / 60, py / 60, octaves: 3) - 0.5) * theme.regionWobble)
                if d1 < 2 { sdf = max(sdf, 6) }          // dokument nikdy nestojí mimo svojej oblasti

                // zvlnené súradnice → organické „maľované“ škvrny
                let wx = px + (n[1].fbm(px / 90, py / 90, octaves: 2) - 0.5) * 60
                let wy = py + (n[2].fbm(px / 90, py / 90, octaves: 2) - 0.5) * 60

                let k = (j * cols + i) * S
                data[k + TerrainField.sdf] = sdf
                data[k + TerrainField.calm] = 1 - sstep(0, 14, d1)
                data[k + TerrainField.detail] = n[3].fbm(px / 90, py / 90, octaves: 2)
                data[k + TerrainField.large] = n[4].fbm(px / 240, py / 240, octaves: 3)
                data[k + TerrainField.mot140] = n[5].fbm(wx / 140, wy / 140, octaves: 3)
                data[k + TerrainField.mot200] = n[6].fbm(wx / 200, wy / 200, octaves: 3)
                data[k + TerrainField.mot60] = n[7].fbm(wx / 60, wy / 60, octaves: 3)
                data[k + TerrainField.n40] = n[8].fbm(px / 40, py / 40, octaves: 2)
                data[k + TerrainField.n22] = n[9].fbm(px / 22, py / 22, octaves: 2)
                data[k + TerrainField.n55] = n[10].fbm(px / 55, py / 55, octaves: 2)
                data[k + TerrainField.n70] = n[11].fbm(px / 70, py / 70, octaves: 2)
                data[k + TerrainField.n30] = n[12].fbm(px / 30, py / 30, octaves: 2)
                data[k + TerrainField.n50] = n[13].fbm(px / 50, py / 50, octaves: 2)
                data[k + TerrainField.warpX] = (n[14].fbm(px / 160, py / 160, octaves: 2) - 0.5) * 46
                data[k + TerrainField.warpY] = (n[15].fbm(px / 160, py / 160, octaves: 2) - 0.5) * 46
            }
        }
        return TerrainField(cols: cols, rows: rows, cell: cell, data: data)
    }

    // MARK: - Render

    static func render(size: CGSize, scale: CGFloat, icons: [CGRect], theme: Theme) -> RenderedScene? {
        guard size.width > 0, size.height > 0 else { return nil }
        let field = buildField(size: size, icons: icons, theme: theme)
        guard let ctx = renderPixels(field: field, size: size, scale: scale, theme: theme) else { return nil }

        ctx.saveGState()
        ctx.scaleBy(x: scale, y: scale)
        if theme.id == .mesto { CityPainter.draw(in: ctx, size: size, field: field) }
        let paths = theme.showPaths ? PathPlanner.plan(field: field, icons: icons, overWater: theme.hasSea) : []
        Decorator.drawPaths(paths, in: ctx, theme: theme)
        Decorator.draw(in: ctx, size: size, field: field, icons: icons, paths: paths, theme: theme)
        ctx.restoreGState()
        guard let terrain = ctx.makeImage() else { return nil }

        if theme.hasSea, let m = renderSeaMasks(field: field, size: size, scale: scale, theme: theme) {
            return RenderedScene(terrain: terrain, waterMask: m.water, shoreFoamMask: m.shore, foamLinesMask: m.lines)
        }
        guard let empty = makeContext(1, 1)?.makeImage() else { return nil }
        return RenderedScene(terrain: terrain, waterMask: empty, shoreFoamMask: empty, foamLinesMask: empty)
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
        let painter = Painters(theme: t, field: field)

        field.data.withUnsafeBufferPointer { d in
            var ch = [Float](repeating: 0, count: TerrainField.channels)
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
                    for c in 0..<TerrainField.channels {
                        let p = d[b00 + c] + (d[b10 + c] - d[b00 + c]) * fx
                        let q = d[b01 + c] + (d[b11 + c] - d[b01 + c]) * fx
                        ch[c] = p + (q - p) * fy
                    }
                    var c = painter.pixel(x: x, y: y, ch)

                    let vx = (x / W - 0.5) * 2
                    let r = (vx * vx + vy * vy).squareRoot() / 1.2
                    c = c.scaled(1 - 0.08 * sstep(0.55, 1.15, r))       // jemná vinetácia

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

    /// Masky pre animácie mora: voda (polovičné rozlíšenie stačí), pena pri brehu
    /// a línie peny (plné rozlíšenie – sú jemné).
    private static func renderSeaMasks(field: TerrainField, size: CGSize, scale: CGFloat, theme t: Theme)
        -> (water: CGImage, shore: CGImage, lines: CGImage)? {
        func mask(_ scale: CGFloat, _ value: (Float, Float) -> Float) -> CGImage? {
            let width = max(1, Int((size.width * scale).rounded()))
            let height = max(1, Int((size.height * scale).rounded()))
            guard let ctx = makeContext(width, height), let b = ctx.data?.assumingMemoryBound(to: UInt8.self) else { return nil }
            let rowBytes = ctx.bytesPerRow
            let inv = 1 / Float(scale)
            for row in 0..<height {
                let y = (Float(height - 1 - row) + 0.5) * inv
                for col in 0..<width {
                    let x = (Float(col) + 0.5) * inv
                    let a = byte(value(x, y))
                    let o = row * rowBytes + col * 4
                    b[o] = a; b[o + 1] = a; b[o + 2] = a; b[o + 3] = a     // biela, premultiplikovaná
                }
            }
            return ctx.makeImage()
        }
        let painter = Painters(theme: t, field: field)
        guard let water = mask(max(0.5, scale * 0.5), { x, y in clamp01(-field.value(TerrainField.sdf, x, y) + 0.5) }),
              let shore = mask(scale, { x, y in painter.foam(x: x, y: y).shore }),
              let lines = mask(scale, { x, y in painter.foam(x: x, y: y).lines })
        else { return nil }
        return (water, shore, lines)
    }

    @inline(__always)
    static func byte(_ v: Float) -> UInt8 { UInt8(clamp01(v) * 255 + 0.5) }

    static func makeContext(_ w: Int, _ h: Int) -> CGContext? {
        guard let cs = CGColorSpace(name: CGColorSpace.sRGB) else { return nil }
        return CGContext(data: nil, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w * 4,
                         space: cs, bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)
    }
}
