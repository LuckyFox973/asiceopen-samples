import AppKit
import QuartzCore

/// Vrstvy jednej obrazovky:
///
///   root
///   ├─ terrainLayer          statický obraz terénu + dekorácií (prelína sa pri zmene)
///   └─ animRoot              všetko animované; pozastavuje sa ako celok
///      ├─ waterGroup         (maska = voda) → 2× posúvajúca sa mriežka iskier
///      ├─ foamLayer          (maska = pena pri brehu) → pulzuje
///      ├─ cloudGroup         tiene oblakov plávajúce cez celú šírku
///      └─ snowLayer          sneženie (len téma Zima)
///
/// Animácie bežia v Core Animation (render server, GPU) – aplikácia sama
/// počas animácie nič neprepočítava.
final class SceneView: NSView {
    private let terrainLayer = CALayer()
    private let animRoot = CALayer()
    private let waterGroup = CALayer()
    private let waterMaskLayer = CALayer()
    private let foamLayer = CALayer()
    private let foamMaskLayer = CALayer()
    private let cloudGroup = CALayer()
    private var snowLayer: CAEmitterLayer?

    private(set) var theme: Theme
    private(set) var isAnimating = true

    init(frame: NSRect, theme: Theme) {
        self.theme = theme
        super.init(frame: frame)

        let root = CALayer()
        root.backgroundColor = theme.deep.cg()
        layer = root            // layer-hosting view: vrstvu nastavíme pred wantsLayer
        wantsLayer = true

        let none: [String: CAAction] = [
            "contents": NSNull(), "bounds": NSNull(), "position": NSNull(),
            "opacity": NSNull(), "backgroundColor": NSNull(), "sublayers": NSNull(),
        ]
        for l in [terrainLayer, animRoot, waterGroup, waterMaskLayer, foamLayer, foamMaskLayer, cloudGroup] {
            l.actions = none
            l.contentsGravity = .resize
        }

        root.addSublayer(terrainLayer)
        root.addSublayer(animRoot)
        animRoot.addSublayer(waterGroup)
        animRoot.addSublayer(foamLayer)
        animRoot.addSublayer(cloudGroup)
        waterGroup.mask = waterMaskLayer
        foamLayer.mask = foamMaskLayer

        rebuildAnimatedLayers()
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("init(coder:) sa nepoužíva") }

    // MARK: - Verejné API

    func setTheme(_ theme: Theme) {
        self.theme = theme
        layer?.backgroundColor = theme.deep.cg()
        rebuildAnimatedLayers()
    }

    /// Vymení obraz terénu a masky. Pri `animated` sa staré a nové plynulo prelínajú.
    func apply(_ scene: RenderedScene, animated: Bool) {
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        if animated {
            for l in [terrainLayer, waterMaskLayer, foamMaskLayer] {
                let fade = CATransition()
                fade.type = .fade
                fade.duration = 0.9
                fade.timingFunction = CAMediaTimingFunction(name: .easeInEaseOut)
                l.add(fade, forKey: kCATransition)
            }
        }
        terrainLayer.contents = scene.terrain
        waterMaskLayer.contents = scene.waterMask
        foamMaskLayer.contents = scene.foamMask
        CATransaction.commit()
    }

    /// Pozastavenie/obnovenie všetkých animácií bez straty ich fázy.
    func setAnimating(_ on: Bool) {
        guard on != isAnimating else { return }
        isAnimating = on
        if on {
            let pausedAt = animRoot.timeOffset
            animRoot.speed = 1
            animRoot.timeOffset = 0
            animRoot.beginTime = 0
            animRoot.beginTime = animRoot.convertTime(CACurrentMediaTime(), from: nil) - pausedAt
        } else {
            let now = animRoot.convertTime(CACurrentMediaTime(), from: nil)
            animRoot.speed = 0
            animRoot.timeOffset = now
        }
    }

    // MARK: - Stavba animovaných vrstiev

    private func rebuildAnimatedLayers() {
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        defer { CATransaction.commit() }

        let b = CGRect(origin: .zero, size: bounds.size)
        for l in [terrainLayer, animRoot, waterGroup, waterMaskLayer, foamLayer, foamMaskLayer, cloudGroup] {
            l.frame = b
        }
        waterGroup.sublayers?.forEach { $0.removeFromSuperlayer() }
        cloudGroup.sublayers?.forEach { $0.removeFromSuperlayer() }
        foamLayer.removeAllAnimations()
        snowLayer?.removeFromSuperlayer()
        snowLayer = nil

        // Pena pri brehu – „dýchanie“ vĺn
        foamLayer.backgroundColor = theme.foamColor
        let pulse = CABasicAnimation(keyPath: "opacity")
        pulse.fromValue = theme.foamOpacity.lowerBound
        pulse.toValue = theme.foamOpacity.upperBound
        pulse.duration = 3.2
        pulse.autoreverses = true
        pulse.repeatCount = .infinity
        pulse.timingFunction = CAMediaTimingFunction(name: .easeInEaseOut)
        throttle(pulse, fps: 20)
        foamLayer.add(pulse, forKey: "pulse")

        // Iskry na vode – dve mriežky idúce proti sebe, striedavo blikajúce
        if theme.sparkleOpacity > 0 {
            let t = SceneAssets.sparkleTileSize
            waterGroup.addSublayer(sparkleGrid(tile: SceneAssets.sparkleTile(seed: 7), drift: (1, 0),
                                               duration: 26, phase: 0, in: b, tileSize: t))
            waterGroup.addSublayer(sparkleGrid(tile: SceneAssets.sparkleTile(seed: 99), drift: (-1, 1),
                                               duration: 38, phase: 2.6, in: b, tileSize: t))
        }

        addClouds(in: b)

        if theme.snowfall {
            let snow = makeSnow(in: b)
            animRoot.addSublayer(snow)
            snowLayer = snow
        }
    }

