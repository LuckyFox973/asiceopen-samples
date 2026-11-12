import CoreGraphics

/// Bitmapy pre animácie, generované kódom (nič sa nenačítava zo súborov).
enum SceneAssets {
    static let scale: CGFloat = 2
    private static let colorSpace = CGColorSpace(name: CGColorSpace.sRGB)!

    /// Kreslí do kontextu s veľkosťou v bodoch (počiatok vľavo dole).
    private static func image(_ size: CGSize, scale: CGFloat = SceneAssets.scale, _ draw: (CGContext) -> Void) -> CGImage {
        let ctx = CGContext(data: nil, width: Int(size.width * scale), height: Int(size.height * scale),
                            bitsPerComponent: 8, bytesPerRow: 0, space: colorSpace,
                            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
        ctx.scaleBy(x: scale, y: scale)
        draw(ctx)
        return ctx.makeImage()!
    }

    private static func radial(_ ctx: CGContext, _ c: CGPoint, _ r: CGFloat, _ inner: CGColor, _ outer: CGColor, mid: CGFloat = 0) {
        let g = CGGradient(colorsSpace: colorSpace, colors: [inner, outer] as CFArray, locations: [mid, 1])!
        ctx.drawRadialGradient(g, startCenter: c, startRadius: 0, endCenter: c, endRadius: r, options: [])
    }

    private static func c(_ hex: UInt32, _ a: CGFloat = 1) -> CGColor { RGB(hex).cg(a) }

    // MARK: - Opakovateľný mäkký šum (svetlo na vode, poryvy vetra)

    static let noiseTileSize: CGFloat = 384

    /// Value noise na „toruse“ – dlaždica sa dá bez švu opakovať.
    private static func periodicNoise(_ x: Float, _ y: Float, period: Int32, seed: UInt32) -> Float {
        let xf = x.rounded(.down), yf = y.rounded(.down)
        let tx = smooth(x - xf), ty = smooth(y - yf)
        func wrap(_ v: Int32) -> Int32 { ((v % period) + period) % period }
        let ix = wrap(Int32(xf)), iy = wrap(Int32(yf)), jx = wrap(Int32(xf) + 1), jy = wrap(Int32(yf) + 1)
        let a = hash2(ix, iy, seed), b = hash2(jx, iy, seed), cc = hash2(ix, jy, seed), d = hash2(jx, jy, seed)
        let top = a + (b - a) * tx, bottom = cc + (d - cc) * tx
        return top + (bottom - top) * ty
    }

    /// Biele mäkké škvrny s danou priehľadnosťou; `stretch` > 1 = pretiahnuté (vietor).
    static func noiseTile(seed: UInt32, stretch: Float = 1, threshold: (Float, Float) = (0.5, 0.78)) -> CGImage {
        let px = 192                                         // 0.5 px/bod – je to mäkké, viac netreba
        let ctx = CGContext(data: nil, width: px, height: px, bitsPerComponent: 8, bytesPerRow: px * 4,
                            space: colorSpace, bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
        let buf = ctx.data!.assumingMemoryBound(to: UInt8.self)
        let base: Float = 4                                  // počet buniek šumu na dlaždicu
        for j in 0..<px {
            for i in 0..<px {
                let u = Float(i) / Float(px) * base, v = Float(j) / Float(px) * base
                var n: Float = 0, amp: Float = 0.5, norm: Float = 0, f: Float = 1
                for o in 0..<3 {
                    let p = Int32(base * f)                          // perióda v bunkách (os y sa pri vetre opakuje častejšie)
                    n += amp * periodicNoise(u * f, v * f * stretch, period: p, seed: seed &+ UInt32(o))
                    norm += amp; amp *= 0.5; f *= 2
                }
                n /= norm
                let a = UInt8(clamp01(sstep(threshold.0, threshold.1, n)) * 255)
                let o = (j * px + i) * 4
                buf[o] = a; buf[o + 1] = a; buf[o + 2] = a; buf[o + 3] = a
            }
        }
        return ctx.makeImage()!
    }

    // MARK: - Drobnosti

    /// Mäkká bodka – trblietka na vode.
    static let softDot: CGImage = image(CGSize(width: 6, height: 6)) { ctx in
        radial(ctx, CGPoint(x: 3, y: 3), 3, c(0xFFFFFF), c(0xFFFFFF, 0), mid: 0.25)
    }

    static let snowflake: CGImage = image(CGSize(width: 10, height: 10)) { ctx in
        radial(ctx, CGPoint(x: 5, y: 5), 5, c(0xFFFFFF, 0.95), c(0xFFFFFF, 0), mid: 0.35)
    }

    static let snowflakeSoft: CGImage = image(CGSize(width: 18, height: 18)) { ctx in
        radial(ctx, CGPoint(x: 9, y: 9), 9, c(0xFFFFFF, 0.7), c(0xFFFFFF, 0), mid: 0.1)
    }

    // MARK: - Tiene oblakov

    static let cloudSize = CGSize(width: 560, height: 300)

    /// Mäkké tiene oblakov (tri tvary).
    static let cloudShadows: [CGImage] = ([42, 7, 99] as [UInt64]).map { seed in
        image(cloudSize, scale: 1) { ctx in
            var rng = SplitMix64(seed: seed)
            let g = CGGradient(colorsSpace: colorSpace,
                               colors: [c(0x1A2A3A, 0.14), c(0x1A2A3A, 0.10), c(0x1A2A3A, 0)] as CFArray,
                               locations: [0, 0.5, 1])!
            for i in 0..<16 {
                let t = CGFloat(i) / 15
                let bell = sin(t * .pi)
                let p = CGPoint(x: 90 + t * 380 + CGFloat.random(in: -20...20, using: &rng),
                                y: 120 + bell * 40 + CGFloat.random(in: -14...14, using: &rng))
                let r = 40 + 50 * bell * CGFloat.random(in: 0.75...1.1, using: &rng)
                ctx.drawRadialGradient(g, startCenter: p, startRadius: 0, endCenter: p, endRadius: r, options: [])
            }
        }
    }
}
