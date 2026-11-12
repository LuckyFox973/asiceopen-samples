import CoreGraphics

/// Rozmiestňuje a kreslí flóru, kamene a drobnosti. Nikdy nie na ikony ani na chodníky.
/// Svetlo prichádza zhora zľava, tiene padajú doprava dole.
enum Decorator {
    private enum Kind {
        case tree, palm, pine, bush, bushFlowers, rock, rockSnowy, rockInWater, lily
        case tuft, flowers, starfish, reeds, crack

        var baseSize: CGFloat {
            switch self {
            case .palm: return 32
            case .tree: return 36
            case .pine: return 32
            case .bush, .bushFlowers: return 17
            case .rock: return 14
            case .rockSnowy: return 15
            case .rockInWater: return 13
            case .lily: return 13
            case .tuft, .starfish: return 6
            case .flowers: return 7
            case .reeds: return 12
            case .crack: return 14
            }
        }

        var isTall: Bool { self == .tree || self == .pine || self == .palm }
    }

    private struct Item {
        let p: CGPoint
        let kind: Kind
        let s: CGFloat
        let seed: Int32
    }

    /// Prechody a farby pripravené raz na celé prekreslenie.
    private struct Kit {
        let theme: Theme
        let shadow: CGGradient
        let blob: CGGradient
        let leafBack: CGGradient
        let leafFront: CGGradient
        let lily: CGGradient

        init(_ t: Theme) {
            theme = t
            shadow = Decorator.gradient([RGB.black.cg(0.30), RGB.black.cg(0.21), RGB.black.cg(0)], [0, 0.55, 1])
            blob = Decorator.gradient([t.foliageLight.cg(), t.foliageMid.cg(), t.foliageDark.cg()], [0, 0.45, 1])
            leafBack = Decorator.gradient([t.foliageDark.cg(), t.foliageMid.cg()], [0, 1])
            leafFront = Decorator.gradient([t.foliageMid.cg(), t.foliageLight.cg(), t.foliageMid.cg()], [0, 0.6, 1])
            lily = Decorator.gradient([t.foliageLight.cg(), t.foliageMid.cg()], [0, 1])
        }
    }

    private static let colorSpace = CGColorSpace(name: CGColorSpace.sRGB)!

    private static func gradient(_ colors: [CGColor], _ locations: [CGFloat]) -> CGGradient {
        CGGradient(colorsSpace: colorSpace, colors: colors as CFArray, locations: locations)!
    }

    @inline(__always)
    private static func h(_ a: Int32, _ b: Int32, _ salt: UInt32) -> CGFloat {
        CGFloat(hash2(a, b, salt))
    }

    // MARK: - Rozmiestnenie

