import CoreGraphics

/// Mäkké vektorové prvky: koruny stromov (bez obrysov, s jemným tieňom) a voliteľné chodníky.
enum Decorator {
    private static let colorSpace = CGColorSpace(name: CGColorSpace.sRGB)!
    private static let crown = CGGradient(colorsSpace: colorSpace,
                                          colors: [RGB(0x8DBE62).cg(), RGB(0x5E9246).cg(), RGB(0x3F6E30).cg()] as CFArray,
                                          locations: [0, 0.5, 1])!
    private static let shade = CGGradient(colorsSpace: colorSpace,
                                          colors: [RGB.black.cg(0.108), RGB.black.cg(0.065), RGB.black.cg(0)] as CFArray,
                                          locations: [0, 0.6, 1])!

    /// Koruna stromu zhora: svetlo zľava hore, mäkký dvojitý tieň doprava dole.
    static func softTree(_ ctx: CGContext, _ p: CGPoint, _ r: CGFloat) {
        for (dx, dy, rr) in [(r * 0.35, -r * 0.3, r * 1.05), (r * 0.25, -r * 0.2, r * 0.9)] {
            let c = CGPoint(x: p.x + dx, y: p.y + dy)
            ctx.drawRadialGradient(shade, startCenter: c, startRadius: 0, endCenter: c, endRadius: rr, options: [])
        }
        ctx.saveGState()
        ctx.addEllipse(in: CGRect(x: p.x - r, y: p.y - r, width: r * 2, height: r * 2))
        ctx.clip()
        ctx.drawRadialGradient(crown, startCenter: CGPoint(x: p.x - r * 0.35, y: p.y + r * 0.35), startRadius: r * 0.05,
                               endCenter: p, endRadius: r, options: [.drawsBeforeStartLocation, .drawsAfterEndLocation])
        ctx.restoreGState()
    }

    @inline(__always)
    private static func h(_ a: Int32, _ b: Int32, _ salt: UInt32) -> CGFloat { CGFloat(hash2(a, b, salt)) }

    /// Stromy na ostrovoch, v parkoch a osamelé stromy pri poliach. Nikdy nie na ikony a chodníky.
    static func draw(in ctx: CGContext, size: CGSize, field: TerrainField, icons: [CGRect],
                     paths: [PathSegment], theme t: Theme) {
        let pathPoints = paths.flatMap { $0.points }
        guard t.id == .ostrovy || t.id == .mesto || t.id == .polia else { return }
        let s = t.seed, g: CGFloat = 26
        var trees: [(CGPoint, CGFloat)] = []
        for j in 0..<(Int(size.height / g) + 2) {
            for i in 0..<(Int(size.width / g) + 2) {
                let ii = Int32(i), jj = Int32(j)
                let p = CGPoint(x: (CGFloat(i) + 0.1 + 0.8 * h(ii, jj, s &+ 11)) * g,
                                y: (CGFloat(j) + 0.1 + 0.8 * h(ii, jj, s &+ 23)) * g)
                let roll = h(ii, jj, s &+ 37), sz = h(ii, jj, s &+ 53)
                let sd = CGFloat(field.sdf(p))
                let dens = CGFloat(sstep(0.4, 0.6, field.detail(p)))
                var r: CGFloat?
                switch t.id {
                case .ostrovy: if sd > 16 && roll < 0.25 + 0.4 * dens { r = 6 + 5 * sz }
                case .mesto: if sd > 7 && roll < 0.3 + 0.3 * dens { r = 5 + 4 * sz }
                case .polia: if sd > -40 && sd < -8 && roll < 0.03 { r = 5 + 3 * sz }
                default: break
                }
                guard let rad = r else { continue }
                if icons.contains(where: { $0.insetBy(dx: -rad - 4, dy: -rad - 4).contains(p) }) { continue }
                if pathPoints.contains(where: { hypot($0.x - p.x, $0.y - p.y) < rad + 4 }) { continue }
                trees.append((p, rad))
            }
        }
        for (p, r) in trees.sorted(by: { $0.0.y > $1.0.y }) { softTree(ctx, p, r) }
    }

    // MARK: - Chodníky (voliteľné)

    static func drawPaths(_ segments: [PathSegment], in ctx: CGContext, theme t: Theme) {
        guard !segments.isEmpty else { return }
        ctx.setLineCap(.round)
        ctx.setLineJoin(.round)
        for seg in segments where !seg.isBridge && seg.points.count >= 2 {
            ctx.setStrokeColor(t.pathEdge.cg(0.35))
            ctx.setLineWidth(9)
            ctx.beginPath(); ctx.addLines(between: seg.points); ctx.strokePath()
            ctx.setStrokeColor(t.path.cg(0.95))
            ctx.setLineWidth(6)
            ctx.beginPath(); ctx.addLines(between: seg.points); ctx.strokePath()
        }
        for seg in segments where seg.isBridge && seg.points.count >= 2 {
            let p = seg.points
            ctx.setStrokeColor(RGB.black.cg(0.12))
            ctx.setLineWidth(11)
            ctx.beginPath(); ctx.addLines(between: p.map { CGPoint(x: $0.x + 3, y: $0.y - 3) }); ctx.strokePath()
            ctx.setStrokeColor(t.wood.cg())
            ctx.setLineWidth(8)
            ctx.beginPath(); ctx.addLines(between: p); ctx.strokePath()
            ctx.setStrokeColor(t.woodDark.cg(0.45))
            ctx.setLineWidth(0.7)
            for k in 1..<max(1, p.count - 1) {
                let a = p[k - 1], b = p[k + 1]
                let l = max(hypot(b.x - a.x, b.y - a.y), 0.0001)
                let n = CGPoint(x: -(b.y - a.y) / l * 4, y: (b.x - a.x) / l * 4)
                ctx.beginPath()
                ctx.move(to: CGPoint(x: p[k].x - n.x, y: p[k].y - n.y))
                ctx.addLine(to: CGPoint(x: p[k].x + n.x, y: p[k].y + n.y))
                ctx.strokePath()
            }
        }
    }
}
