import AppKit
import MetalKit

/// Obsah okna jednej obrazovky. Ak je k dispozícii Metal, kreslí živú krajinu
/// (LiveRenderer); inak ukáže statický obraz (aj s penou a trblietkami v podklade).
final class SceneView: NSView {
    private(set) var theme: Theme
    private(set) var isAnimating = true
    private let metalView: MTKView?
    private let renderer: LiveRenderer?
    private let staticView: NSView

    /// true = beží Metal shader (podklad sa potom kreslí bez peny – tú kreslí shader).
    var isLive: Bool { renderer != nil }

    init(frame: NSRect, theme: Theme) {
        self.theme = theme
        let mv = MTKView(frame: NSRect(origin: .zero, size: frame.size))
        mv.colorPixelFormat = .bgra8Unorm
        mv.framebufferOnly = true
        mv.colorspace = CGColorSpace(name: CGColorSpace.sRGB)   // farby ako v podklade aj na P3 displejoch
        mv.preferredFramesPerSecond = 30
        mv.enableSetNeedsDisplay = false
        mv.autoresizingMask = [.width, .height]
        mv.layer?.isOpaque = true
        let r = LiveRenderer(view: mv)
        renderer = r
        metalView = r != nil ? mv : nil
        staticView = NSView(frame: NSRect(origin: .zero, size: frame.size))
        super.init(frame: frame)
        wantsLayer = true
        layer?.backgroundColor = theme.base.cg()

        if let mv = metalView, let r = renderer {
            mv.delegate = r
            r.themeIndex = Self.index(theme.id)
            addSubview(mv)
        } else {
            let l = CALayer()
            l.contentsGravity = .resize
            l.actions = ["contents": NSNull()]
            staticView.layer = l
            staticView.wantsLayer = true
            staticView.autoresizingMask = [.width, .height]
            addSubview(staticView)
        }
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("init(coder:) sa nepoužíva") }

    private static func index(_ id: ThemeID) -> Int32 {
        switch id {
        case .plaz, .dlPlaz, .dlLuka, .corgi: return 0
        case .ostrovy: return 1
        case .les: return 2
        case .polia: return 3
        case .mesto: return 4
        }
    }

    func setTheme(_ theme: Theme) {
        self.theme = theme
        layer?.backgroundColor = theme.base.cg()
        renderer?.themeIndex = Self.index(theme.id)
    }

    func apply(_ scene: RenderedScene, animated: Bool) {
        if let r = renderer, let mv = metalView {
            r.update(scene, animated: animated)
            if mv.isPaused { mv.draw() }               // aj pozastavená scéna ukáže nový obraz
        } else if let l = staticView.layer {
            if animated {
                let fade = CATransition()
                fade.type = .fade
                fade.duration = 1.0
                l.add(fade, forKey: kCATransition)
            }
            l.contents = scene.terrain
        }
    }

    func setAnimating(_ on: Bool) {
        guard on != isAnimating else { return }
        isAnimating = on
        metalView?.isPaused = !on
    }
}
