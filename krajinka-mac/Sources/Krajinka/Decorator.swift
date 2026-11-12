import CoreGraphics

/// Rozmiestňuje a kreslí vektorové prvky podľa témy. Nikdy nie na ikony ani na chodníky.
/// Svetlo prichádza zhora zľava, tiene padajú doprava dole. 1:1 s tools/terrain_preview.py.
enum Decorator {
    private enum Kind {
        case tree, palm, pine, bush, umbrella, starfish, shell, house, hay

        var baseSize: CGFloat {
            switch self {
            case .pine: return 26
            case .umbrella: return 16
            case .starfish: return 6
            case .shell: return 5
            case .palm: return 30
            case .bush: return 16
            case .house: return 20
            case .hay: return 9
            case .tree: return 22
            }
        }
    }

    private struct Item {
        let p: CGPoint
        let kind: Kind
        let s: CGFloat
        let seed: Int32
    }

    /// Prechody pripravené raz na celé prekreslenie.
    private struct Kit {
        let theme: Theme
        let shadow30: CGGradient
        let shadow22: CGGradient
        let shadow28: CGGradient
        let shadow35: CGGradient
        let blob: CGGradient
        let leafBack: CGGradient
        let leafFront: CGGradient
        let hay: CGGradient

        init(_ t: Theme) {
            theme = t
            func shadow(_ a: CGFloat) -> CGGradient {
                Decorator.gradient([RGB.black.cg(a), RGB.black.cg(a * 0.7), RGB.black.cg(0)], [0, 0.55, 1])
            }
            shadow30 = shadow(0.30); shadow22 = shadow(0.22); shadow28 = shadow(0.28); shadow35 = shadow(0.35)
            blob = Decorator.gradient([t.foliageLight.cg(), t.foliageMid.cg(), t.foliageDark.cg()], [0, 0.45, 1])
            leafBack = Decorator.gradient([t.foliageDark.cg(), t.foliageMid.cg()], [0, 1])
            leafFront = Decorator.gradient([t.foliageMid.cg(), t.foliageLight.cg(), t.foliageMid.cg()], [0, 0.6, 1])
            hay = Decorator.gradient([RGB(0xF1D98B).cg(), RGB(0xC9A44E).cg()], [0, 1])
        }
    }

    private static let colorSpace = CGColorSpace(name: CGColorSpace.sRGB)!

    private static func gradient(_ colors: [CGColor], _ locations: [CGFloat]) -> CGGradient {
        CGGradient(colorsSpace: colorSpace, colors: colors as CFArray, locations: locations)!
    }

    @inline(__always)
    private static func h(_ a: Int32, _ b: Int32, _ salt: UInt32) -> CGFloat { CGFloat(hash2(a, b, salt)) }

    // MARK: - Rozmiestnenie

