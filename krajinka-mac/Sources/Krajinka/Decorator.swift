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

    // MARK: - Zasnežené smreky (Zima)

    private static let spruceShadow = CGGradient(colorsSpace: colorSpace,
        colors: [CGColor(srgbRed: 0.25, green: 0.33, blue: 0.45, alpha: 0.32),
                 CGColor(srgbRed: 0.25, green: 0.33, blue: 0.45, alpha: 0)] as CFArray, locations: [0, 1])!
    private static let spruceLayers: [(f: CGFloat, g: CGGradient)] = [
        (1.0, CGGradient(colorsSpace: colorSpace, colors: [RGB(0x2F5E50).cg(), RGB(0x1F4038).cg()] as CFArray, locations: [0, 1])!),
        (0.7, CGGradient(colorsSpace: colorSpace, colors: [RGB(0x3D705E).cg(), RGB(0x28503F).cg()] as CFArray, locations: [0, 1])!),
        (0.42, CGGradient(colorsSpace: colorSpace, colors: [RGB(0x4E8270).cg(), RGB(0x335E4F).cg()] as CFArray, locations: [0, 1])!),
    ]
    private static let snowCap = CGGradient(colorsSpace: colorSpace,
        colors: [RGB.white.cg(0.95), RGB.white.cg(0)] as CFArray, locations: [0, 1])!

    /// Smrek zhora: tri hviezdicové poschodia vetiev, sneh na osvetlenej strane, modrastý tieň.
    static func spruce(_ ctx: CGContext, _ p: CGPoint, _ r: CGFloat, seed: Int32) {
        let x = p.x, y = p.y
        let sc = CGPoint(x: x + r * 0.45, y: y - r * 0.45)
        ctx.drawRadialGradient(spruceShadow, startCenter: sc, startRadius: 0, endCenter: sc, endRadius: r * 1.15, options: [])
        let n = 7 + Int(h(seed, 1, 1) * 3)
        let rot = h(seed, 2, 2) * 2 * .pi
        for (k, layer) in spruceLayers.enumerated() {
            let R = r * layer.f, Ri = R * 0.74
            let turn = rot + CGFloat(k) * 0.35
            var pts: [CGPoint] = []
            for i in 0..<(n * 2) {
                let a = CGFloat(i) / CGFloat(n * 2) * 2 * .pi + turn
                let rr = (i % 2 == 0 ? R : Ri) * (0.9 + 0.2 * h(seed &+ Int32(k), Int32(i), 3))
                pts.append(CGPoint(x: x + cos(a) * rr, y: y + sin(a) * rr))
            }
            ctx.saveGState()
            ctx.beginPath()
            ctx.addLines(between: pts)
            ctx.closePath()
            ctx.clip()
            ctx.drawRadialGradient(layer.g, startCenter: CGPoint(x: x - R * 0.3, y: y + R * 0.3), startRadius: 0,
                                   endCenter: p, endRadius: R, options: [.drawsAfterEndLocation])
            ctx.restoreGState()
            for i in 0..<n {                                                   // sneh na vetvách
                let a = turn + CGFloat(i) / CGFloat(n) * 2 * .pi
                let lit = 0.5 + 0.5 * cos(a - 2.36)                            // svetlo zľava hore
                if lit < 0.25 { continue }
                let sr = R * (0.22 + 0.16 * lit)
                ctx.setFillColor(RGB.white.cg(0.88 * lit))
                ctx.fillEllipse(in: CGRect(x: x + cos(a) * R * 0.58 - sr, y: y + sin(a) * R * 0.58 - sr, width: sr * 2, height: sr * 2))
            }
        }
        let cc = CGPoint(x: x - r * 0.08, y: y + r * 0.08)
        ctx.drawRadialGradient(snowCap, startCenter: cc, startRadius: 0, endCenter: cc, endRadius: r * 0.22, options: [])
    }

    private static func drawWinterForest(in ctx: CGContext, size: CGSize, field: TerrainField, icons: [CGRect],
                                         pathPoints: [CGPoint], seed s: UInt32) {
        let g: CGFloat = 17
        var trees: [(CGPoint, CGFloat, Int32)] = []
        for j in 0..<(Int(size.height / g) + 2) {
            for i in 0..<(Int(size.width / g) + 2) {
                let ii = Int32(i), jj = Int32(j)
                let p = CGPoint(x: (CGFloat(i) + 0.1 + 0.8 * h(ii, jj, s &+ 11)) * g,
                                y: (CGFloat(j) + 0.1 + 0.8 * h(ii, jj, s &+ 23)) * g)
                let dens = CGFloat(sstep(0.3, 0.55, field.large(p) + (field.detail(p) - 0.5) * 0.3))
                if h(ii, jj, s &+ 37) > 0.35 + 0.6 * dens { continue }
                let r = 8 + 7 * h(ii, jj, s &+ 53) * (0.6 + 0.4 * dens)
                if CGFloat(field.sdf(p)) > -r - 4 { continue }
                if Painters.riverDistance(field, Float(p.x), Float(p.y)) < 0.02 { continue }
                if icons.contains(where: { $0.insetBy(dx: -r - 3, dy: -r - 3).contains(p) }) { continue }
                if pathPoints.contains(where: { hypot($0.x - p.x, $0.y - p.y) < r + 3 }) { continue }
                trees.append((p, r, Int32(h(ii, jj, s &+ 61) * 1e6)))
            }
        }
        for t in trees.sorted(by: { $0.0.y > $1.0.y }) { spruce(ctx, t.0, t.1, seed: t.2) }
    }

    /// Stromy na ostrovoch, v parkoch a osamelé stromy pri poliach. Nikdy nie na ikony a chodníky.
    static func draw(in ctx: CGContext, size: CGSize, field: TerrainField, icons: [CGRect],
                     paths: [PathSegment], theme t: Theme) {
        let pathPoints = paths.flatMap { $0.points }
        if t.id == .zima {
            drawWinterForest(in: ctx, size: size, field: field, icons: icons, pathPoints: pathPoints, seed: t.seed)
            return
        }
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
