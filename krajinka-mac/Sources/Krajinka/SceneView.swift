import AppKit
import QuartzCore

/// Vrstvy jednej obrazovky (zdola nahor):
///
///   terrainLayer        maľovaný obraz (prelína sa pri zmene ikon)
///   animRoot            všetko živé – pozastavuje sa ako celok
///   ├─ waterGroup       (maska: voda) pomaly plávajúce svetlo na hladine + trblietky
///   ├─ shoreFoamLayer   (maska: pena pri brehu) pokojne „dýcha“
///   ├─ foamLinesLayer   (maska: línie peny) dýcha s posunutou fázou → vlnenie
///   ├─ windGroup        poryvy vetra – svetlo prebiehajúce trávou
///   ├─ cloudShadows     mäkké tiene oblakov
///   └─ weatherGroup     sneh
///
/// Všetko je pomalé a jemné; Core Animation to počíta na GPU.
final class SceneView: NSView {
    private let terrainLayer = CALayer()
    private let animRoot = CALayer()
    private let waterGroup = CALayer()
    private let waterMaskLayer = CALayer()
    private let shoreFoamLayer = CALayer()
    private let shoreFoamMask = CALayer()
    private let foamLinesLayer = CALayer()
    private let foamLinesMask = CALayer()
    private let windGroup = CALayer()
    private let cloudShadows = CALayer()
    private let weatherGroup = CALayer()

    private(set) var theme: Theme
    private(set) var isAnimating = true
    private var rng = SplitMix64(seed: 1)

    private static let noActions: [String: CAAction] = [
        "contents": NSNull(), "bounds": NSNull(), "position": NSNull(), "frame": NSNull(),
        "opacity": NSNull(), "backgroundColor": NSNull(), "sublayers": NSNull(), "transform": NSNull(),
    ]

    private var allLayers: [CALayer] {
        [terrainLayer, animRoot, waterGroup, waterMaskLayer, shoreFoamLayer, shoreFoamMask,
         foamLinesLayer, foamLinesMask, windGroup, cloudShadows, weatherGroup]
    }

    init(frame: NSRect, theme: Theme) {
        self.theme = theme
        super.init(frame: frame)
        let root = CALayer()
        root.backgroundColor = theme.base.cg()
        layer = root            // layer-hosting view: vrstvu nastavíme pred wantsLayer
        wantsLayer = true

        for l in allLayers {
            l.actions = Self.noActions
            l.contentsGravity = .resize
        }
        root.addSublayer(terrainLayer)
        root.addSublayer(animRoot)
        waterGroup.mask = waterMaskLayer
        shoreFoamLayer.mask = shoreFoamMask
        foamLinesLayer.mask = foamLinesMask
        for l in [waterGroup, shoreFoamLayer, foamLinesLayer, windGroup, cloudShadows, weatherGroup] {
            animRoot.addSublayer(l)
        }
        rebuildAnimatedLayers()
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("init(coder:) sa nepoužíva") }

    // MARK: - Verejné API

    func setTheme(_ theme: Theme) {
        self.theme = theme
        layer?.backgroundColor = theme.base.cg()
        rebuildAnimatedLayers()
    }

    /// Vymení obraz a masky. Pri `animated` sa staré a nové plynulo prelínajú.
    func apply(_ scene: RenderedScene, animated: Bool) {
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        if animated {
            for l in [terrainLayer, waterMaskLayer, shoreFoamMask, foamLinesMask] {
                let fade = CATransition()
                fade.type = .fade
                fade.duration = 1.0
                fade.timingFunction = CAMediaTimingFunction(name: .easeInEaseOut)
                l.add(fade, forKey: kCATransition)
            }
        }
        terrainLayer.contents = scene.terrain
        waterMaskLayer.contents = scene.waterMask
        shoreFoamMask.contents = scene.shoreFoamMask
        foamLinesMask.contents = scene.foamLinesMask
        CATransaction.commit()
    }

    /// Pozastavenie/obnovenie všetkých animácií bez straty fázy.
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

    // MARK: - Stavba