    static func draw(in ctx: CGContext, size: CGSize, field: TerrainField, icons: [CGRect],
                     paths: [PathSegment], theme t: Theme) {
        let pathPoints = paths.flatMap { $0.points }
        let seed = t.seed
        let g: CGFloat = t.id == .zima ? 21 : 30
        var items: [Item] = []

        for j in 0..<(Int(size.height / g) + 2) {
            for i in 0..<(Int(size.width / g) + 2) {
                let ii = Int32(i), jj = Int32(j)
                let p = CGPoint(x: (CGFloat(i) + 0.1 + 0.8 * h(ii, jj, seed &+ 11)) * g,
                                y: (CGFloat(j) + 0.1 + 0.8 * h(ii, jj, seed &+ 23)) * g)
                let roll = h(ii, jj, seed &+ 37)
                let sz = h(ii, jj, seed &+ 53)
                let sid = Int32(h(ii, jj, seed &+ 61) * 1e6)
                let sd = CGFloat(field.sdf(p))
                let dens = CGFloat(sstep(0.38, 0.62, field.detail(p)))

                var kind: Kind?
                switch t.id {
                case .plaz:
                    if sd > 18 && roll < 0.05 { kind = .umbrella }
                    else if sd > 2 && sd < 14 && roll < 0.1 { kind = roll < 0.05 ? .starfish : .shell }
                    else if sd > 10 && sd < 40 && roll > 0.9 { kind = .palm }
                case .ostrovy:
                    if sd > 9 && roll < 0.35 + 0.5 * dens { kind = roll < 0.1 + 0.45 * dens ? .palm : .bush }
                case .polia:
                    if sd > -60 && sd < -14 && roll < 0.035 { kind = .house }
                    else if sd < -10 && roll < 0.06 {
                        let crop = Int(worley(Float(p.x), Float(p.y), cell: Painters.fieldCell, seed: seed &+ 60, jitter: 0.9).id * 7)
                        if crop == 0 || crop == 6 { kind = .hay }                  // pšenica, strnisko
                    } else if sd > 6 && roll < 0.08 { kind = .tree }
                case .mesto:
                    if sd > 6 && roll < 0.3 + 0.3 * dens { kind = .tree }
                case .les:
                    if sd < -16 && roll < 0.02 && field.large(p) < 0.45 { kind = .tree }   // osamelé stromy na lúkach
                case .zima:
                    if sd < -3 && roll < 0.93 { kind = .pine }
                }
                guard let k = kind else { continue }
                let s = k.baseSize * (0.8 + 0.45 * sz)
                let box = CGRect(x: p.x - s * 0.7 - 5, y: p.y - s * 0.5 - 5, width: s * 1.4 + 10, height: s * 1.7 + 10)
                if icons.contains(where: { $0.intersects(box) }) { continue }
                if pathPoints.contains(where: { hypot($0.x - p.x, $0.y - p.y) < 6 }) { continue }
                items.append(Item(p: p, kind: k, s: s, seed: sid))
            }
        }

        let kit = Kit(t)
        items.sort { $0.p.y > $1.p.y }
        for it in items where [.tree, .palm, .bush, .pine].contains(it.kind) {
            softShadow(ctx, it.kind == .pine ? kit.shadow22 : kit.shadow30,
                       CGPoint(x: it.p.x + it.s * 0.38, y: it.p.y - it.s * 0.08), it.s * 0.62, it.s * 0.26)
        }
        for it in items {
            switch it.kind {
            case .tree: tree(ctx, kit, it)
            case .palm: palm(ctx, kit, it)
            case .pine: pine(ctx, kit, it)
            case .bush: bush(ctx, kit, it)
            case .umbrella: umbrella(ctx, kit, it)
            case .starfish: starfish(ctx, it)
            case .shell: shell(ctx, it)
            case .house: house(ctx, kit, it)
            case .hay: haybale(ctx, kit, it)
            }
        }
    }

    // MARK: - Pomocníci

    private static func circle(_ ctx: CGContext, _ c: CGPoint, _ r: CGFloat) {
        ctx.fillEllipse(in: CGRect(x: c.x - r, y: c.y - r, width: r * 2, height: r * 2))
    }

    private static func softShadow(_ ctx: CGContext, _ g: CGGradient, _ c: CGPoint, _ rx: CGFloat, _ ry: CGFloat) {
        ctx.saveGState()
        ctx.translateBy(x: c.x, y: c.y)
        ctx.scaleBy(x: 1, y: ry / rx)
        ctx.drawRadialGradient(g, startCenter: .zero, startRadius: 0, endCenter: .zero, endRadius: rx, options: [])
        ctx.restoreGState()
    }

    /// Guľa osvetlená zľava zhora – koruny stromov a kríkov.
    private static func shadedBlob(_ ctx: CGContext, _ kit: Kit, _ c: CGPoint, _ r: CGFloat) {
        ctx.saveGState()
        ctx.addEllipse(in: CGRect(x: c.x - r, y: c.y - r, width: r * 2, height: r * 2))
        ctx.clip()
        ctx.drawRadialGradient(kit.blob,
                               startCenter: CGPoint(x: c.x - r * 0.38, y: c.y + r * 0.42), startRadius: r * 0.05,
                               endCenter: c, endRadius: r * 1.05,
                               options: [.drawsBeforeStartLocation, .drawsAfterEndLocation])
        ctx.restoreGState()
    }

    // MARK: - Flóra