    static func draw(in ctx: CGContext, size: CGSize, field: TerrainField, icons: [CGRect],
                     paths: [PathSegment], theme t: Theme) {
        let pathPoints = paths.flatMap { $0.points }

        func blocked(_ p: CGPoint, _ rad: CGFloat) -> Bool {
            for r in icons where p.x > r.minX - rad && p.x < r.maxX + rad && p.y > r.minY - rad && p.y < r.maxY + rad {
                return true
            }
            let lim = (rad * 0.8 + 6) * (rad * 0.8 + 6)
            for q in pathPoints where (q.x - p.x) * (q.x - p.x) + (q.y - p.y) * (q.y - p.y) < lim {
                return true
            }
            return false
        }

        let seed = t.seed
        var items: [Item] = []
        var ground: [Item] = []

        // Veľké prvky – mriežka 40 bodov, hustota podľa „hájového“ šumu
        let g: CGFloat = 40
        for j in 0..<(Int(size.height / g) + 2) {
            for i in 0..<(Int(size.width / g) + 2) {
                let ii = Int32(i), jj = Int32(j)
                let p = CGPoint(x: (CGFloat(i) + 0.1 + 0.8 * h(ii, jj, seed &+ 11)) * g,
                                y: (CGFloat(j) + 0.1 + 0.8 * h(ii, jj, seed &+ 23)) * g)
                let roll = h(ii, jj, seed &+ 37)
                let sz = h(ii, jj, seed &+ 53)
                let sid = Int32(h(ii, jj, seed &+ 61) * 1e6)
                let sd = CGFloat(field.sdf(p))
                let land = sd > 0 && field.clear(p) < 0.02
                let dens = CGFloat(sstep(0.38, 0.62, field.detail(p)))
                let bw = CGFloat(t.beachWidth)

                var kind: Kind?
                switch t.id {
                case .beach:
                    if land && sd > bw + 6 && roll < 0.15 + 0.6 * dens {
                        kind = roll < 0.1 + 0.45 * dens ? .palm : .bush
                    } else if land && sd > bw + 3 && roll < 0.2 {
                        kind = .bush
                    } else if sd > -40 && sd < -8 && roll < 0.05 {
                        kind = .rockInWater
                    }
                case .meadow:
                    if land && sd > 8 && roll < 0.1 + 0.7 * dens {
                        kind = roll < 0.05 + 0.55 * dens ? .tree : .bushFlowers
                    } else if sd > -30 && sd < -6 && roll < 0.12 {
                        kind = .lily
                    } else if land && roll < 0.03 {
                        kind = .rock
                    }
                case .winter:
                    if land && sd > 6 && roll < 0.12 + 0.72 * dens {
                        kind = .pine
                    } else if land && roll < 0.16 + 0.72 * dens {
                        kind = .rockSnowy
                    }
                }
                guard let k = kind else { continue }
                let s = k.baseSize * (0.8 + 0.45 * sz)
                if blocked(p, s * 0.75 + 14) { continue }
                items.append(Item(p: p, kind: k, s: s, seed: sid))
            }
        }

        // Drobnosti na zemi – jemnejšia mriežka 20 bodov
        let gg: CGFloat = 20
        for j in 0..<(Int(size.height / gg) + 2) {
            for i in 0..<(Int(size.width / gg) + 2) {
                let ii = Int32(i), jj = Int32(j)
                let p = CGPoint(x: (CGFloat(i) + 0.1 + 0.8 * h(ii, jj, seed &+ 71)) * gg,
                                y: (CGFloat(j) + 0.1 + 0.8 * h(ii, jj, seed &+ 83)) * gg)
                let roll = h(ii, jj, seed &+ 97)
                let sid = Int32(h(ii, jj, seed &+ 101) * 1e6)
                if field.clear(p) > 0.05 { continue }
                let sd = CGFloat(field.sdf(p))
                let bw = CGFloat(t.beachWidth)

                var kind: Kind?
                switch t.id {
                case .beach:
                    if sd > 2 && sd < bw - 3 && roll < 0.035 { kind = .starfish }
                    else if sd > bw + 4 && roll < 0.22 { kind = .tuft }
                case .meadow:
                    if sd > -6 && sd < 7 && roll < (sd > 0 ? 0.35 : 0.25) { kind = .reeds }
                    else if sd > 8 && roll < 0.16 { kind = .flowers }
                    else if sd > 8 && roll < 0.42 { kind = .tuft }
                case .winter:
                    if sd < -20 && roll < 0.03 { kind = .crack }
                }
                guard let k = kind else { continue }
                if blocked(p, k == .crack ? 2 : 8) { continue }
                ground.append(Item(p: p, kind: k, s: k.baseSize, seed: sid))
            }
        }

        let kit = Kit(t)
        for it in ground { drawItem(it, ctx, kit) }

        items.sort { $0.p.y > $1.p.y }
        // najprv všetky tiene, aby nepadali cez susedné koruny
        for it in items where it.kind != .lily {
            let rx = it.s * (it.kind.isTall ? 0.62 : 0.45)
            let dx = it.s * (it.kind.isTall ? 0.38 : 0.12)
            softShadow(ctx, kit, CGPoint(x: it.p.x + dx, y: it.p.y - it.s * 0.08), rx, rx * 0.42)
        }
        for it in items { drawItem(it, ctx, kit) }
    }

    private static func drawItem(_ it: Item, _ ctx: CGContext, _ kit: Kit) {
        switch it.kind {
        case .tree: tree(ctx, kit, it)
        case .palm: palm(ctx, kit, it)
        case .pine: pine(ctx, kit, it, snowy: kit.theme.id == .winter)
        case .bush: bush(ctx, kit, it, flowers: false)
        case .bushFlowers: bush(ctx, kit, it, flowers: true)
        case .rock: rock(ctx, kit, it, snowy: false, foam: false)
        case .rockSnowy: rock(ctx, kit, it, snowy: true, foam: false)
        case .rockInWater: rock(ctx, kit, it, snowy: false, foam: true)
        case .lily: lily(ctx, kit, it)
        case .tuft: tuft(ctx, kit, it)
        case .flowers: flowers(ctx, kit, it)
        case .starfish: starfish(ctx, kit, it)
        case .reeds: reeds(ctx, kit, it)
        case .crack: crack(ctx, it)
        }
    }

