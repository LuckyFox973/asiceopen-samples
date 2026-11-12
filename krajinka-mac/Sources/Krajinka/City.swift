import CoreGraphics

/// Budovy pre tému Mesto. Pohľad zhora s miernou perspektívou: strecha je
/// posunutá nahor úmerne výške, takže vidno južnú stenu s oknami. Tiene padajú
/// doprava dole. V centre (veľký šum) rastú sklenené mrakodrapy.
enum CityPainter {
    private enum Kind { case glass, concrete, brick, dark, beige }

    private struct Building {
        let x0: CGFloat, y0: CGFloat, x1: CGFloat, y1: CGFloat
        let height: CGFloat
        let seed: Int32
        let kind: Kind
    }

    @inline(__always)
    private static func h(_ a: Int32, _ b: Int32, _ salt: UInt32) -> CGFloat { CGFloat(hash2(a, b, salt)) }

    static func draw(in ctx: CGContext, size: CGSize, field: TerrainField) {
        let bw = CGFloat(CityGrid.blockW), bh = CGFloat(CityGrid.blockH), sw = CGFloat(CityGrid.street)
        var builds: [Building] = []
        for bj in 0...Int(size.height / bh) {
            for bi in 0...Int(size.width / bw) {
                let x0 = CGFloat(bi) * bw + sw / 2 + 3, y0 = CGFloat(bj) * bh + sw / 2 + 3
                let x1 = CGFloat(bi + 1) * bw - sw / 2 - 3, y1 = CGFloat(bj + 1) * bh - sw / 2 - 3
                let ib = Int32(bi), jb = Int32(bj)
                let nx = 1 + Int(h(ib, jb, 21) * 2.99), ny = 1 + Int(h(ib, jb, 22) * 1.99)
                for a in 0..<nx {
                    for b in 0..<ny {
                        let lx0 = x0 + (x1 - x0) * CGFloat(a) / CGFloat(nx) + 1.5
                        let lx1 = x0 + (x1 - x0) * CGFloat(a + 1) / CGFloat(nx) - 1.5
                        let ly0 = y0 + (y1 - y0) * CGFloat(b) / CGFloat(ny) + 1.5
                        let ly1 = y0 + (y1 - y0) * CGFloat(b + 1) / CGFloat(ny) - 1.5
                        let ka = ib &* 7 &+ Int32(a), kb = jb &* 5 &+ Int32(b)
                        let sd = h(ka, kb, 31)
                        let downtown = field.large(CGPoint(x: (lx0 + lx1) / 2, y: (ly0 + ly1) / 2))
                        let height = 8 + 30 * sd + 160 * CGFloat(sstep(0.5, 0.8, downtown)) * h(ka, kb, 32)
                        let lift = height * 0.22
                        let corners = [CGPoint(x: lx0, y: ly0), CGPoint(x: lx1, y: ly0),
                                       CGPoint(x: lx0, y: ly1 + lift), CGPoint(x: lx1, y: ly1 + lift),
                                       CGPoint(x: (lx0 + lx1) / 2, y: (ly0 + ly1) / 2)]
                        if corners.contains(where: { field.sdf($0) > -8 }) { continue }   // nie do parku
                        let kinds: [Kind] = [.concrete, .brick, .dark, .beige, .concrete]
                        let kind: Kind = height > 90 ? .glass : kinds[Int(sd * 4.99)]
                        builds.append(Building(x0: lx0, y0: ly0, x1: lx1, y1: ly1, height: height,
                                               seed: Int32(sd * 1e5), kind: kind))
                    }
                }
            }
        }

        // tiene
        ctx.setFillColor(RGB.black.cg(0.22))
        for b in builds {
            let L = b.height * 0.35
            ctx.beginPath()
            ctx.addLines(between: [CGPoint(x: b.x0, y: b.y1), CGPoint(x: b.x1, y: b.y1),
                                   CGPoint(x: b.x1 + L, y: b.y1 - L * 0.6), CGPoint(x: b.x1 + L, y: b.y0 - L * 0.6),
                                   CGPoint(x: b.x0 + L, y: b.y0 - L * 0.6), CGPoint(x: b.x0, y: b.y0)])
            ctx.closePath()
            ctx.fillPath()
        }
        // budovy od vzdialenejších (vyššie na obrazovke) po bližšie
        for b in builds.sorted(by: { $0.y0 > $1.y0 }) {
            drawBuilding(b, ctx)
        }
    }

    private static func roofColor(_ k: Kind) -> RGB {
        switch k {
        case .glass: return RGB(0x6F9CC4)
        case .concrete: return RGB(0xB9B6AE)
        case .brick: return RGB(0xB0714F)
        case .dark: return RGB(0x6B7078)
        case .beige: return RGB(0xD8C9A8)
        }
    }

    private static let colorSpace = CGColorSpace(name: CGColorSpace.sRGB)!