    private static func tree(_ ctx: CGContext, _ kit: Kit, _ it: Item) {
        let x = it.p.x, y = it.p.y, s = it.s, R = s * 0.44
        ctx.setFillColor(kit.theme.trunkDark.cg())
        ctx.fill(CGRect(x: x - s * 0.06, y: y, width: s * 0.12, height: s * 0.3))
        let blobs: [(CGFloat, CGFloat, CGFloat)] = [(-0.48, -0.22, 0.68), (0.46, -0.18, 0.7), (0.05, 0.32, 0.66), (0, 0, 1.0)]
        for b in blobs {
            shadedBlob(ctx, kit, CGPoint(x: x + b.0 * R, y: y + s * 0.62 + b.1 * R), R * b.2)
        }
    }

    private static func palm(_ ctx: CGContext, _ kit: Kit, _ it: Item) {
        let t = kit.theme, x = it.p.x, y = it.p.y, s = it.s
        let top = CGPoint(x: x + s * 0.2, y: y + s * 0.95)
        let ctrl = CGPoint(x: x + s * 0.03, y: y + s * 0.55)
        func q(_ t: CGFloat) -> CGPoint {
            let u = 1 - t
            let a = u * u, b = 2 * u * t, c = t * t
            return CGPoint(x: a * x + b * ctrl.x + c * top.x, y: a * y + b * ctrl.y + c * top.y)
        }
        ctx.setLineCap(.round)
        for k in 0..<8 {
            let t0 = CGFloat(k) / 8, t1 = CGFloat(k + 1) / 8
            ctx.setStrokeColor((k % 2 == 0 ? t.trunk : t.trunkDark).cg())
            ctx.setLineWidth(s * (0.14 - 0.06 * t0))
            ctx.beginPath(); ctx.move(to: q(t0)); ctx.addLine(to: q(t1)); ctx.strokePath()
        }
        let base = h(it.seed, 2, 9) * 2 * .pi
        for layer in 0..<2 {
            for k in 0..<7 where k % 2 == layer {
                let ang = base + CGFloat(k) / 7 * 2 * .pi
                let L = s * 0.62 * (0.85 + 0.3 * h(it.seed, Int32(k), 3))
                let w = s * 0.15, droop = s * 0.18
                ctx.saveGState()
                ctx.translateBy(x: top.x, y: top.y)
                ctx.rotate(by: ang)
                ctx.beginPath()
                ctx.move(to: .zero)
                ctx.addCurve(to: CGPoint(x: L, y: -droop * 0.5),
                             control1: CGPoint(x: L * 0.3, y: w * 1.1), control2: CGPoint(x: L * 0.75, y: w * 0.6))
                ctx.addCurve(to: .zero,
                             control1: CGPoint(x: L * 0.7, y: -w * 0.2), control2: CGPoint(x: L * 0.3, y: -w * 0.7))
                ctx.closePath()
                ctx.clip()
                ctx.drawLinearGradient(layer == 0 ? kit.leafBack : kit.leafFront, start: .zero, end: CGPoint(x: L, y: 0),
                                       options: [.drawsBeforeStartLocation, .drawsAfterEndLocation])
                ctx.restoreGState()
            }
        }
    }

