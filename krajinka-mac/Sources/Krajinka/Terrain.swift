import CoreGraphics
import Foundation

/// Výsledok jedného prekreslenia jednej obrazovky.
struct RenderedScene {
    let terrain: CGImage     // nepriehľadný podklad vrátane dekorácií
    let waterMask: CGImage   // alfa = kde je voda (pre iskry)
    let foamMask: CGImage    // alfa = pás peny pri brehu
}

/// Výškové pole v hrubej mriežke (v bodoch). Pixely sa z neho počítajú bilineárne,
/// takže hrany pobrežia sú hladké pri ľubovoľnom rozlíšení.
struct TerrainField {
    let cols: Int
    let rows: Int
    let cell: Float
    var height: [Float]
    var detail: [Float]

    @inline(__always)
    func sample(_ x: Float, _ y: Float) -> (height: Float, detail: Float) {
        let gx = min(max(x / cell, 0), Float(cols - 1) - 0.001)
        let gy = min(max(y / cell, 0), Float(rows - 1) - 0.001)
        let ix = Int(gx), iy = Int(gy)
        let fx = gx - Float(ix), fy = gy - Float(iy)
        let i00 = iy * cols + ix
        let i10 = i00 + 1
        let i01 = i00 + cols
        let i11 = i01 + 1

        let h0 = height[i00] + (height[i10] - height[i00]) * fx
        let h1 = height[i01] + (height[i11] - height[i01]) * fx
        let d0 = detail[i00] + (detail[i10] - detail[i00]) * fx
        let d1 = detail[i01] + (detail[i11] - detail[i01]) * fx
        return (h0 + (h1 - h0) * fy, d0 + (d1 - d0) * fy)
    }
}

enum TerrainRenderer {
    static let cell: Float = 6
    /// Do akej vzdialenosti (body) ikona „dvíha“ pevninu.
    static let iconReach: Float = 120

    /// - Parameters:
    ///   - size: veľkosť obrazovky v bodoch
    ///   - icons: rámčeky ikon v lokálnych súradniciach obrazovky (počiatok vľavo dole)
    static func buildField(size: CGSize, icons: [CGRect], theme: Theme) -> TerrainField {
        let cols = Int((Float(size.width) / cell).rounded(.up)) + 2
        let rows = Int((Float(size.height) / cell).rounded(.up)) + 2
        var height = [Float](repeating: 0, count: cols * rows)
        var detail = [Float](repeating: 0, count: cols * rows)

        let noise = ValueNoise(seed: theme.seed)
        let detailNoise = ValueNoise(seed: theme.seed &+ 977)
        let rects = icons.map { (Float($0.minX), Float($0.minY), Float($0.maxX), Float($0.maxY)) }
        let reach = iconReach

        for j in 0..<rows {
            let py = Float(j) * cell
            for i in 0..<cols {
                let px = Float(i) * cell
                let n = noise.fbm(px / 280, py / 280, octaves: 4)
                var f = theme.waterLevel + (n - 0.5) * 0.95

                var influence: Float = 0
                var minD = Float.greatestFiniteMagnitude
                for r in rects {
                    let dx = max(r.0 - px, 0, px - r.2)
                    let dy = max(r.1 - py, 0, py - r.3)
                    let dist = (dx * dx + dy * dy).squareRoot()
                    if dist < minD { minD = dist }
                    if dist < reach {
                        let t = 1 - dist / reach
                        influence += smooth(t)
                    }
                }
                f += min(influence, 1.3) * 0.62

                // Garancia: každá ikona stojí na pokojnej čistinke a tá na ostrove,
                // bez ohľadu na to, čo vygeneroval šum.
                if minD < .greatestFiniteMagnitude {
                    let boost: Float = 0.86 - max(0, minD - 8) / 70
                    if boost > f { f = boost }
                }

                let idx = j * cols + i
                height[idx] = f
                detail[idx] = detailNoise.fbm(px / 90, py / 90, octaves: 2)
            }
        }
        return TerrainField(cols: cols, rows: rows, cell: cell, height: height, detail: detail)
    }