    private static func drawBuilding(_ b: Building, _ ctx: CGContext) {
        let lift = b.height * 0.22
        let rc = roofColor(b.kind)
        let w = b.x1 - b.x0
        // južná stena
        ctx.setFillColor(rc.scaled(0.62).cg())
        ctx.fill(CGRect(x: b.x0, y: b.y0, width: w, height: lift))
        if lift > 6 {
            ctx.setFillColor((b.kind == .glass ? RGB(0xBFD9F0) : RGB(0xF4E7B8)).cg(0.55))
            let rows = Int(lift / 5), cols = max(1, Int(w / 5))
            let cw = (w - 2.4) / CGFloat(cols)
            for r in 0..<rows {
                for k in 0..<cols where h(b.seed &+ Int32(r), Int32(k), 77) < 0.7 {
                    ctx.fill(CGRect(x: b.x0 + 1.2 + CGFloat(k) * cw, y: b.y0 + 1.5 + CGFloat(r) * 5, width: cw * 0.55, height: 2.2))
                }
            }
        }
        // strecha
        let ry0 = b.y0 + lift, ry1 = b.y1 + lift
        let roof = CGRect(x: b.x0, y: ry0, width: w, height: ry1 - ry0)
        if b.kind == .glass {
            let g = CGGradient(colorsSpace: colorSpace,
                               colors: [rc.scaled(1.15).cg(), rc.cg(), rc.scaled(0.85).cg()] as CFArray,
                               locations: [0, 0.5, 1])!
            ctx.saveGState()
            ctx.clip(to: roof)
            ctx.drawLinearGradient(g, start: CGPoint(x: b.x0, y: ry1), end: CGPoint(x: b.x1, y: ry0), options: [])
            ctx.restoreGState()
        } else {
            ctx.setFillColor(rc.cg())
            ctx.fill(roof)
        }
        ctx.setStrokeColor(RGB.black.cg(0.12))
        ctx.setLineWidth(0.8)
        ctx.stroke(roof.insetBy(dx: 0.4, dy: 0.4))

        let rh = ry1 - ry0
        if b.kind != .glass && w > 14 && rh > 14 {                       // klimatizácie
            for k in 0..<(Int(h(b.seed, 3, 3) * 3) + 1) {
                let ax = b.x0 + 3 + h(b.seed, Int32(k), 4) * (w - 9)
                let ay = ry0 + 3 + h(b.seed, Int32(k), 5) * (rh - 9)
                ctx.setFillColor(RGB.black.cg(0.18))
                ctx.fill(CGRect(x: ax + 1, y: ay - 1, width: 5, height: 4))
                ctx.setFillColor(RGB(0xD9D9D6).cg())
                ctx.fill(CGRect(x: ax, y: ay, width: 5, height: 4))
            }
        }
        if b.height > 70 && w > 20 && rh > 20 && h(b.seed, 9, 9) < 0.5 {   // heliport
            let c = CGPoint(x: b.x0 + w / 2, y: ry0 + rh / 2), r = min(w, rh) * 0.3
            ctx.setFillColor(RGB(0x4B4F55).cg())
            ctx.fillEllipse(in: CGRect(x: c.x - r, y: c.y - r, width: r * 2, height: r * 2))
            ctx.setStrokeColor(RGB.white.cg())
            ctx.setLineWidth(1)
            ctx.beginPath()
            ctx.move(to: CGPoint(x: c.x - 2.5, y: c.y - 3)); ctx.addLine(to: CGPoint(x: c.x - 2.5, y: c.y + 3))
            ctx.move(to: CGPoint(x: c.x + 2.5, y: c.y - 3)); ctx.addLine(to: CGPoint(x: c.x + 2.5, y: c.y + 3))
            ctx.move(to: CGPoint(x: c.x - 2.5, y: c.y)); ctx.addLine(to: CGPoint(x: c.x + 2.5, y: c.y))
            ctx.strokePath()
        }
        if b.kind == .concrete && h(b.seed, 8, 8) < 0.25 && w > 16 && rh > 16 {   // strešná záhrada
            ctx.setFillColor(RGB(0x7DB352).cg())
            ctx.fill(CGRect(x: b.x0 + 3, y: ry0 + 3, width: w - 6, height: rh - 6))
            for k in 0..<3 {
                let p = CGPoint(x: b.x0 + 5 + h(b.seed, Int32(k), 11) * (w - 10), y: ry0 + 5 + h(b.seed, Int32(k), 12) * (rh - 10))
                ctx.setFillColor(RGB(0x4A8C3A).cg())
                ctx.fillEllipse(in: CGRect(x: p.x - 2.6, y: p.y - 2.6, width: 5.2, height: 5.2))
                ctx.setFillColor(RGB(0x8CC860).cg())
                ctx.fillEllipse(in: CGRect(x: p.x - 1.8, y: p.y - 0.4, width: 2, height: 2))
            }
        }
    }
}