    private static func pine(_ ctx: CGContext, _ kit: Kit, _ it: Item) {
        let t = kit.theme, x = it.p.x, y = it.p.y, s = it.s
        let snowy = t.id == .zima
        ctx.setFillColor(t.trunkDark.cg())
        ctx.fill(CGRect(x: x - s * 0.05, y: y, width: s * 0.1, height: s * 0.22))
        var apex = y
        for k in 0..<4 {
            let kf = CGFloat(k)
            let by = y + s * (0.12 + 0.24 * kf)
            let half = s * (0.46 - 0.095 * kf)
            let ay = by + s * 0.46
            let sag = s * 0.05
            apex = ay
            for (side, color) in [(CGFloat(-1), t.foliageMid), (CGFloat(1), t.foliageDark)] {
                ctx.beginPath()
                ctx.move(to: CGPoint(x: x, y: ay))
                ctx.addCurve(to: CGPoint(x: x + side * half, y: by),
                             control1: CGPoint(x: x + side * half * 0.35, y: ay - s * 0.2),
                             control2: CGPoint(x: x + side * half * 0.8, y: by + s * 0.08))
                ctx.addCurve(to: CGPoint(x: x, y: by + sag * 0.6),
                             control1: CGPoint(x: x + side * half * 0.5, y: by - sag * 0.2),
                             control2: CGPoint(x: x + side * half * 0.2, y: by + sag))
                ctx.closePath()
                ctx.setFillColor(color.cg())
                ctx.fillPath()
            }
            if snowy {
                ctx.setFillColor(RGB.white.cg(0.95))
                ctx.beginPath()
                ctx.move(to: CGPoint(x: x - half * 0.98, y: by + s * 0.01))
                ctx.addCurve(to: CGPoint(x: x - half * 0.02, y: by + s * 0.08),
                             control1: CGPoint(x: x - half * 0.6, y: by + s * 0.07),
                             control2: CGPoint(x: x - half * 0.25, y: by + s * 0.06))
                ctx.addCurve(to: CGPoint(x: x - half * 0.98, y: by + s * 0.01),
                             control1: CGPoint(x: x - half * 0.3, y: by + s * 0.02),
                             control2: CGPoint(x: x - half * 0.65, y: by - s * 0.015))
                ctx.fillPath()
            }
        }
        guard snowy else { return }
        let ay = apex
        ctx.setFillColor(RGB.white.cg())
        ctx.beginPath()
        ctx.move(to: CGPoint(x: x, y: ay))
        ctx.addCurve(to: CGPoint(x: x - s * 0.11, y: ay - s * 0.13),
                     control1: CGPoint(x: x - s * 0.06, y: ay - s * 0.06), control2: CGPoint(x: x - s * 0.1, y: ay - s * 0.11))
        ctx.addCurve(to: CGPoint(x: x + s * 0.1, y: ay - s * 0.13),
                     control1: CGPoint(x: x - s * 0.02, y: ay - s * 0.1), control2: CGPoint(x: x + s * 0.05, y: ay - s * 0.12))
        ctx.addCurve(to: CGPoint(x: x, y: ay),
                     control1: CGPoint(x: x + s * 0.07, y: ay - s * 0.08), control2: CGPoint(x: x + s * 0.03, y: ay - s * 0.04))
        ctx.fillPath()
    }

    private static func bush(_ ctx: CGContext, _ kit: Kit, _ it: Item) {
        let x = it.p.x, y = it.p.y, s = it.s
        let blobs: [(CGFloat, CGFloat, CGFloat)] = [(-0.24, 0.18, 0.3), (0.24, 0.2, 0.3), (0, 0.34, 0.36)]
        for b in blobs { shadedBlob(ctx, kit, CGPoint(x: x + b.0 * s, y: y + b.1 * s), s * b.2) }
    }

    // MARK: - Pláž

    private static func umbrella(_ ctx: CGContext, _ kit: Kit, _ it: Item) {
        let x = it.p.x, y = it.p.y, s = it.s
        let canopy = [RGB(0xE4574B), RGB(0xF2B33D), RGB(0x3E8ED0), RGB(0x2BB39B)][Int(h(it.seed, 1, 1) * 4) % 4]
        let towel = [RGB(0xFFFFFF), RGB(0xF6C744), RGB(0x7FC8F8)][Int(h(it.seed, 3, 3) * 3) % 3]
        // uterák
        ctx.saveGState()
        ctx.translateBy(x: x + s * 0.55, y: y - s * 0.35)
        ctx.rotate(by: (h(it.seed, 2, 2) - 0.5) * 0.8)
        ctx.setFillColor(towel.cg())
        ctx.fill(CGRect(x: -s * 0.22, y: -s * 0.45, width: s * 0.44, height: s * 0.9))
        ctx.setFillColor(RGB(0xE4574B).cg(0.8))
        for k in 0..<3 { ctx.fill(CGRect(x: -s * 0.22, y: -s * 0.3 + CGFloat(k) * s * 0.3, width: s * 0.44, height: s * 0.08)) }
        ctx.restoreGState()
        softShadow(ctx, kit.shadow28, CGPoint(x: x + s * 0.25, y: y - s * 0.2), s * 0.62, s * 0.5)
        // slnečník: 8 dielov
        for k in 0..<8 {
            let a0 = CGFloat(k) / 8 * 2 * .pi, a1 = CGFloat(k + 1) / 8 * 2 * .pi
            ctx.beginPath()
            ctx.move(to: it.p)
            ctx.addArc(center: it.p, radius: s * 0.5, startAngle: a0, endAngle: a1, clockwise: false)
            ctx.closePath()
            ctx.setFillColor((k % 2 == 0 ? canopy : RGB.white).cg())
            ctx.fillPath()
        }
        ctx.setStrokeColor(RGB.black.cg(0.15))
        ctx.setLineWidth(0.6)
        ctx.strokeEllipse(in: CGRect(x: x - s * 0.5, y: y - s * 0.5, width: s, height: s))
        ctx.setFillColor(RGB(0x5A3A1E).cg())
        circle(ctx, it.p, s * 0.06)
    }

