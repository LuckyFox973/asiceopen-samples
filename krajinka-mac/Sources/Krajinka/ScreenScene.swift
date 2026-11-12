import AppKit

/// Okno na úrovni tapety: pod ikonami Findera, na všetkých Spaces, neprijíma klik.
final class DesktopWindow: NSWindow {
    convenience init(screen: NSScreen) {
        self.init(contentRect: screen.frame, styleMask: [.borderless], backing: .buffered, defer: false)
        // +1: nad systémovou tapetou, stále hlboko pod ikonami (kCGDesktopIconWindowLevel = desktop + 20)
        level = NSWindow.Level(rawValue: Int(CGWindowLevelForKey(.desktopWindow)) + 1)
        collectionBehavior = [.canJoinAllSpaces, .stationary, .ignoresCycle, .fullScreenNone]
        ignoresMouseEvents = true
        isOpaque = true
        hasShadow = false
        backgroundColor = .black
        isReleasedWhenClosed = false
        animationBehavior = .none
        setFrame(screen.frame, display: false)
    }

    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }

    /// Bez tohto by AppKit okno odsunul spod menu baru.
    override func constrainFrameRect(_ frameRect: NSRect, to screen: NSScreen?) -> NSRect { frameRect }
}

extension NSScreen {
    var displayID: CGDirectDisplayID? {
        (deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber)?.uint32Value
    }
}

/// Scéna jednej obrazovky: okno + renderovanie na pozadí so zlučovaním požiadaviek.
@MainActor
final class ScreenScene: NSObject {
    let displayID: CGDirectDisplayID
    private(set) var screen: NSScreen
    let window: DesktopWindow
    let view: SceneView
    var onVisibilityChange: (() -> Void)?

    private var theme: Theme
    private var icons: [CGRect]?
    private var hasContent = false
    private var rendering = false
    private var pending = false
    private let renderQueue: DispatchQueue

    init(screen: NSScreen, displayID: CGDirectDisplayID, theme: Theme) {
        self.screen = screen
        self.displayID = displayID
        self.theme = theme
        self.renderQueue = DispatchQueue(label: "krajinka.render.\(displayID)", qos: .utility)
        self.window = DesktopWindow(screen: screen)
        self.view = SceneView(frame: NSRect(origin: .zero, size: screen.frame.size), theme: theme)
        super.init()

        window.contentView = view
        window.orderFrontRegardless()
        NotificationCenter.default.addObserver(self, selector: #selector(occlusionChanged),
                                               name: NSWindow.didChangeOcclusionStateNotification,
                                               object: window)
    }

    var isVisible: Bool { window.occlusionState.contains(.visible) }

    func matches(_ s: NSScreen) -> Bool {
        s.frame == screen.frame && s.backingScaleFactor == screen.backingScaleFactor
    }

    func updateScreen(_ s: NSScreen) { screen = s }

    /// Ikony v lokálnych súradniciach tejto obrazovky (počiatok vľavo dole, body).
    func setIcons(_ newIcons: [CGRect]) {
        guard newIcons != icons else { return }
        icons = newIcons
        requestRender()
    }

    func setTheme(_ t: Theme) {
        theme = t
        view.setTheme(t)
        requestRender()
    }

    func forceRedraw() { requestRender() }

    func close() {
        NotificationCenter.default.removeObserver(self)
        window.orderOut(nil)
        window.close()
    }

    // MARK: - Render

    /// Hustota renderu: Retina tam, kde to je lacné; na obrovských plochách
    /// (5120×1440 a pod.) strop ~6 Mpx, aby prekreslenie trvalo desiatky ms.
    private var renderScale: CGFloat {
        let size = screen.frame.size
        let area = max(size.width * size.height, 1)
        let budget = (6_000_000 / area).squareRoot()
        return min(screen.backingScaleFactor, max(0.5, budget))
    }

    private func requestRender() {
        if rendering { pending = true; return }
        startRender()
    }

    private func startRender() {
        rendering = true
        pending = false
        let size = screen.frame.size
        let scale = renderScale
        let icons = self.icons ?? []
        let theme = self.theme
        let animated = hasContent
        renderQueue.async { [self] in
            let scene = TerrainRenderer.render(size: size, scale: scale, icons: icons, theme: theme)
            Task { @MainActor in
                self.finishRender(scene, animated: animated)
            }
        }
    }

    private func finishRender(_ scene: RenderedScene?, animated: Bool) {
        rendering = false
        if let scene {
            view.apply(scene, animated: animated)
            hasContent = true
        }
        if pending { startRender() }
    }

    @objc private func occlusionChanged(_ note: Notification) {
        onVisibilityChange?()
    }
}