    private func rand(_ r: ClosedRange<CGFloat>) -> CGFloat { CGFloat.random(in: r, using: &rng) }

    private func rebuildAnimatedLayers() {
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        defer { CATransaction.commit() }

        let b = CGRect(origin: .zero, size: bounds.size)
        rng = SplitMix64(seed: UInt64(theme.seed) &* 7919 &+ UInt64(b.width) &* 31 &+ UInt64(b.height))
        for l in allLayers { l.frame = b }
        for g in [waterGroup, windGroup, cloudShadows, weatherGroup] {
            g.sublayers?.forEach { $0.removeFromSuperlayer() }
        }
        shoreFoamLayer.removeAllAnimations()
        foamLinesLayer.removeAllAnimations()
        shoreFoamLayer.opacity = 0
        foamLinesLayer.opacity = 0

        if theme.hasSea { setUpSea(in: b) }
        if theme.wind { setUpWind(in: b) }
        addCloudShadows(in: b)
        if theme.snowfall { weatherGroup.addSublayer(makeSnow(in: b)) }
    }

    // MARK: More

    private func setUpSea(in b: CGRect) {
        // pena: pri brehu a línie ďalej od brehu dýchajú s posunutou fázou → dojem vlnenia
        shoreFoamLayer.backgroundColor = RGB.white.cg()
        shoreFoamLayer.add(breathe(from: 0.15, to: 0.85, period: 5.5, phase: 0), forKey: "breathe")
        foamLinesLayer.backgroundColor = RGB.white.cg()
        foamLinesLayer.add(breathe(from: 0.05, to: 0.7, period: 7.5, phase: 3.2), forKey: "breathe")

        // svetlo na hladine: dve vrstvy mäkkých škvŕn plávajúce rôznym smerom
        let t = SceneAssets.noiseTileSize
        waterGroup.addSublayer(tileGrid(SceneAssets.noiseTile(seed: 21), tile: t, in: b,
                                        drift: (1, 0), duration: 140, opacity: 0.16))
        waterGroup.addSublayer(tileGrid(SceneAssets.noiseTile(seed: 57, threshold: (0.55, 0.85)), tile: t * 0.75, in: b,
                                        drift: (-1, 1), duration: 110, opacity: 0.10))
        waterGroup.addSublayer(makeSparkles(in: b))
    }

    /// Mäkké trblietky: bodka vznikne drobná, narastie a zmizne.
    private func makeSparkles(in b: CGRect) -> CAEmitterLayer {
        let e = CAEmitterLayer()
        e.frame = b
        e.emitterShape = .rectangle
        e.emitterMode = .surface
        e.emitterPosition = CGPoint(x: b.midX, y: b.midY)
        e.emitterSize = b.size
        let cell = CAEmitterCell()
        cell.contents = SceneAssets.softDot
        cell.contentsScale = SceneAssets.scale
        cell.birthRate = Float(b.width * b.height / 14_000)
        cell.lifetime = 1.6
        cell.lifetimeRange = 0.6
        cell.scale = 0.05
        cell.scaleSpeed = 0.32
        cell.alphaSpeed = -0.6
        cell.velocity = 0
        e.emitterCells = [cell]
        return e
    }

    // MARK: Vietor

    /// Poryvy vetra: pretiahnuté svetlé škvrny pomaly prebiehajú krajinou.
    private func setUpWind(in b: CGRect) {
        let t = SceneAssets.noiseTileSize
        windGroup.addSublayer(tileGrid(SceneAssets.noiseTile(seed: 88, stretch: 3, threshold: (0.55, 0.85)), tile: t, in: b,
                                       drift: (1, 0), duration: 34, opacity: 0.09))
    }

    // MARK: Oblaky

