import AppKit
import QuartzCore

/// Vrstvy jednej obrazovky (zdola nahor):
///
///   terrainLayer        statický obraz: terén, chodníky, mosty, flóra (prelína sa pri zmene)
///   animRoot            všetko živé – pozastavuje sa ako celok
///   ├─ waterGroup       (maska: voda) iskry posúvajúce sa po hladine + záblesky slnka
///   ├─ waveLayers ×4    (masky: prstence v hĺbke 6/12/19/27 bodov) → vlny bežiace k brehu
///   ├─ foamLayer        (maska: pás pri brehu) – vlna „dobehne“ a spení sa
///   ├─ swimGroup        plachetnice / kačky krúžiace po vode
///   ├─ cloudShadows     tiene oblakov na zemi
///   ├─ birdShadows      tiene vtákov
///   ├─ critterGroup     motýle
///   ├─ birdGroup        kŕdle vtákov
///   ├─ cloudWhites      samotné oblaky (posunuté od tieňa smerom k svetlu)
///   └─ weatherGroup     sneh, trblietky na snehu
///
/// Animácie počíta Core Animation (render server, GPU); aplikácia sama počas
/// behu nič neprepočítava.
final class SceneView: NSView {
    private let terrainLayer = CALayer()
    private let animRoot = CALayer()
    private let waterGroup = CALayer()
    private let waterMaskLayer = CALayer()
    private var waveLayers: [CALayer] = []
    private var waveMaskLayers: [CALayer] = []
    private let foamLayer = CALayer()
    private let foamMaskLayer = CALayer()
    private let swimGroup = CALayer()
    private let carGroup = CALayer()
    private let cloudShadows = CALayer()
    private let birdShadows = CALayer()
    private let critterGroup = CALayer()
    private let birdGroup = CALayer()
    private let cloudWhites = CALayer()
    private let weatherGroup = CALayer()

    private var swimmers: [(spot: CGPoint, layer: CALayer)] = []
    private var swimSpots: [CGPoint] = []

    private(set) var theme: Theme
    private(set) var isAnimating = true

    private static let noActions: [String: CAAction] = [
        "contents": NSNull(), "bounds": NSNull(), "position": NSNull(), "frame": NSNull(),
        "opacity": NSNull(), "backgroundColor": NSNull(), "sublayers": NSNull(), "transform": NSNull(),
    ]

