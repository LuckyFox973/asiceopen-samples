import CoreGraphics

/// Budovy pre tému Mesto v maľovanom štýle: tlmené strechy s jemným prechodom,
/// mierna perspektíva (vidno južnú stenu), mäkké dvojité tiene, stromy pri uliciach.
enum CityPainter {
    private struct Building {
        let x0: CGFloat, y0: CGFloat, x1: CGFloat, y1: CGFloat
        let height: CGFloat
        let roof: RGB
    }

    private static let roofs: [RGB] = [RGB(0xC9C2B6), RGB(0xBFA48E), RGB(0xA9ADB3), RGB(0xD9CCB0), RGB(0xB8BEB0), RGB(0x9FB4C8)]
    private static let colorSpace = CGColorSpace(name: CGColorSpace.sRGB)!

    @inline(__always)
    private static func h(_ a: Int32, _ b: Int32, _ salt: UInt32) -> CGFloat { CGFloat(hash2(a, b, salt)) }

    static func draw(in ctx: CGContext, size: CGSize, field: TerrainField) {
        let bw = CGFloat(CityGrid.blockW), bh = CGFloat(CityGrid.blockH), sw = CGFloat(CityGrid.street)
        var builds: [Building] = []
        for bj in 0...Int(size.height / bh) {
            for bi in 0...Int(size.width / bw) {
                let x0 = CGFloat(bi) * bw + sw / 2 + 4, y0 = CGFloat(bj) * bh + sw / 2 + 4
                let x1 = CGFloat(bi + 1) * bw - sw / 2 - 4, y1 = CGFloat(bj + 1) * bh - sw / 2 - 4
                let ib = Int32(bi), jb = Int32(bj)
                let nx = 1 + Int(h(ib, jb, 21) * 2.99), ny = 1 + Int(h(ib, jb, 22) * 1.99)
                for a in 0..<nx {
                    for b in 0..<ny {
                        let lx0 = x0 + (x1 - x0) * CGFloat(a) / CGFloat(nx) + 2
                        let lx1 = x0 + (x1 - x0) * CGFloat(a + 1) / CGFloat(nx) - 2
                        let ly0 = y0 + (y1 - y0) * CGFloat(b) / CGFloat(ny) + 2
                        let ly1 = y0 + (y1 - y0) * CGFloat(b + 1) / CGFloat(ny) - 2
                        let ka = ib &* 7 &+ Int32(a), kb = jb &* 5 &+ Int32(b)
                        let sd = h(ka, kb, 31)
                        let downtown = field.large(CGPoint(x: (lx0 + lx1) / 2, y: (ly0 + ly1) / 2))
                        let height = 8 + 26 * sd + 130 * CGFloat(sstep(0.52, 0.8, downtown)) * h(ka, kb, 32)
                        let lift = height * 0.18
                        let pts = [CGPoint(x: lx0, y: ly0), CGPoint(x: lx1, y: ly0), CGPoint(x: lx0, y: ly1 + lift),
                                   CGPoint(x: lx1, y: ly1 + lift), CGPoint(x: (lx0 + lx1) / 2, y: (ly0 + ly1) / 2)]
                        if pts.contains(where: { field.sdf($0) > -10 }) { continue }          // nie do parku
                        let roof = height < 80 ? roofs[Int(sd * CGFloat(roofs.count)) % roofs.count] : RGB(0x8FAFCF)
                        builds.append(Building(x0: lx0, y0: ly0, x1: lx1, y1: ly1, height: height, roof: roof))
                    }
                }
            }
        }

        // mäkké tiene: dve priesvitné vrstvy rôznej dĺžky
        for b in builds {
            let L = b.height * 0.3
            for (k, a) in [(CGFloat(1.0), CGFloat(0.10)), (0.6, 0.08)] {
                ctx.setFillColor(RGB(0x2A3040).cg(a))
                ctx.beginPath()
                ctx.addLines(between: [CGPoint(x: b.x0, y: b.y1), CGPoint(x: b.x1, y: b.y1),
                                       CGPoint(x: b.x1 + L * k, y: b.y1 - L * 0.6 * k), CGPoint(x: b.x1 + L * k, y: b.y0 - L * 0.6 * k),
                                       CGPoint(x: b.x0 + L * k, y: b.y0 - L * 0.6 * k), CGPoint(x: b.x0, y: b.y0)])
                ctx.closePath()
                ctx.fillPath()
            }
        }
        for b in builds.sorted(by: { $0.y0 > $1.y0 }) { drawBuilding(b, ctx) }

        // stromy pozdĺž ulíc
        for bj in 0...(Int(size.height / bh) + 1) {
            for bi in 0...(Int(size.width / bw) + 1) {
                for k in 0..<6 where h(Int32(bi * 11 + k), Int32(bj), 51) < 0.55 {
                    let horiz = k < 3
                    let x = horiz ? CGFloat(bi) * bw + (h(Int32(bi), Int32(bj * 13 + k), 52) * 0.8 + 0.1) * bw
                                  : CGFloat(bi) * bw + sw / 2 + 2.5
                    let y = horiz ? CGFloat(bj) * bh + sw / 2 + 2.5
                                  : CGFloat(bj) * bh + (h(Int32(bi * 3 + k), Int32(bj), 53) * 0.8 + 0.1) * bh
                    let p = CGPoint(x: x, y: y)
                    if field.sdf(p) > -8 { continue }
                    Decorator.softTree(ctx, p, 4.2)
                }
            }
        }
    }

    private static func drawBuilding(_ b: Building, _ ctx: CGContext) {
        let lift = b.height * 0.18
        let w = b.x1 - b.x0
        ctx.setFillColor(b.roof.scaled(0.72).cg())
        ctx.fill(CGRect(x: b.x0, y: b.y0, width: w, height: lift))              // južná stena
        if lift > 7 {
            ctx.setFillColor(RGB(0xF6F0DC).cg(0.28))
            for r in 0..<Int(lift / 6) {
                ctx.fill(CGRect(x: b.x0 + 1.5, y: b.y0 + 2 + CGFloat(r) * 6, width: w - 3, height: 1.6))
            }
        }
        let roof = CGRect(x: b.x0, y: b.y0 + lift, width: w, height: b.y1 - b.y0)
        let g = CGGradient(colorsSpace: colorSpace, colors: [b.roof.scaled(1.06).cg(), b.roof.scaled(0.95).cg()] as CFArray,
                           locations: [0, 1])!
        ctx.saveGState()
        ctx.clip(to: roof)
        ctx.drawLinearGradient(g, start: CGPoint(x: roof.minX, y: roof.maxY), end: CGPoint(x: roof.maxX, y: roof.minY), options: [])
        ctx.restoreGState()
    }
}
