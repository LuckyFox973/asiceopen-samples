import CoreGraphics

/// Malé bitmapy (sprity) pre animované vrstvy. Všetko sa generuje kódom
/// v 2× hustote; nič sa nenačítava zo súborov. Pohľad je zhora, smer
/// pohybu je vždy +x (rotáciu robí Core Animation podľa dráhy).
enum SceneAssets {
    static let scale: CGFloat = 2
    private static let colorSpace = CGColorSpace(name: CGColorSpace.sRGB)!

    /// Kreslí do kontextu s veľkosťou v bodoch (počiatok vľavo dole).
    private static func image(_ size: CGSize, scale: CGFloat = SceneAssets.scale, _ draw: (CGContext) -> Void) -> CGImage {
        let ctx = CGContext(data: nil, width: Int(size.width * scale), height: Int(size.height * scale),
                            bitsPerComponent: 8, bytesPerRow: 0, space: colorSpace,
                            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
        ctx.scaleBy(x: scale, y: scale)
        ctx.setLineCap(.round)
        ctx.setLineJoin(.round)
        draw(ctx)
        return ctx.makeImage()!
    }

    private static func radial(_ ctx: CGContext, _ c: CGPoint, _ r: CGFloat, _ inner: CGColor, _ outer: CGColor, mid: CGFloat = 0) {
        let g = CGGradient(colorsSpace: colorSpace, colors: [inner, outer] as CFArray, locations: [mid, 1])!
        ctx.drawRadialGradient(g, startCenter: c, startRadius: 0, endCenter: c, endRadius: r, options: [])
    }

    private static func c(_ hex: UInt32, _ a: CGFloat = 1) -> CGColor { RGB(hex).cg(a) }

    // MARK: - Voda a svetlo

    static let sparkleTileSize: CGFloat = 180

    /// Dlaždica s krátkymi odleskami – dve rôzne sa posúvajú proti sebe.
    static func sparkleTile(seed: UInt64) -> CGImage {
        let t = sparkleTileSize
        return image(CGSize(width: t, height: t)) { ctx in
            var rng = SplitMix64(seed: seed)
            for _ in 0..<16 {
                let x = CGFloat.random(in: 4..<(t - 14), using: &rng)
                let y = CGFloat.random(in: 4..<(t - 4), using: &rng)
                let len = CGFloat.random(in: 3...10, using: &rng)
                ctx.setStrokeColor(c(0xFFFFFF, CGFloat.random(in: 0.45...0.9, using: &rng)))
                ctx.setLineWidth(CGFloat.random(in: 0.9...1.5, using: &rng))
                ctx.beginPath()
                ctx.move(to: CGPoint(x: x, y: y))
                ctx.addLine(to: CGPoint(x: x + len, y: y))
                ctx.strokePath()
            }
        }
    }

    /// Štvorcípa hviezdička so žiarou – záblesk slnka na vode alebo snehu.
    static let glint: CGImage = image(CGSize(width: 16, height: 16)) { ctx in
        let m = CGPoint(x: 8, y: 8)
        radial(ctx, m, 5, c(0xFFFFFF, 0.9), c(0xFFFFFF, 0))
        ctx.setFillColor(c(0xFFFFFF))
        for (dx, dy) in [(CGFloat(7.5), CGFloat(1.1)), (1.1, 7.5)] {
            ctx.beginPath()
            ctx.move(to: CGPoint(x: m.x - dx, y: m.y))
            ctx.addLine(to: CGPoint(x: m.x, y: m.y + dy))
            ctx.addLine(to: CGPoint(x: m.x + dx, y: m.y))
            ctx.addLine(to: CGPoint(x: m.x, y: m.y - dy))
            ctx.closePath()
            ctx.fillPath()
        }
    }

    // MARK: - Oblaky

    static let cloudSize = CGSize(width: 560, height: 300)

    /// Kopy kopovitého oblaku: v strede vyššie a väčšie, na krajoch menšie.
    private static func cloudPuffs(seed: UInt64) -> [(CGPoint, CGFloat)] {
        var rng = SplitMix64(seed: seed)
        var out: [(CGPoint, CGFloat)] = []
        let n = 14
        for i in 0..<n {
            let t = CGFloat(i) / CGFloat(n - 1)
            let bell = sin(t * .pi)
            let x = 90 + t * 380 + CGFloat.random(in: -18...18, using: &rng)
            let r = 34 + 46 * bell * CGFloat.random(in: 0.75...1.1, using: &rng)
            let y = 120 + bell * 45 + CGFloat.random(in: -12...14, using: &rng)
            out.append((CGPoint(x: x, y: y), r))
        }
        for _ in 0..<6 {
            out.append((CGPoint(x: CGFloat.random(in: 180...380, using: &rng),
                                y: 175 + CGFloat.random(in: -10...20, using: &rng)),
                        CGFloat.random(in: 38...60, using: &rng)))
        }
        return out
    }

    private static func drawPuffs(_ ctx: CGContext, _ puffs: [(CGPoint, CGFloat)], _ color: UInt32, _ alpha: CGFloat,
                                  shift: CGPoint = .zero, k: CGFloat = 1) {
        let g = CGGradient(colorsSpace: colorSpace,
                           colors: [c(color, alpha), c(color, alpha * 0.9), c(color, 0)] as CFArray,
                           locations: [0, 0.55, 1])!
        for (p, r) in puffs {
            let q = CGPoint(x: p.x + shift.x, y: p.y + shift.y)
            ctx.drawRadialGradient(g, startCenter: q, startRadius: 0, endCenter: q, endRadius: r * k, options: [])
        }
    }

    /// Tri tvary oblakov. Tieň a oblak majú rovnaké kopy, takže tieň „sedí“.
    static let clouds: [(shadow: CGImage, white: CGImage)] = ([42, 7, 99] as [UInt64]).map { seed in
        let puffs = cloudPuffs(seed: seed)
        let shadow = image(cloudSize, scale: 1) { ctx in
            drawPuffs(ctx, puffs, 0x000000, 0.14)
        }
        let white = image(cloudSize, scale: 1) { ctx in
            drawPuffs(ctx, puffs, 0xCCDBEB, 0.45)                                   // tienistý spodok
            drawPuffs(ctx, puffs, 0xFFFFFF, 0.475, shift: CGPoint(x: -6, y: 7), k: 0.9) // osvetlený vršok
        }
        return (shadow, white)
    }

    // MARK: - Sneh

    static let snowflake: CGImage = image(CGSize(width: 12, height: 12)) { ctx in
        radial(ctx, CGPoint(x: 6, y: 6), 6, c(0xFFFFFF), c(0xFFFFFF, 0), mid: 0.35)
    }

    static let snowflakeSoft: CGImage = image(CGSize(width: 20, height: 20)) { ctx in
        radial(ctx, CGPoint(x: 10, y: 10), 10, c(0xFFFFFF, 0.85), c(0xFFFFFF, 0), mid: 0.1)
    }

    // MARK: - Vtáky

    static let birdSize = CGSize(width: 34, height: 30)

    /// - Parameter wingsUp: druhá fáza mávnutia (krídla skrátené perspektívou).
    static func bird(_ kind: Birds, wingsUp: Bool) -> CGImage {
        let look: (body: UInt32, wing: UInt32, tip: UInt32, k: CGFloat)
        switch kind {
        case .gulls, .none: look = (0xF6F6F4, 0xC7CDD3, 0x2E2E30, 1.0)
        case .songbirds: look = (0x6E4C30, 0x55391F, 0x3A2614, 0.7)
        case .crows: look = (0x2B2B30, 0x1F1F24, 0x111114, 0.85)
        }
        let body = look.body, wing = look.wing, tip = look.tip, k = look.k
        return image(birdSize) { ctx in
            let m = CGPoint(x: 17, y: 15)
            ctx.translateBy(x: m.x, y: m.y)
            ctx.scaleBy(x: k, y: k)
            let span: CGFloat = wingsUp ? 6 : 14
            let sweep: CGFloat = wingsUp ? -1 : -4
            for side: CGFloat in [-1, 1] {
                ctx.beginPath()
                ctx.move(to: CGPoint(x: 3, y: 0))
                ctx.addCurve(to: CGPoint(x: sweep - 2, y: side * span),
                             control1: CGPoint(x: 3, y: side * span * 0.5), control2: CGPoint(x: 1, y: side * span * 0.9))
                ctx.addCurve(to: CGPoint(x: -3, y: 0),
                             control1: CGPoint(x: sweep - 3, y: side * span * 0.6), control2: CGPoint(x: -3, y: side * span * 0.3))
                ctx.closePath()
                ctx.setFillColor(c(wing))
                ctx.fillPath()
                // tmavé končeky krídel
                ctx.setFillColor(c(tip))
                ctx.fillEllipse(in: CGRect(x: sweep - 3.5, y: side * span - (side > 0 ? 2.5 : 0.5), width: 3, height: 3))
            }
            ctx.setFillColor(c(body))
            ctx.fillEllipse(in: CGRect(x: -7, y: -2.2, width: 13, height: 4.4))       // telo
            ctx.fillEllipse(in: CGRect(x: 4.5, y: -1.9, width: 3.8, height: 3.8))     // hlava
            ctx.beginPath()                                                          // chvost
            ctx.move(to: CGPoint(x: -6, y: 0))
            ctx.addLine(to: CGPoint(x: -10, y: 2))
            ctx.addLine(to: CGPoint(x: -10, y: -2))
            ctx.closePath()
            ctx.fillPath()
            ctx.setFillColor(c(kind == .gulls ? 0xF2B33D : tip))
            ctx.fillEllipse(in: CGRect(x: 8, y: -0.7, width: 2.4, height: 1.4))      // zobák
        }
    }

    static let birdShadow: CGImage = image(CGSize(width: 30, height: 26)) { ctx in
        ctx.translateBy(x: 15, y: 13)
        ctx.scaleBy(x: 1, y: 0.75)
        radial(ctx, .zero, 13, c(0x000000, 0.22), c(0x000000, 0), mid: 0.2)
    }

    // MARK: - Plachetnica

    static let boatSize = CGSize(width: 44, height: 34)

    static let boat: CGImage = image(boatSize) { ctx in
        ctx.translateBy(x: 22, y: 17)
        // tieň plachty na vode
        ctx.setFillColor(c(0x000000, 0.16))
        ctx.beginPath()
        ctx.move(to: CGPoint(x: 2, y: -2))
        ctx.addLine(to: CGPoint(x: -9, y: -14))
        ctx.addLine(to: CGPoint(x: -6, y: -3))
        ctx.closePath()
        ctx.fillPath()
        // trup
        let hull = CGMutablePath()
        hull.move(to: CGPoint(x: -14, y: 0))
        hull.addCurve(to: CGPoint(x: 15, y: 0), control1: CGPoint(x: -14, y: 6.5), control2: CGPoint(x: 8, y: 6))
        hull.addCurve(to: CGPoint(x: -14, y: 0), control1: CGPoint(x: 8, y: -6), control2: CGPoint(x: -14, y: -6.5))
        hull.closeSubpath()
        ctx.setFillColor(c(0x000000, 0.18))
        ctx.saveGState(); ctx.translateBy(x: 1.2, y: -1.5); ctx.addPath(hull); ctx.fillPath(); ctx.restoreGState()
        ctx.addPath(hull)
        ctx.setFillColor(c(0xF7F4EC))
        ctx.fillPath()
        ctx.addPath(hull)
        ctx.setStrokeColor(c(0x8A6A4A))
        ctx.setLineWidth(0.9)
        ctx.strokePath()
        // drevená paluba
        ctx.setFillColor(c(0xC0915F))
        ctx.fillEllipse(in: CGRect(x: -10, y: -3, width: 19, height: 6))
        ctx.setStrokeColor(c(0x8A6A4A, 0.7))
        ctx.setLineWidth(0.5)
        for x: CGFloat in [-6, -2, 2, 6] {
            ctx.beginPath(); ctx.move(to: CGPoint(x: x, y: -2.6)); ctx.addLine(to: CGPoint(x: x, y: 2.6)); ctx.strokePath()
        }
        // plachta (vydutá vetrom) so svetlou a tienistou stranou
        let sail = CGMutablePath()
        sail.move(to: CGPoint(x: 3, y: 0.5))
        sail.addCurve(to: CGPoint(x: -11, y: 12), control1: CGPoint(x: 1, y: 7), control2: CGPoint(x: -5, y: 11))
        sail.addLine(to: CGPoint(x: -8, y: 1))
        sail.closeSubpath()
        ctx.addPath(sail)
        ctx.setFillColor(c(0xFFFFFF))
        ctx.fillPath()
        ctx.beginPath()
        ctx.move(to: CGPoint(x: -8, y: 1))
        ctx.addLine(to: CGPoint(x: -11, y: 12))
        ctx.addCurve(to: CGPoint(x: -3, y: 4), control1: CGPoint(x: -8, y: 9), control2: CGPoint(x: -5, y: 6))
        ctx.closePath()
        ctx.setFillColor(c(0xDCD6C8))
        ctx.fillPath()
        // sťažeň
        ctx.setFillColor(c(0x6E4B2C))
        ctx.fillEllipse(in: CGRect(x: 1.8, y: -0.7, width: 2.2, height: 2.2))
        // vlajočka
        ctx.setFillColor(c(0xE4574B))
        ctx.beginPath(); ctx.move(to: CGPoint(x: 3, y: 0.5)); ctx.addLine(to: CGPoint(x: -1, y: 2.5)); ctx.addLine(to: CGPoint(x: -0.5, y: -0.5)); ctx.closePath(); ctx.fillPath()
    }

    static let wakeSize = CGSize(width: 90, height: 30)

    /// Brázda za loďou: dve rozbiehajúce sa čiary, ktoré dozadu slabnú.
    static let wake: CGImage = image(wakeSize) { ctx in
        let stern = CGPoint(x: 90, y: 15)
        let steps = 18
        for side: CGFloat in [-1, 1] {
            for k in 0..<steps {
                let t0 = CGFloat(k) / CGFloat(steps), t1 = CGFloat(k + 1) / CGFloat(steps)
                func p(_ t: CGFloat) -> CGPoint {
                    CGPoint(x: stern.x - t * 88, y: stern.y + side * (2 + t * 11 + sin(t * 9) * 0.8))
                }
                ctx.setStrokeColor(c(0xFFFFFF, 0.75 * (1 - t0)))
                ctx.setLineWidth(1.6 - t0)
                ctx.beginPath(); ctx.move(to: p(t0)); ctx.addLine(to: p(t1)); ctx.strokePath()
            }
        }
        // spenená stopa priamo za kormou
        radial(ctx, CGPoint(x: 84, y: 15), 8, c(0xFFFFFF, 0.5), c(0xFFFFFF, 0))
    }

    // MARK: - Kačky

    static let duckSize = CGSize(width: 22, height: 16)

    static func duck(baby: Bool) -> CGImage {
        image(duckSize) { ctx in
            ctx.translateBy(x: 11, y: 8)
            let k: CGFloat = baby ? 0.62 : 1
            ctx.scaleBy(x: k, y: k)
            // vlnka okolo
            ctx.setStrokeColor(c(0xFFFFFF, 0.35))
            ctx.setLineWidth(0.8)
            ctx.strokeEllipse(in: CGRect(x: -9.5, y: -6, width: 19, height: 12))
            ctx.setFillColor(c(0x000000, 0.15))
            ctx.fillEllipse(in: CGRect(x: -7, y: -5, width: 14, height: 9))
            ctx.setFillColor(c(baby ? 0xF2D16B : 0x8E6E4C))
            ctx.fillEllipse(in: CGRect(x: -7.5, y: -4, width: 14, height: 8))            // telo
            if !baby {
                ctx.setFillColor(c(0x6E5038))
                ctx.fillEllipse(in: CGRect(x: -5.5, y: -2.6, width: 8, height: 5.2))     // krídla
                ctx.setFillColor(c(0x3E7FB8))
                ctx.fillEllipse(in: CGRect(x: -2, y: -2.8, width: 2.4, height: 1.2))      // modré pierko
            }
            ctx.setFillColor(c(baby ? 0xF5DB7E : 0x2F6B3A))
            ctx.fillEllipse(in: CGRect(x: 3.5, y: -2.4, width: 4.8, height: 4.8))        // hlava
            ctx.setFillColor(c(0xF2A541))
            ctx.fillEllipse(in: CGRect(x: 7.4, y: -1, width: 3, height: 2))              // zobák
        }
    }

    // MARK: - Motýle

    static let butterflySize = CGSize(width: 16, height: 16)

    static func butterfly(color: UInt32, open: Bool) -> CGImage {
        image(butterflySize) { ctx in
            ctx.translateBy(x: 8, y: 8)
            let w: CGFloat = open ? 1 : 0.3
            for side: CGFloat in [-1, 1] {
                ctx.setFillColor(c(color))
                ctx.fillEllipse(in: CGRect(x: -1, y: side > 0 ? 0.5 : -0.5 - 6 * w, width: 5, height: 6 * w))   // predné krídlo
                ctx.setFillColor(c(color, 0.85))
                ctx.fillEllipse(in: CGRect(x: -4.5, y: side > 0 ? 0.5 : -0.5 - 4.5 * w, width: 4, height: 4.5 * w)) // zadné krídlo
                ctx.setFillColor(c(0x2A2420, 0.6))
                ctx.fillEllipse(in: CGRect(x: 1.5, y: side > 0 ? 4.5 * w : -5.5 * w, width: 1.6, height: 1.2 * w + 0.2))
            }
            ctx.setStrokeColor(c(0x2A2420))
            ctx.setLineWidth(1.1)
            ctx.beginPath(); ctx.move(to: CGPoint(x: -4, y: 0)); ctx.addLine(to: CGPoint(x: 4, y: 0)); ctx.strokePath()
        }
    }
}