    private func addCloudShadows(in b: CGRect) {
        guard theme.cloudShadowOpacity > 0 else { return }
        let size = SceneAssets.cloudSize
        let count = max(2, Int(b.width / 760))
        for _ in 0..<count {
            let k = rand(0.9...1.6)
            let w = size.width * k, h = size.height * k
            let l = CALayer()
            l.actions = Self.noActions
            l.contents = SceneAssets.cloudShadows[Int(rand(0...2.99))]
            l.bounds = CGRect(x: 0, y: 0, width: w, height: h)
            l.position = CGPoint(x: -w, y: rand(0...b.height))
            l.opacity = theme.cloudShadowOpacity * 7          // sprite má zapečenú priehľadnosť ~0.14
            let speed = rand(5...9)
            let a = CABasicAnimation(keyPath: "position.x")
            a.fromValue = -w
            a.toValue = b.width + w
            a.duration = CFTimeInterval((b.width + 2 * w) / speed)
            a.repeatCount = .infinity
            a.timeOffset = Double(rand(0...1)) * a.duration
            throttle(a, fps: 15)
            l.add(a, forKey: "drift")
            cloudShadows.addSublayer(l)
        }
    }

    // MARK: Sneh

    private func makeSnow(in b: CGRect) -> CAEmitterLayer {
        let e = CAEmitterLayer()
        e.frame = b
        e.emitterShape = .line
        e.emitterPosition = CGPoint(x: b.midX, y: b.height + 16)
        e.emitterSize = CGSize(width: b.width * 1.1, height: 1)

        let far = CAEmitterCell()
        far.contents = SceneAssets.snowflake
        far.contentsScale = SceneAssets.scale
        far.birthRate = Float(b.width / 160)
        far.lifetime = Float(b.height / 12) + 6
        far.velocity = 14
        far.velocityRange = 6
        far.emissionLongitude = -.pi / 2
        far.emissionRange = .pi / 10
        far.xAcceleration = 2
        far.scale = 0.35
        far.scaleRange = 0.15
        far.alphaRange = 0.3

        let near = CAEmitterCell()
        near.contents = SceneAssets.snowflakeSoft
        near.contentsScale = SceneAssets.scale
        near.birthRate = Float(b.width / 700)
        near.lifetime = Float(b.height / 22) + 4
        near.velocity = 26
        near.velocityRange = 8
        near.emissionLongitude = -.pi / 2
        near.emissionRange = .pi / 9
        near.xAcceleration = 4
        near.scale = 0.7
        near.scaleRange = 0.25
        near.alphaRange = 0.3

        e.emitterCells = [far, near]
        return e
    }

    // MARK: Pomôcky

    private func breathe(from: Float, to: Float, period: CFTimeInterval, phase: CFTimeInterval) -> CABasicAnimation {
        let a = CABasicAnimation(keyPath: "opacity")
        a.fromValue = from
        a.toValue = to
        a.duration = period / 2
        a.autoreverses = true
        a.repeatCount = .infinity
        a.timeOffset = phase
        a.timingFunction = CAMediaTimingFunction(name: .easeInEaseOut)
        throttle(a, fps: 20)
        return a
    }

    /// Mriežka opakovanej dlaždice posúvaná presne o jednu dlaždicu → nekonečná slučka bez švu.
    private func tileGrid(_ image: CGImage, tile t: CGFloat, in b: CGRect, drift: (CGFloat, CGFloat),
                          duration: CFTimeInterval, opacity: Float) -> CALayer {
        let nx = Int((b.width / t).rounded(.up)) + 2
        let ny = Int((b.height / t).rounded(.up)) + 2
        let tileLayer = CALayer()
        tileLayer.contents = image
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
        grid.opacity = opacity
        let move = CABasicAnimation(keyPath: "position")
        move.fromValue = NSValue(point: grid.position)
        move.toValue = NSValue(point: NSPoint(x: grid.position.x + drift.0 * t, y: grid.position.y + drift.1 * t))
        move.duration = duration
        move.repeatCount = .infinity
        throttle(move, fps: 24)
        grid.add(move, forKey: "drift")
        return grid
    }

    /// Animácie nepotrebujú 120 Hz (ProMotion) – šetrí to GPU aj batériu.
    private func throttle(_ a: CAAnimation, fps: Float) {
        a.preferredFrameRateRange = CAFrameRateRange(minimum: fps / 2, maximum: fps, preferred: fps)
    }
}