    // MARK: - Pomocníci

    private static func circle(_ ctx: CGContext, _ c: CGPoint, _ r: CGFloat) {
        ctx.fillEllipse(in: CGRect(x: c.x - r, y: c.y - r, width: r * 2, height: r * 2))
    }

    private static func softShadow(_ ctx: CGContext, _ kit: Kit, _ c: CGPoint, _ rx: CGFloat, _ ry: CGFloat) {
        ctx.saveGState()
        ctx.translateBy(x: c.x, y: c.y)
        ctx.scaleBy(x: 1, y: ry / rx)
        ctx.drawRadialGradient(kit.shadow, startCenter: .zero, startRadius: 0,
                               endCenter: .zero, endRadius: rx, options: [])
        ctx.restoreGState()
    }

    /// Guľa osvetlená zľava zhora – základ korún stromov a kríkov.
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

    // MARK: - Tvary

    private static func tree(_ ctx: CGContext, _ kit: Kit, _ it: Item) {
        let t = kit.theme, x = it.p.x, y = it.p.y, s = it.s
        let R = s * 0.44
        ctx.setFillColor(t.trunkDark.cg())
        ctx.fill(CGRect(x: x - s * 0.06, y: y, width: s * 0.12, height: s * 0.32))
        let blobs: [(CGFloat, CGFloat, CGFloat)] = [(-0.48, -0.22, 0.68), (0.46, -0.18, 0.7), (0.05, 0.32, 0.66), (0, 0, 1.0)]
        for (k, b) in blobs.enumerated() {
            let j = h(Int32(k), it.seed, 5) * 0.15
            shadedBlob(ctx, kit, CGPoint(x: x + b.0 * R, y: y + s * 0.62 + b.1 * R + j * R), R * b.2 * (0.92 + j))
        }
        ctx.setFillColor(t.foliageLight.cg(0.55))
        for k in 0..<3 {
            let a = 2.2 + CGFloat(k) * 0.35, rr = R * (0.45 + 0.12 * CGFloat(k))
            circle(ctx, CGPoint(x: x + cos(a) * rr * 0.6, y: y + s * 0.62 + sin(a) * rr * 0.6), s * 0.035)
        }
    }

    private static func palm(_ ctx: CGContext, _ kit: Kit, _ it: Item) {
        let t = kit.theme, x = it.p.x, y = it.p.y, s = it.s
        let lean = (h(it.seed, 1, 9) - 0.5) * 0.5
        let top = CGPoint(x: x + s * (0.18 + lean * 0.3), y: y + s * 0.95)
        let ctrl = CGPoint(x: x + s * (0.02 + lean * 0.2), y: y + s * 0.55)
        func q(_ t: CGFloat) -> CGPoint {
            let u = 1 - t
            return CGPoint(x: u * u * x + 2 * u * t * ctrl.x + t * t * top.x,
                           y: u * u * y + 2 * u * t * ctrl.y + t * t * top.y)
        }
        // kmeň z prstencov
        ctx.setLineCap(.round)
        let rings = 8
        for k in 0..<rings {
            let t0 = CGFloat(k) / CGFloat(rings), t1 = CGFloat(k + 1) / CGFloat(rings)
            ctx.setStrokeColor((k % 2 == 0 ? t.trunk : t.trunkDark).cg())
            ctx.setLineWidth(s * (0.14 - 0.06 * t0))
            ctx.beginPath()
            ctx.move(to: q(t0))
            ctx.addLine(to: q(t1))
            ctx.strokePath()
        }
        // listy: zadná (tmavšia) a predná (svetlejšia) vrstva
        let leaves = 7
        let base = h(it.seed, 2, 9) * 2 * .pi
        for layer in 0..<2 {
            for k in 0..<leaves where k % 2 == layer {
                let ang = base + CGFloat(k) / CGFloat(leaves) * 2 * .pi
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
                ctx.saveGState()
                ctx.clip()
                ctx.drawLinearGradient(layer == 0 ? kit.leafBack : kit.leafFront,
                                       start: .zero, end: CGPoint(x: L, y: 0),
                                       options: [.drawsBeforeStartLocation, .drawsAfterEndLocation])
                ctx.restoreGState()
                // stredové rebro listu
                ctx.setStrokeColor(t.foliageDark.cg(0.5))
                ctx.setLineWidth(s * 0.018)
                ctx.beginPath()
                ctx.move(to: CGPoint(x: s * 0.03, y: 0))
                ctx.addCurve(to: CGPoint(x: L * 0.97, y: -droop * 0.48),
                             control1: CGPoint(x: L * 0.4, y: w * 0.25), control2: CGPoint(x: L * 0.75, y: 0))
                ctx.strokePath()
                ctx.restoreGState()
            }
        }
        ctx.setFillColor(RGB(0x5A3A1E).cg())
        for k in 0..<3 {
            let a = base + CGFloat(k) * 2.1
            circle(ctx, CGPoint(x: top.x + cos(a) * s * 0.06, y: top.y + sin(a) * s * 0.05), s * 0.05)
        }
    }