    private static func starfish(_ ctx: CGContext, _ it: Item) {
        let rot = h(it.seed, 1, 1) * 2 * .pi
        let pts: [CGPoint] = (0..<10).map { k in
            let a = rot + CGFloat(k) / 10 * 2 * .pi, r = it.s * (k % 2 == 0 ? 0.5 : 0.2)
            return CGPoint(x: it.p.x + cos(a) * r, y: it.p.y + sin(a) * r)
        }
        ctx.setFillColor(RGB(0xF0806E).cg())
        ctx.beginPath(); ctx.addLines(between: pts); ctx.closePath(); ctx.fillPath()
    }

    private static func shell(_ ctx: CGContext, _ it: Item) {
        let s = it.s
        ctx.saveGState()
        ctx.translateBy(x: it.p.x, y: it.p.y)
        ctx.rotate(by: h(it.seed, 1, 1) * 2 * .pi)
        ctx.setFillColor(RGB(0xF6E7D2).cg())
        ctx.beginPath()
        ctx.move(to: .zero)
        ctx.addArc(center: .zero, radius: s * 0.5, startAngle: 0, endAngle: .pi, clockwise: false)
        ctx.closePath()
        ctx.fillPath()
        ctx.setStrokeColor(RGB(0xC9A27E).cg(0.8))
        ctx.setLineWidth(0.5)
        for k in 0..<5 {
            let b = (CGFloat(k) + 0.5) / 5 * .pi
            ctx.beginPath(); ctx.move(to: .zero); ctx.addLine(to: CGPoint(x: cos(b) * s * 0.48, y: sin(b) * s * 0.48)); ctx.strokePath()
        }
        ctx.restoreGState()
    }

    // MARK: - Polia

    private static func haybale(_ ctx: CGContext, _ kit: Kit, _ it: Item) {
        let x = it.p.x, y = it.p.y, s = it.s
        softShadow(ctx, kit.shadow30, CGPoint(x: x + s * 0.3, y: y - s * 0.1), s * 0.6, s * 0.3)
        ctx.saveGState()
        ctx.addEllipse(in: CGRect(x: x - s * 0.5, y: y - s * 0.5, width: s, height: s))
        ctx.clip()
        ctx.drawRadialGradient(kit.hay, startCenter: CGPoint(x: x - s * 0.15, y: y + s * 0.15), startRadius: 0,
                               endCenter: it.p, endRadius: s * 0.5, options: [.drawsAfterEndLocation])
        ctx.restoreGState()
        ctx.setStrokeColor(RGB(0xA9873B).cg(0.8))
        ctx.setLineWidth(0.7)
        for r: CGFloat in [0.15, 0.28, 0.4] {
            ctx.beginPath()
            ctx.addArc(center: it.p, radius: s * r, startAngle: 0, endAngle: 5.5, clockwise: false)
            ctx.strokePath()
        }
    }