    static func render(size: CGSize, scale: CGFloat, icons: [CGRect], theme: Theme) -> RenderedScene? {
        guard size.width > 0, size.height > 0 else { return nil }
        let field = buildField(size: size, icons: icons, theme: theme)

        let width = max(1, Int((size.width * scale).rounded()))
        let height = max(1, Int((size.height * scale).rounded()))
        guard let cs = CGColorSpace(name: CGColorSpace.sRGB),
              let terrainCtx = makeContext(width, height, cs),
              let waterCtx = makeContext(width, height, cs),
              let foamCtx = makeContext(width, height, cs),
              let tp = terrainCtx.data?.assumingMemoryBound(to: UInt8.self),
              let wp = waterCtx.data?.assumingMemoryBound(to: UInt8.self),
              let fp = foamCtx.data?.assumingMemoryBound(to: UInt8.self)
        else { return nil }

        let tRow = terrainCtx.bytesPerRow
        let wRow = waterCtx.bytesPerRow
        let fRow = foamCtx.bytesPerRow
        let inv = 1 / Float(scale)
        let outlineWidth: Float = 0.006
        let foamWidth: Float = 0.05

        for row in 0..<height {
            // Riadok 0 v pamäti bitmapy = horný okraj obrázka.
            let y = (Float(height - 1 - row) + 0.5) * inv
            let tBase = row * tRow, wBase = row * wRow, fBase = row * fRow
            for col in 0..<width {
                let x = (Float(col) + 0.5) * inv
                let s = field.sample(x, y)
                let f = s.height
                var c = theme.color(height: f, detail: s.detail)

                // jemné zrnko, aby plochy nepôsobili plasticky
                c = c.offset((hash2(Int32(col), Int32(row), 7) - 0.5) * 0.022)

                // obrys pobrežia
                let dl = abs(f - Band.shore)
                if dl < outlineWidth {
                    c = c.mix(theme.outline, 0.55 * (1 - dl / outlineWidth))
                }

                let to = tBase + col * 4
                tp[to] = byte(c.r)
                tp[to + 1] = byte(c.g)
                tp[to + 2] = byte(c.b)
                tp[to + 3] = 255

                // maska vody (antialiasovaná)
                let wa = clamp01((Band.shore - f) / outlineWidth + 0.5)
                let wb = byte(wa)
                let wo = wBase + col * 4
                wp[wo] = wb; wp[wo + 1] = wb; wp[wo + 2] = wb; wp[wo + 3] = wb

                // pena: pás tesne pod hladinou brehu, slabne smerom do vody
                var fa: Float = 0
                if f < Band.shore {
                    let t = (Band.shore - f) / foamWidth
                    if t < 1 { fa = (1 - t) * (1 - t) * wa }
                }
                let fb = byte(fa)
                let fo = fBase + col * 4
                fp[fo] = fb; fp[fo + 1] = fb; fp[fo + 2] = fb; fp[fo + 3] = fb
            }
        }

        // Dekorácie kreslíme vektorovo v bodoch nad terén.
        terrainCtx.saveGState()
        terrainCtx.scaleBy(x: scale, y: scale)
        Decorator.draw(in: terrainCtx, size: size, field: field, icons: icons, theme: theme)
        terrainCtx.restoreGState()

        guard let terrain = terrainCtx.makeImage(),
              let water = waterCtx.makeImage(),
              let foam = foamCtx.makeImage()
        else { return nil }
        return RenderedScene(terrain: terrain, waterMask: water, foamMask: foam)
    }

    @inline(__always)
    private static func byte(_ v: Float) -> UInt8 {
        UInt8(clamp01(v) * 255 + 0.5)
    }

    private static func makeContext(_ w: Int, _ h: Int, _ cs: CGColorSpace) -> CGContext? {
        CGContext(data: nil, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w * 4,
                  space: cs, bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)
    }
}