    private static func pine(_ ctx: CGContext, _ kit: Kit, _ it: Item, snowy: Bool) {
        let t = kit.theme, x = it.p.x, y = it.p.y, s = it.s
        ctx.setFillColor(t.trunkDark.cg())
        ctx.fill(CGRect(x: x - s * 0.05, y: y, width: s * 0.1, height: s * 0.22))
        let tiers = 4
        var apex = y
        for k in 0..<tiers {
            let kf = CGFloat(k)
            let by = y + s * (0.12 + 0.24 * kf)
            let half = s * (0.46 - 0.095 * kf)
            let ay = by + s * 0.46
            let sag = s * 0.05
            apex = ay
            // ľavá polovica osvetlená, pravá v tieni
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
        if snowy {
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
    }

    private static func bush(_ ctx: CGContext, _ kit: Kit, _ it: Item, flowers: Bool) {
        let x = it.p.x, y = it.p.y, s = it.s
        for b in [(CGFloat(-0.24), CGFloat(0.18), CGFloat(0.3)), (0.24, 0.2, 0.3), (0, 0.34, 0.36)] {
            shadedBlob(ctx, kit, CGPoint(x: x + b.0 * s, y: y + b.1 * s), s * b.2)
        }
        let palette = kit.theme.flowers
        guard flowers, !palette.isEmpty else { return }
        ctx.setFillColor(palette[Int(h(it.seed, 0, 8) * CGFloat(palette.count)) % palette.count].cg())
        for k in 0..<4 {
            let a = h(it.seed, Int32(k), 4) * 2 * .pi, r = s * 0.28 * h(it.seed, Int32(k), 6)
            circle(ctx, CGPoint(x: x + cos(a) * r, y: y + s * 0.32 + sin(a) * r), s * 0.045)
        }
    }

    private static func rock(_ ctx: CGContext, _ kit: Kit, _ it: Item, snowy: Bool, foam: Bool) {
        let t = kit.theme, x = it.p.x, y = it.p.y, s = it.s
        let pts: [CGPoint] = (0..<7).map { k in
            let a = CGFloat(k) / 7 * 2 * .pi, r = 0.85 + 0.3 * h(it.seed, Int32(k), 21)
            return CGPoint(x: x + cos(a) * s * 0.5 * r, y: y + sin(a) * s * 0.34 * r)
        }
        if foam {
            ctx.setStrokeColor(RGB.white.cg(0.45))
            ctx.setLineWidth(1.6)
            let rx = s * 0.66, ry = rx * 0.62
            ctx.strokeEllipse(in: CGRect(x: x - rx, y: y - s * 0.04 - ry, width: rx * 2, height: ry * 2))
        }
        ctx.setFillColor(t.rock.cg())
        ctx.beginPath()
        ctx.addLines(between: pts)
        ctx.closePath()
        ctx.fillPath()
        ctx.setFillColor(snowy ? RGB.white.cg(0.95) : t.rockLight.cg())
        ctx.beginPath()
        ctx.addLines(between: [CGPoint(x: x + s * 0.06, y: y + s * 0.06)] + Array(pts[2...5]))
        ctx.closePath()
        ctx.fillPath()
    }

    private static func tuft(_ ctx: CGContext, _ kit: Kit, _ it: Item) {
        let x = it.p.x, y = it.p.y, s = it.s
        ctx.setStrokeColor(kit.theme.foliageDark.cg(0.75))
        ctx.setLineWidth(0.9)
        ctx.setLineCap(.round)
        for k in 0..<4 {
            let kf = CGFloat(k)
            let a = .pi / 2 + (kf - 1.5) * 0.35 + (h(it.seed, Int32(k), 2) - 0.5) * 0.2
            let L = s * (0.7 + 0.4 * h(it.seed, Int32(k), 3))
            ctx.beginPath()
            ctx.move(to: CGPoint(x: x + (kf - 1.5) * 0.8, y: y))
            ctx.addLine(to: CGPoint(x: x + cos(a) * L, y: y + sin(a) * L))
            ctx.strokePath()
        }
    }

    private static func flowers(_ ctx: CGContext, _ kit: Kit, _ it: Item) {
        let palette = kit.theme.flowers
        guard !palette.isEmpty else { return }
        for k in 0..<6 {
            let a = h(it.seed, Int32(k), 4) * 2 * .pi, r = it.s * h(it.seed, Int32(k), 6)
            let c = CGPoint(x: it.p.x + cos(a) * r, y: it.p.y + sin(a) * r * 0.7)
            ctx.setFillColor(RGB.black.cg(0.18))
            circle(ctx, CGPoint(x: c.x + 0.5, y: c.y - 0.5), 1.6)
            ctx.setFillColor(palette[Int(h(it.seed, Int32(k), 8) * CGFloat(palette.count)) % palette.count].cg())
            circle(ctx, c, 1.5)
        }
    }

    private static func starfish(_ ctx: CGContext, _ kit: Kit, _ it: Item) {
        let rot = h(it.seed, 1, 1) * 2 * .pi
        let pts: [CGPoint] = (0..<10).map { k in
            let a = rot + CGFloat(k) / 10 * 2 * .pi, r = it.s * (k % 2 == 0 ? 0.5 : 0.2)
            return CGPoint(x: it.p.x + cos(a) * r, y: it.p.y + sin(a) * r)
        }
        ctx.setFillColor(kit.theme.accent.cg())
        ctx.beginPath()
        ctx.addLines(between: pts)
        ctx.closePath()
        ctx.fillPath()
    }

    private static func reeds(_ ctx: CGContext, _ kit: Kit, _ it: Item) {
        let x = it.p.x, y = it.p.y, s = it.s
        ctx.setLineCap(.round)
        for k in 0..<5 {
            let kk = Int32(k)
            let ox = CGFloat(k - 2) * s * 0.12 + (h(it.seed, kk, 1) - 0.5) * s * 0.1
            let L = s * (0.7 + 0.5 * h(it.seed, kk, 2))
            let bend = (h(it.seed, kk, 3) - 0.5) * s * 0.25
            ctx.setStrokeColor(kit.theme.foliageMid.cg())
            ctx.setLineWidth(1.0)
            ctx.beginPath()
            ctx.move(to: CGPoint(x: x + ox, y: y))
            ctx.addCurve(to: CGPoint(x: x + ox + bend, y: y + L),
                         control1: CGPoint(x: x + ox, y: y + L * 0.5), control2: CGPoint(x: x + ox + bend, y: y + L * 0.8))
            ctx.strokePath()
            if h(it.seed, kk, 4) < 0.5 {
                ctx.setFillColor(RGB(0x6B4426).cg())
                let c = CGPoint(x: x + ox + bend * 0.95, y: y + L * 0.88)
                ctx.fillEllipse(in: CGRect(x: c.x - s * 0.045, y: c.y - s * 0.1, width: s * 0.09, height: s * 0.2))
            }
        }
    }

    private static func lily(_ ctx: CGContext, _ kit: Kit, _ it: Item) {
        let x = it.p.x, y = it.p.y, s = it.s
        let a = h(it.seed, 1, 1) * 2 * .pi
        ctx.setFillColor(RGB.black.cg(0.12))
        ctx.fillEllipse(in: CGRect(x: x + 1 - s * 0.5, y: y - 1 - s * 0.4, width: s, height: s * 0.8))

        ctx.saveGState()
        ctx.translateBy(x: x, y: y)
        ctx.scaleBy(x: 1, y: 0.8)
        ctx.beginPath()
        ctx.move(to: .zero)
        ctx.addArc(center: .zero, radius: s * 0.5, startAngle: a + 0.35, endAngle: a - 0.35 + 2 * .pi, clockwise: false)
        ctx.closePath()
        ctx.clip()
        ctx.drawRadialGradient(kit.lily, startCenter: CGPoint(x: -s * 0.15, y: s * 0.15), startRadius: 0,
                               endCenter: .zero, endRadius: s * 0.5, options: [.drawsAfterEndLocation])
        ctx.restoreGState()

        if h(it.seed, 2, 2) < 0.35 {
            ctx.setFillColor(RGB(0xF6B6C8).cg())
            for k in 0..<6 {
                let b = CGFloat(k) / 6 * 2 * .pi
                ctx.saveGState()
                ctx.translateBy(x: x + cos(b) * s * 0.12, y: y + sin(b) * s * 0.1)
                ctx.rotate(by: b)
                ctx.scaleBy(x: 1, y: 0.55)
                circle(ctx, .zero, s * 0.13)
                ctx.restoreGState()
            }
            ctx.setFillColor(RGB(0xFFD166).cg())
            circle(ctx, it.p, s * 0.07)
        }
    }

    private static func crack(_ ctx: CGContext, _ it: Item) {
        var pts = [it.p]
        var a = h(it.seed, 0, 1) * 2 * .pi
        for k in 0..<5 {
            a += (h(it.seed, Int32(k), 2) - 0.5) * 1.4
            let L = it.s * (0.4 + 0.6 * h(it.seed, Int32(k), 3))
            let last = pts[pts.count - 1]
            pts.append(CGPoint(x: last.x + cos(a) * L, y: last.y + sin(a) * L))
        }
        ctx.setLineJoin(.round)
        ctx.setStrokeColor(RGB(0x3E5F80).cg(0.35))
        ctx.setLineWidth(1.0)
        ctx.beginPath()
        ctx.addLines(between: pts.map { CGPoint(x: $0.x + 0.6, y: $0.y - 0.6) })
        ctx.strokePath()
        ctx.setStrokeColor(RGB.white.cg(0.7))
        ctx.setLineWidth(0.8)
        ctx.beginPath()
        ctx.addLines(between: pts)
        ctx.strokePath()
    }

    // MARK: - Chodníky a mosty

    static func drawPaths(_ segments: [PathSegment], in ctx: CGContext, theme t: Theme) {
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
            // kamienky
            ctx.setFillColor(t.pathEdge.cg(0.5))
            for k in stride(from: 0, to: p.count, by: 3) where hash2(Int32(k), Int32(p.count), 41) < 0.5 {
                let o = CGFloat(hash2(Int32(k), 1, 42) - 0.5) * 5
                circle(ctx, CGPoint(x: p[k].x + o, y: p[k].y - o * 0.5), 0.9)
            }
        }

        for seg in segments where seg.isBridge && seg.points.count >= 2 {
            let p = seg.points
            // tieň mosta na vode
            ctx.setStrokeColor(RGB.black.cg(0.22))
            ctx.setLineWidth(12)
            ctx.beginPath(); ctx.addLines(between: p.map { CGPoint(x: $0.x + 3, y: $0.y - 3) }); ctx.strokePath()
            // paluba
            ctx.setStrokeColor(t.wood.cg())
            ctx.setLineWidth(9)
            ctx.beginPath(); ctx.addLines(between: p); ctx.strokePath()

            func normal(_ k: Int) -> CGPoint {
                let a = p[max(0, k - 1)], b = p[min(p.count - 1, k + 1)]
                let tx = b.x - a.x, ty = b.y - a.y
                let l = max(hypot(tx, ty), 0.0001)
                return CGPoint(x: -ty / l, y: tx / l)
            }
            // dosky
            ctx.setStrokeColor(t.woodDark.cg(0.8))
            ctx.setLineWidth(0.8)
            for k in 1..<max(1, p.count - 1) {
                let n = normal(k)
                ctx.beginPath()
                ctx.move(to: CGPoint(x: p[k].x - n.x * 4.5, y: p[k].y - n.y * 4.5))
                ctx.addLine(to: CGPoint(x: p[k].x + n.x * 4.5, y: p[k].y + n.y * 4.5))
                ctx.strokePath()
            }
            // zábradlie
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