    /// Domček zhora-spredu: sedlová strecha v dvoch tónoch, stena s oknami, komín, tieň.
    private static func house(_ ctx: CGContext, _ kit: Kit, _ it: Item) {
        let x = it.p.x, y = it.p.y, w = it.s, d = it.s * 0.7, wall = it.s * 0.3
        let roof = [RGB(0xB5523B), RGB(0x8C5A3C), RGB(0x5E6B7A), RGB(0xC0703F)][Int(h(it.seed, 1, 5) * 4) % 4]
        softShadow(ctx, kit.shadow35, CGPoint(x: x + w * 0.35, y: y - wall * 0.1), w * 0.75, d * 0.45)
        ctx.setFillColor(RGB(0xEDE3D0).cg())
        ctx.fill(CGRect(x: x - w / 2, y: y, width: w, height: wall))
        ctx.setFillColor(RGB(0x6E8FB0).cg())
        for k in 0..<3 {
            ctx.fill(CGRect(x: x - w / 2 + w * (0.15 + CGFloat(k) * 0.28), y: y + wall * 0.3, width: w * 0.12, height: wall * 0.4))
        }
        ctx.setFillColor(RGB(0x7A5533).cg())
        ctx.fill(CGRect(x: x - w * 0.06, y: y, width: w * 0.12, height: wall * 0.6))
        let top = y + wall, ridge = top + d * 0.5
        ctx.setFillColor(roof.scaled(0.78).cg())
        ctx.fill(CGRect(x: x - w / 2 - 1, y: top, width: w + 2, height: ridge - top))
        ctx.setFillColor(roof.cg())
        ctx.fill(CGRect(x: x - w / 2 - 1, y: ridge, width: w + 2, height: top + d - ridge))
        ctx.setStrokeColor(RGB.black.cg(0.15))
        ctx.setLineWidth(0.6)
        ctx.beginPath(); ctx.move(to: CGPoint(x: x - w / 2 - 1, y: ridge)); ctx.addLine(to: CGPoint(x: x + w / 2 + 1, y: ridge)); ctx.strokePath()
        ctx.setFillColor(RGB(0x8A6A5A).cg())
        ctx.fill(CGRect(x: x + w * 0.22, y: ridge + d * 0.08, width: w * 0.1, height: d * 0.22))
    }

    // MARK: - Chodníky a mosty

    static func drawPaths(_ segments: [PathSegment], in ctx: CGContext, theme t: Theme) {
        guard !segments.isEmpty else { return }
        ctx.setLineCap(.round)
        ctx.setLineJoin(.round)

        for seg in segments where !seg.isBridge && seg.points.count >= 2 {
            let p = seg.points
            ctx.setStrokeColor(t.pathEdge.cg(0.45))
            ctx.setLineWidth(10)
            ctx.beginPath(); ctx.addLines(between: p); ctx.strokePath()
            ctx.setStrokeColor(t.path.cg())
            ctx.setLineWidth(7)
            ctx.beginPath(); ctx.addLines(between: p); ctx.strokePath()
            ctx.setFillColor(t.pathEdge.cg(0.5))
            for k in stride(from: 0, to: p.count, by: 3) where hash2(Int32(k), Int32(p.count), 41) < 0.5 {
                let o = CGFloat(hash2(Int32(k), 1, 42) - 0.5) * 5
                circle(ctx, CGPoint(x: p[k].x + o, y: p[k].y - o * 0.5), 0.9)
            }
        }

        for seg in segments where seg.isBridge && seg.points.count >= 2 {
            let p = seg.points
            ctx.setStrokeColor(RGB.black.cg(0.22))
            ctx.setLineWidth(12)
            ctx.beginPath(); ctx.addLines(between: p.map { CGPoint(x: $0.x + 3, y: $0.y - 3) }); ctx.strokePath()
            ctx.setStrokeColor(t.wood.cg())
            ctx.setLineWidth(9)
            ctx.beginPath(); ctx.addLines(between: p); ctx.strokePath()

            func normal(_ k: Int) -> CGPoint {
                let a = p[max(0, k - 1)], b = p[min(p.count - 1, k + 1)]
                let tx = b.x - a.x, ty = b.y - a.y
                let l = max(hypot(tx, ty), 0.0001)
                return CGPoint(x: -ty / l, y: tx / l)
            }
            ctx.setStrokeColor(t.woodDark.cg(0.8))
            ctx.setLineWidth(0.8)
            for k in 1..<max(1, p.count - 1) {
                let n = normal(k)
                ctx.beginPath()
                ctx.move(to: CGPoint(x: p[k].x - n.x * 4.5, y: p[k].y - n.y * 4.5))
                ctx.addLine(to: CGPoint(x: p[k].x + n.x * 4.5, y: p[k].y + n.y * 4.5))
                ctx.strokePath()
            }
            ctx.setStrokeColor(t.woodDark.cg())
            ctx.setLineWidth(1.3)
            for side: CGFloat in [-1, 1] {
                let rail = p.indices.map { k -> CGPoint in
                    let n = normal(k)
                    return CGPoint(x: p[k].x + n.x * 5 * side, y: p[k].y + n.y * 5 * side)
                }
                ctx.beginPath(); ctx.addLines(between: rail); ctx.strokePath()
            }
        }
    }
}