    init(frame: NSRect, theme: Theme) {
        self.theme = theme
        super.init(frame: frame)

        let root = CALayer()
        root.backgroundColor = theme.deep.cg()
        layer = root            // layer-hosting view: vrstvu nastavíme pred wantsLayer
        wantsLayer = true

        for _ in Waves.depths {
            waveLayers.append(CALayer())
            waveMaskLayers.append(CALayer())
        }

        let all = [terrainLayer, animRoot, waterGroup, waterMaskLayer, foamLayer, foamMaskLayer, swimGroup, carGroup,
                   cloudShadows, birdShadows, critterGroup, birdGroup, cloudWhites, weatherGroup]
            + waveLayers + waveMaskLayers
        for l in all {
            l.actions = Self.noActions
            l.contentsGravity = .resize
        }

        root.addSublayer(terrainLayer)
        root.addSublayer(animRoot)
        animRoot.addSublayer(waterGroup)
        waterGroup.mask = waterMaskLayer
        for (wave, mask) in zip(waveLayers, waveMaskLayers) {
            wave.mask = mask
            animRoot.addSublayer(wave)
        }
        animRoot.addSublayer(foamLayer)
        foamLayer.mask = foamMaskLayer
        for l in [swimGroup, carGroup, cloudShadows, birdShadows, critterGroup, birdGroup, cloudWhites, weatherGroup] {
            animRoot.addSublayer(l)
        }

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
        let targets = [terrainLayer, waterMaskLayer, foamMaskLayer] + waveMaskLayers
        if animated {
            for l in targets {
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
        for (mask, image) in zip(waveMaskLayers, scene.waveMasks) { mask.contents = image }
        CATransaction.commit()

        updateSwimmers(scene.swimSpots)
        updateCars(scene.carRoutes)
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

    // MARK: - Stavba

    private var rng = SplitMix64(seed: 1)

    private func rand(_ r: ClosedRange<CGFloat>) -> CGFloat { CGFloat.random(in: r, using: &rng) }

    private func rebuildAnimatedLayers() {
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        defer { CATransaction.commit() }

        let b = CGRect(origin: .zero, size: bounds.size)
        rng = SplitMix64(seed: UInt64(theme.seed) &* 7919 &+ UInt64(b.width) &* 31 &+ UInt64(b.height))

        let frames = [terrainLayer, animRoot, waterGroup, waterMaskLayer, foamLayer, foamMaskLayer, swimGroup, carGroup,
                      cloudShadows, birdShadows, critterGroup, birdGroup, cloudWhites, weatherGroup]
            + waveLayers + waveMaskLayers
        for l in frames { l.frame = b }
        for g in [waterGroup, swimGroup, carGroup, cloudShadows, birdShadows, critterGroup, birdGroup, cloudWhites, weatherGroup] {
            g.sublayers?.forEach { $0.removeFromSuperlayer() }
        }
        (waveLayers + [foamLayer]).forEach { $0.removeAllAnimations() }
        swimmers = []
        carRoutes = []

        setUpWaves()
        setUpWater(in: b)
        addClouds(in: b)
        addBirds(in: b)
        if theme.butterflies { addButterflies(in: b) }
        if theme.snowfall { weatherGroup.addSublayer(makeSnow(in: b)) }
        if theme.snowGlints > 0 {
            weatherGroup.addSublayer(makeGlints(in: b, density: theme.snowGlints, size: 0.22))
        }
        updateSwimmers(swimSpots, force: true)
    }

    // MARK: Vlny

    /// Štyri prstence sa rozsvecujú postupne zvonka dnu → vlna beží k brehu,
    /// potom sa spení pás pri brehu. Celý cyklus trvá `wavePeriod`.
    private func setUpWaves() {
        let strength = theme.waveStrength
        let period = theme.wavePeriod
        for (k, wave) in waveLayers.enumerated() {
            wave.backgroundColor = theme.waveColor.cg()
            wave.opacity = 0
            guard strength > 0 else { continue }
            let peak = strength * 0.85
            let a = CAKeyframeAnimation(keyPath: "opacity")
            a.values = [0, peak, 0, 0].map { NSNumber(value: $0) }
            a.keyTimes = [0, 0.18, 0.42, 1]
            a.timingFunctions = [CAMediaTimingFunction(name: .easeOut), CAMediaTimingFunction(name: .easeIn),
                                 CAMediaTimingFunction(name: .linear)]
            a.duration = period
            a.repeatCount = .infinity
            a.timeOffset = period * (1 - Waves.phases[k]).truncatingRemainder(dividingBy: 1)
            throttle(a, fps: 24)
            wave.add(a, forKey: "wave")
        }

        foamLayer.backgroundColor = theme.waveColor.cg()
        foamLayer.opacity = 0
        guard strength > 0 else { return }
        let peak = strength * 0.9
        let f = CAKeyframeAnimation(keyPath: "opacity")
        f.values = [0.35 * peak, peak, 0.35 * peak, 0.35 * peak].map { NSNumber(value: $0) }
        f.keyTimes = [0, 0.12, 0.5, 1]
        f.duration = period
        f.repeatCount = .infinity
        f.timeOffset = period * (1 - Waves.foamPhase)
        throttle(f, fps: 24)
        foamLayer.add(f, forKey: "foam")
    }

    // MARK: Hladina

    private func setUpWater(in b: CGRect) {
        if theme.shimmerOpacity > 0 {
            let t = SceneAssets.sparkleTileSize
            waterGroup.addSublayer(sparkleGrid(tile: SceneAssets.sparkleTile(seed: 7), drift: (1, 0),
                                               duration: 26, phase: 0, in: b, tileSize: t))
            waterGroup.addSublayer(sparkleGrid(tile: SceneAssets.sparkleTile(seed: 99), drift: (-1, 1),
                                               duration: 38, phase: 2.6, in: b, tileSize: t))
        }
        if theme.waterGlints > 0 {
            waterGroup.addSublayer(makeGlints(in: b, density: theme.waterGlints, size: 0.35))
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

        // posun presne o jednu dlaždicu → nekonečná slučka bez švu
        let move = CABasicAnimation(keyPath: "position")
        move.fromValue = NSValue(point: grid.position)
        move.toValue = NSValue(point: NSPoint(x: grid.position.x + drift.0 * t, y: grid.position.y + drift.1 * t))
        move.duration = duration
        move.repeatCount = .infinity
        throttle(move, fps: 24)
        grid.add(move, forKey: "drift")

        let twinkle = CABasicAnimation(keyPath: "opacity")
        twinkle.fromValue = theme.shimmerOpacity * 0.15
        twinkle.toValue = theme.shimmerOpacity
        twinkle.duration = 2.6
        twinkle.autoreverses = true
        twinkle.repeatCount = .infinity
        twinkle.timeOffset = phase
        twinkle.timingFunction = CAMediaTimingFunction(name: .easeInEaseOut)
        throttle(twinkle, fps: 20)
        grid.add(twinkle, forKey: "twinkle")
        return grid
    }

    /// Náhodné krátke záblesky (hviezdičky). Hustota sa počíta na plochu,
    /// takže ultrawide nemá „redšie“ trblietanie.
    private func makeGlints(in b: CGRect, density: Float, size: CGFloat) -> CAEmitterLayer {
        let e = CAEmitterLayer()
        e.frame = b
        e.emitterShape = .rectangle
        e.emitterMode = .surface
        e.emitterPosition = CGPoint(x: b.midX, y: b.midY)
        e.emitterSize = b.size

        let cell = CAEmitterCell()
        cell.contents = SceneAssets.glint
        cell.contentsScale = SceneAssets.scale
        cell.birthRate = Float(b.width * b.height / 30_000) * density
        cell.lifetime = 0.9
        cell.lifetimeRange = 0.4
        cell.scale = size
        cell.scaleRange = size * 0.35
        cell.scaleSpeed = -size * 0.5
        cell.alphaSpeed = -1.0
        cell.spin = 0.6
        cell.spinRange = 0.6
        cell.velocity = 0
        e.emitterCells = [cell]
        return e
    }

    // MARK: Oblaky

    /// Tieň na zemi + biely závoj o kus bližšie k svetlu – dávajú scéne hĺbku.
    /// Počet a dĺžka preletu rastú so šírkou: na ultrawide rovnaká hustota aj rýchlosť.
    private func addClouds(in b: CGRect) {
        let size = SceneAssets.cloudSize
        let count = max(3, Int(b.width / 620 * theme.cloudCountFactor))
        let lift = CGPoint(x: -70, y: 110)
        for _ in 0..<count {
            let k = rand(0.75...1.45)
            let w = size.width * k, h = size.height * k
            let y = rand(0...b.height)
            let speed = rand(7...13)
            let duration = CFTimeInterval((b.width + 2 * w + abs(lift.x)) / speed)
            let offset = Double(rand(0...1)) * duration

            let variant = SceneAssets.clouds[Int(rand(0...2.99))]
            for (group, image, opacity, shift) in [
                (cloudShadows, variant.shadow, theme.cloudShadowOpacity, CGPoint.zero),
                (cloudWhites, variant.white, theme.cloudWhiteOpacity, lift),
            ] {
                guard opacity > 0 else { continue }
                let l = CALayer()
                l.actions = Self.noActions
                l.contents = image
                l.bounds = CGRect(x: 0, y: 0, width: w, height: h)
                l.position = CGPoint(x: -w, y: y + shift.y)
                l.opacity = opacity
                let a = CABasicAnimation(keyPath: "position.x")
                a.fromValue = -w + shift.x
                a.toValue = b.width + w + shift.x
                a.duration = duration
                a.repeatCount = .infinity
                a.timeOffset = offset
                throttle(a, fps: 15)
                l.add(a, forKey: "drift")
                group.addSublayer(l)
            }
        }
    }

    // MARK: Vtáky

    /// Kŕdeľ v tvare V raz za čas preletí cez obrazovku; tieň letí s odstupom
    /// po zemi. Medzi preletmi je pauza, aby to nepôsobilo rušivo.
    private func addBirds(in b: CGRect) {
        guard theme.birds != .none else { return }
        let frames = [SceneAssets.bird(theme.birds, wingsUp: false), SceneAssets.bird(theme.birds, wingsUp: true)]
        let flap: CFTimeInterval = theme.birds == .gulls ? 0.9 : 0.32
        let speed: CGFloat = theme.birds == .gulls ? 70 : 95
        let flocks = max(1, Int(b.width / 2200))
        let shadowShift = CGPoint(x: 45, y: -70)

        for f in 0..<flocks {
            let n = Int(rand(3...5.99))
            let ltr = f % 2 == 0
            let y0 = rand(b.height * 0.15...b.height * 0.85)
            let y1 = min(max(y0 + rand(-260...260), 40), b.height - 40)
            let x0: CGFloat = ltr ? -150 : b.width + 150
            let x1: CGFloat = ltr ? b.width + 150 : -150
            let path = CGMutablePath()
            path.move(to: CGPoint(x: x0, y: y0))
            path.addCurve(to: CGPoint(x: x1, y: y1),
                          control1: CGPoint(x: x0 + (x1 - x0) * 0.33, y: y0 + rand(-180...180)),
                          control2: CGPoint(x: x0 + (x1 - x0) * 0.66, y: y1 + rand(-180...180)))
            let travel = CFTimeInterval((b.width + 300) / speed)
            let cycle = travel + Double(rand(40...110))
            let offset = Double(rand(0...1)) * cycle

            for (target, isShadow) in [(birdShadows, true), (birdGroup, false)] {
                let flock = CALayer()
                flock.actions = Self.noActions
                flock.bounds = .zero
                flock.position = CGPoint(x: -1000, y: -1000)   // počas pauzy mimo obrazovky
                for i in 0..<n {
                    let bird = CALayer()
                    bird.actions = Self.noActions
                    let side: CGFloat = i % 2 == 0 ? 1 : -1
                    let pos = CGPoint(x: -CGFloat((i + 1) / 2) * 24 + rand(-4...4),
                                      y: side * CGFloat((i + 1) / 2) * 16 + rand(-3...3))
                    if isShadow {
                        bird.contents = SceneAssets.birdShadow
                        bird.bounds = CGRect(x: 0, y: 0, width: 30, height: 26)
                    } else {
                        bird.contents = frames[0]
                        bird.bounds = CGRect(origin: .zero, size: SceneAssets.birdSize)
                        let wings = CAKeyframeAnimation(keyPath: "contents")
                        wings.values = frames
                        wings.calculationMode = .discrete
                        wings.duration = flap * Double(rand(0.85...1.15))
                        wings.repeatCount = .infinity
                        wings.timeOffset = Double(rand(0...1))
                        bird.add(wings, forKey: "flap")
                    }
                    bird.position = pos
                    flock.addSublayer(bird)
                }

                var shift = CGAffineTransform.identity
                if isShadow { shift = CGAffineTransform(translationX: shadowShift.x, y: shadowShift.y) }
                let fly = CAKeyframeAnimation(keyPath: "position")
                fly.path = path.copy(using: &shift)
                fly.rotationMode = .rotateAuto
                fly.calculationMode = .paced
                fly.duration = travel
                let flight = CAAnimationGroup()
                flight.animations = [fly]
                flight.duration = cycle
                flight.repeatCount = .infinity
                flight.timeOffset = offset
                throttle(flight, fps: 30)
                flock.add(flight, forKey: "flight")
                target.addSublayer(flock)
            }
        }
    }

    // MARK: Motýle

    private func addButterflies(in b: CGRect) {
        let colors: [UInt32] = [0xF29E38, 0xFFFFFF, 0x6FA8DC, 0xF2D16B]
        let count = max(2, Int(b.width / 1100))
        for i in 0..<count {
            let color = colors[i % colors.count]
            let frames = [SceneAssets.butterfly(color: color, open: true), SceneAssets.butterfly(color: color, open: false)]
            let l = CALayer()
            l.actions = Self.noActions
            l.contents = frames[0]
            l.bounds = CGRect(origin: .zero, size: SceneAssets.butterflySize)
            let wings = CAKeyframeAnimation(keyPath: "contents")
            wings.values = frames
            wings.calculationMode = .discrete
            wings.duration = 0.22
            wings.repeatCount = .infinity
            wings.timeOffset = Double(rand(0...1))
            l.add(wings, forKey: "flap")

            // uzavretá hladká slučka cez náhodné body okolo jedného miesta
            let center = CGPoint(x: rand(80...max(81, b.width - 80)), y: rand(80...max(81, b.height - 80)))
            let pts: [CGPoint] = (0..<7).map { k in
                let a = CGFloat(k) / 7 * 2 * .pi + rand(-0.3...0.3)
                let r = rand(90...260)
                return CGPoint(x: min(max(center.x + cos(a) * r, 20), b.width - 20),
                               y: min(max(center.y + sin(a) * r * 0.7, 20), b.height - 20))
            }
            let fly = CAKeyframeAnimation(keyPath: "position")
            fly.path = Self.closedCatmullRom(pts)
            fly.rotationMode = .rotateAuto
            fly.calculationMode = .cubicPaced
            fly.duration = Double(rand(45...75))
            fly.repeatCount = .infinity
            fly.timeOffset = Double(rand(0...1)) * fly.duration
            throttle(fly, fps: 30)
            l.position = pts[0]
            l.add(fly, forKey: "flutter")
            critterGroup.addSublayer(l)
        }
    }

    private static func closedCatmullRom(_ p: [CGPoint]) -> CGPath {
        let path = CGMutablePath()
        let n = p.count
        path.move(to: p[0])
        for i in 0..<n {
            let p0 = p[(i - 1 + n) % n], p1 = p[i], p2 = p[(i + 1) % n], p3 = p[(i + 2) % n]
            let c1 = CGPoint(x: p1.x + (p2.x - p0.x) / 6, y: p1.y + (p2.y - p0.y) / 6)
            let c2 = CGPoint(x: p2.x - (p3.x - p1.x) / 6, y: p2.y - (p3.y - p1.y) / 6)
            path.addCurve(to: p2, control1: c1, control2: c2)
        }
        path.closeSubpath()
        return path
    }

    // MARK: Plachetnice a kačky

    /// Zachová lodičky, ktorých miesto ostalo vodou; nové sa jemne objavia.
    private func updateSwimmers(_ spots: [CGPoint], force: Bool = false) {
        swimSpots = spots
        guard theme.swimmers != .none else {
            swimmers.forEach { $0.layer.removeFromSuperlayer() }
            swimmers = []
            return
        }
        var kept: [(spot: CGPoint, layer: CALayer)] = []
        for s in swimmers {
            if !force, spots.contains(where: { hypot($0.x - s.spot.x, $0.y - s.spot.y) < 40 }) {
                kept.append(s)
            } else {
                s.layer.removeFromSuperlayer()
            }
        }
        for spot in spots where !kept.contains(where: { hypot($0.spot.x - spot.x, $0.spot.y - spot.y) < 40 }) {
            let l = makeSwimmer(at: spot)
            let appear = CABasicAnimation(keyPath: "opacity")
            appear.fromValue = 0
            appear.toValue = 1
            appear.duration = 1.5
            l.add(appear, forKey: "appear")
            swimGroup.addSublayer(l)
            kept.append((spot, l))
        }
        swimmers = kept
    }

    private func makeSwimmer(at spot: CGPoint) -> CALayer {
        let container = CALayer()
        container.actions = Self.noActions
        container.bounds = .zero
        container.position = spot
        let radius: CGFloat
        let duration: CFTimeInterval

        switch theme.swimmers {
        case .sailboats, .none:
            radius = 46
            duration = Double(rand(140...190))
            let wake = CALayer()
            wake.actions = Self.noActions
            wake.contents = SceneAssets.wake
            wake.bounds = CGRect(origin: .zero, size: SceneAssets.wakeSize)
            wake.position = CGPoint(x: -SceneAssets.wakeSize.width / 2 - 8, y: 0)
            container.addSublayer(wake)
            let boat = CALayer()
            boat.actions = Self.noActions
            boat.contents = SceneAssets.boat
            boat.bounds = CGRect(origin: .zero, size: SceneAssets.boatSize)
            boat.position = .zero
            let bob = CABasicAnimation(keyPath: "transform.rotation.z")
            bob.fromValue = -0.05
            bob.toValue = 0.05
            bob.duration = 3.4
            bob.autoreverses = true
            bob.repeatCount = .infinity
            bob.timingFunction = CAMediaTimingFunction(name: .easeInEaseOut)
            throttle(bob, fps: 20)
            boat.add(bob, forKey: "bob")
            container.addSublayer(boat)
        case .ducks:
            radius = 30
            duration = Double(rand(100...140))
            let offsets: [CGFloat] = [0, -15, -25, -34]
            for (i, x) in offsets.enumerated() {
                let d = CALayer()
                d.actions = Self.noActions
                d.contents = SceneAssets.duck(baby: i > 0)
                d.bounds = CGRect(origin: .zero, size: SceneAssets.duckSize)
                d.position = CGPoint(x: x, y: i == 0 ? 0 : CGFloat(i % 2 == 0 ? 2 : -2))
                let paddle = CABasicAnimation(keyPath: "transform.rotation.z")
                paddle.fromValue = -0.08
                paddle.toValue = 0.08
                paddle.duration = 0.9 + Double(i) * 0.13
                paddle.autoreverses = true
                paddle.repeatCount = .infinity
                throttle(paddle, fps: 20)
                d.add(paddle, forKey: "paddle")
                container.addSublayer(d)
            }
        }

        let loop = CGPath(ellipseIn: CGRect(x: spot.x - radius, y: spot.y - radius * 0.65,
                                            width: radius * 2, height: radius * 1.3), transform: nil)
        let sail = CAKeyframeAnimation(keyPath: "position")
        sail.path = loop
        sail.rotationMode = .rotateAuto
        sail.calculationMode = .paced
        sail.duration = duration
        sail.repeatCount = .infinity
        sail.timeOffset = Double(rand(0...1)) * duration
        throttle(sail, fps: 30)
        container.add(sail, forKey: "sail")
        return container
    }

    // MARK: Autá

    private var carRoutes: [(CGPoint, CGPoint)] = []

    /// Autá jazdia po uliciach mimo parkov, vpravo v jazdnom pruhu, každé svojou
    /// rýchlosťou a s krátkou pauzou (ako na semafore) medzi jazdami.
    private func updateCars(_ routes: [(CGPoint, CGPoint)]) {
        let same = routes.count == carRoutes.count && zip(routes, carRoutes).allSatisfy {
            hypot($0.0.x - $1.0.x, $0.0.y - $1.0.y) < 1 && hypot($0.1.x - $1.1.x, $0.1.y - $1.1.y) < 1
        }
        guard !same else { return }
        carRoutes = routes
        carGroup.sublayers?.forEach { $0.removeFromSuperlayer() }
        guard theme.cars, !routes.isEmpty else { return }

        let colors: [UInt32] = [0xE4574B, 0xF2F2F2, 0x2E6FD0, 0xF2C230, 0x333840, 0x5DBB63, 0xF28C38]
        let count = max(4, Int(bounds.width / 110))
        for i in 0..<count {
            let route = routes[Int(rand(0...CGFloat(routes.count) - 0.01))]
            let forward = rand(0...1) < 0.5
            var a = forward ? route.0 : route.1
            var z = forward ? route.1 : route.0
            let len = max(hypot(z.x - a.x, z.y - a.y), 1)
            let dir = CGPoint(x: (z.x - a.x) / len, y: (z.y - a.y) / len)
            let lane = CGPoint(x: dir.y * 3.5, y: -dir.x * 3.5)          // pravý jazdný pruh
            a = CGPoint(x: a.x + lane.x, y: a.y + lane.y)
            z = CGPoint(x: z.x + lane.x, y: z.y + lane.y)

            let car = CALayer()
            car.actions = Self.noActions
            car.contents = SceneAssets.car(colors[i % colors.count])
            car.bounds = CGRect(origin: .zero, size: SceneAssets.carSize)
            car.position = CGPoint(x: -100, y: -100)                       // počas pauzy mimo obrazovky
            car.setAffineTransform(CGAffineTransform(rotationAngle: atan2(dir.y, dir.x)))

            let speed = rand(45...80)
            let travel = CFTimeInterval(len / speed)
            let drive = CABasicAnimation(keyPath: "position")
            drive.fromValue = NSValue(point: a)
            drive.toValue = NSValue(point: z)
            drive.duration = travel
            let trip = CAAnimationGroup()
            trip.animations = [drive]
            trip.duration = travel + Double(rand(1...9))
            trip.repeatCount = .infinity
            trip.timeOffset = Double(rand(0...1)) * trip.duration
            throttle(trip, fps: 30)
            car.add(trip, forKey: "drive")
            carGroup.addSublayer(car)
        }
    }

    // MARK: Sneh

    /// Dve vrstvy vločiek: drobné a pomalé v diaľke, väčšie a mäkké bližšie.
    private func makeSnow(in b: CGRect) -> CAEmitterLayer {
        let e = CAEmitterLayer()
        e.frame = b
        e.emitterShape = .line
        e.emitterPosition = CGPoint(x: b.midX, y: b.height + 16)
        e.emitterSize = CGSize(width: b.width * 1.1, height: 1)

        let far = CAEmitterCell()
        far.contents = SceneAssets.snowflake
        far.contentsScale = SceneAssets.scale
        far.birthRate = Float(b.width / 140)
        far.lifetime = Float(b.height / 18) + 4
        far.velocity = 20
        far.velocityRange = 8
        far.emissionLongitude = -.pi / 2
        far.emissionRange = .pi / 12
        far.xAcceleration = 3
        far.scale = 0.45
        far.scaleRange = 0.2
        far.alphaRange = 0.35
        far.spinRange = 1

        let near = CAEmitterCell()
        near.contents = SceneAssets.snowflakeSoft
        near.contentsScale = SceneAssets.scale
        near.birthRate = Float(b.width / 420)
        near.lifetime = Float(b.height / 40) + 6
        near.velocity = 46
        near.velocityRange = 14
        near.emissionLongitude = -.pi / 2
        near.emissionRange = .pi / 9
        near.xAcceleration = 6
        near.scale = 0.75
        near.scaleRange = 0.3
        near.alphaRange = 0.3

        e.emitterCells = [far, near]
        return e
    }

    /// Animácie nepotrebujú 120 Hz (ProMotion) – šetrí to GPU aj batériu.
    private func throttle(_ a: CAAnimation, fps: Float) {
        a.preferredFrameRateRange = CAFrameRateRange(minimum: fps / 2, maximum: fps, preferred: fps)
    }
}
