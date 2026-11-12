import CoreGraphics

/// Malé bitmapy pre animované vrstvy. Generujú sa raz, nič sa nenačítava zo súborov.
enum SceneAssets {
    static let sparkleTileSize: CGFloat = 180

    private static let colorSpace = CGColorSpace(name: CGColorSpace.sRGB)!

    private static func context(_ w: Int, _ h: Int) -> CGContext {
        CGContext(data: nil, width: w, height: h, bitsPerComponent: 8, bytesPerRow: 0,
                  space: colorSpace, bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
    }

    /// Dlaždica s krátkymi odleskami na vode. Dve rôzne dlaždice sa prelínajú.
    static func sparkleTile(seed: UInt64) -> CGImage {
        let scale: CGFloat = 2
        let t = sparkleTileSize
        let ctx = context(Int(t * scale), Int(t * scale))
        ctx.scaleBy(x: scale, y: scale)
        ctx.setLineCap(.round)
        ctx.setLineWidth(1.3)
        var rng = SplitMix64(seed: seed)
        for _ in 0..<14 {
            let x = CGFloat.random(in: 4..<(t - 14), using: &rng)
            let y = CGFloat.random(in: 4..<(t - 4), using: &rng)
            let len = CGFloat.random(in: 3...9, using: &rng)
            let a = CGFloat.random(in: 0.5...0.95, using: &rng)
            ctx.setStrokeColor(CGColor(srgbRed: 1, green: 1, blue: 1, alpha: a))
            ctx.beginPath()
            ctx.move(to: CGPoint(x: x, y: y))
            ctx.addLine(to: CGPoint(x: x + len, y: y))
            ctx.strokePath()
        }
        return ctx.makeImage()!
    }

    static let cloudSize = CGSize(width: 520, height: 280)

    /// Mäkký tieň oblaku (pohľad zhora).
    static let cloudShadow: CGImage = {
        let w = Int(cloudSize.width), h = Int(cloudSize.height)
        let ctx = context(w, h)
        let colors = [CGColor(srgbRed: 0, green: 0, blue: 0, alpha: 0.55),
                      CGColor(srgbRed: 0, green: 0, blue: 0, alpha: 0)] as CFArray
        let gradient = CGGradient(colorsSpace: colorSpace, colors: colors, locations: [0, 1])!
        var rng = SplitMix64(seed: 42)
        for _ in 0..<9 {
            let c = CGPoint(x: CGFloat.random(in: 150...370, using: &rng),
                            y: CGFloat.random(in: 115...165, using: &rng))
            let r = CGFloat.random(in: 60...110, using: &rng)
            ctx.drawRadialGradient(gradient, startCenter: c, startRadius: 0,
                                   endCenter: c, endRadius: r, options: [])
        }
        return ctx.makeImage()!
    }()

    static let snowflake: CGImage = {
        let ctx = context(24, 24)
        let colors = [CGColor(srgbRed: 1, green: 1, blue: 1, alpha: 1),
                      CGColor(srgbRed: 1, green: 1, blue: 1, alpha: 0)] as CFArray
        let gradient = CGGradient(colorsSpace: colorSpace, colors: colors, locations: [0.3, 1])!
        let c = CGPoint(x: 12, y: 12)
        ctx.drawRadialGradient(gradient, startCenter: c, startRadius: 0,
                               endCenter: c, endRadius: 12, options: [])
        return ctx.makeImage()!
    }()
}