    private func sparkleGrid(tile: CGImage, drift: (CGFloat, CGFloat), duration: CFTimeInterval,
                             phase: CFTimeInterval, in b: CGRect, tileSize t: CGFloat) -> CALayer {
        let nx = Int((b.width / t).rounded(.up)) + 2
        let ny = Int((b.height / t).rounded(.up)) + 2

        let tileLayer = CALayer()
        tileLayer.contents = tile
        tileLayer.frame = CGRect(x: 0, y: 0, width: t, height: t)

        let row = CAReplicatorLayer()
        row.frame = CGRect(x: 0, y: 0, width: CGFloat(nx) * t, height: t)
        row.instanceCount = nx
        row.instanceTransform = CATransform3DMakeTranslation(t, 0, 0)
        row.addSublayer(tileLayer)

        let grid = CAReplicatorLayer()
        grid.frame = CGRect(x: -t, y: -t, width: CGFloat(nx) * t, height: CGFloat(ny) * t)
        grid.instanceCount = ny
        grid.instanceTransform = CATransform3DMakeTranslation(0, t, 0)
        grid.addSublayer(row)

        // Posun presne o jednu dlaždicu → plynulá nekonečná slučka
        let move = CABasicAnimation(keyPath: "position")
        move.fromValue = NSValue(point: grid.position)
        move.toValue = NSValue(point: NSPoint(x: grid.position.x + drift.0 * t,
                                              y: grid.position.y + drift.1 * t))
        move.duration = duration
        move.repeatCount = .infinity
        throttle(move, fps: 24)
        grid.add(move, forKey: "drift")

        let twinkle = CABasicAnimation(keyPath: "opacity")
        twinkle.fromValue = theme.sparkleOpacity * 0.1
        twinkle.toValue = theme.sparkleOpacity
        twinkle.duration = 2.6
        twinkle.autoreverses = true
        twinkle.repeatCount = .infinity
        twinkle.timeOffset = phase
        twinkle.timingFunction = CAMediaTimingFunction(name: .easeInEaseOut)
        throttle(twinkle, fps: 20)
        grid.add(twinkle, forKey: "twinkle")
        return grid
    }

    /// Počet oblakov a dĺžka preletu rastú so šírkou – na ultrawide je rovnaká
    /// hustota aj rýchlosť ako na bežnom monitore, nič sa nenaťahuje.
    private func addClouds(in b: CGRect) {
        let cw = SceneAssets.cloudSize.width, ch = SceneAssets.cloudSize.height
        let count = max(3, Int(b.width / 520))
        var rng = SplitMix64(seed: UInt64(theme.seed) &* 31 &+ UInt64(b.width))
        for _ in 0..<count {
            let scale = CGFloat.random(in: 0.7...1.5, using: &rng)
            let l = CALayer()
            l.contents = SceneAssets.cloudShadow
            l.bounds = CGRect(x: 0, y: 0, width: cw * scale, height: ch * scale)
            l.position = CGPoint(x: -cw, y: CGFloat.random(in: 0...b.height, using: &rng))
            l.opacity = theme.cloudShadowOpacity

            let speed = CGFloat.random(in: 8...14, using: &rng)   // body za sekundu
            let a = CABasicAnimation(keyPath: "position.x")
            a.fromValue = -cw
            a.toValue = b.width + cw
            a.duration = CFTimeInterval((b.width + 2 * cw) / speed)
            a.repeatCount = .infinity
            a.timeOffset = Double.random(in: 0..<a.duration, using: &rng)
            throttle(a, fps: 15)
            l.add(a, forKey: "drift")
            cloudGroup.addSublayer(l)
        }
    }

    private func makeSnow(in b: CGRect) -> CAEmitterLayer {
        let e = CAEmitterLayer()
        e.frame = b
        e.emitterShape = .line
        e.emitterPosition = CGPoint(x: b.midX, y: b.height + 12)
        e.emitterSize = CGSize(width: b.width, height: 1)

        let cell = CAEmitterCell()
        cell.contents = SceneAssets.snowflake
        cell.birthRate = Float(b.width / 120)
        cell.lifetime = Float(b.height / 20) + 6
        cell.velocity = 24
        cell.velocityRange = 10
        cell.emissionLongitude = -.pi / 2
        cell.emissionRange = .pi / 10
        cell.xAcceleration = 2
        cell.yAcceleration = -1
        cell.scale = 0.3
        cell.scaleRange = 0.18
        cell.alphaRange = 0.4
        e.emitterCells = [cell]
        return e
    }

    /// Animácie nepotrebujú 120 Hz (ProMotion) – šetrí to GPU aj batériu.
    private func throttle(_ a: CAAnimation, fps: Float) {
        a.preferredFrameRateRange = CAFrameRateRange(minimum: fps / 2, maximum: fps, preferred: fps)
    }
}
