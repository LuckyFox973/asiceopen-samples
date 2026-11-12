import AppKit
import ApplicationServices

/// Číta polohy ikon na ploche z Findera cez Accessibility API.
///
/// Čítajú sa len role a rámčeky prvkov – názvy súborov ani ich obsah nie.
/// Volá sa z vedľajšieho vlákna (AX volania môžu pri zaneprázdnenom Finderi čakať).
enum FinderDesktopReader {
    static let finderBundleID = "com.apple.finder"
    private static let iconRoles: Set<String> = ["AXImage", "AXGroup", "AXButton", "AXCell", "AXStaticText"]

    static var isTrusted: Bool { AXIsProcessTrusted() }

    static func requestTrust() {
        let key = kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String
        _ = AXIsProcessTrustedWithOptions([key: true] as CFDictionary)
    }

    /// - Parameter primaryHeight: výška hlavnej obrazovky (NSScreen.screens[0]) v bodoch.
    /// - Returns: rámčeky ikon v globálnych Cocoa súradniciach (počiatok vľavo dole
    ///   hlavnej obrazovky), už rozšírené o popisok; `nil` ak prístup nie je povolený.
    static func readIcons(primaryHeight: CGFloat) -> [CGRect]? {
        guard AXIsProcessTrusted(), let desktop = desktopElements() else { return nil }
        var frames: [CGRect] = []
        for area in desktop {
            collectIcons(in: area, depth: 0, into: &frames)
        }
        return frames
            .map { ax in
                // AX: počiatok vľavo hore, os y dole → Cocoa: vľavo dole, os y hore
                let cocoa = CGRect(x: ax.minX, y: primaryHeight - ax.maxY, width: ax.width, height: ax.height)
                // rezerva na popisok pod ikonou (ak ho AX rámček nezahŕňa, nevadí ani tak)
                return CGRect(x: cocoa.minX - 14, y: cocoa.minY - 36,
                              width: cocoa.width + 28, height: cocoa.height + 44).integral
            }
            .sorted { ($0.minX, $0.minY) < ($1.minX, $1.minY) }
    }

    /// Plocha je vo Finderi priamy potomok aplikácie (typicky AXScrollArea).
    /// Okná Findera (AXWindow) a menu bar preskakujeme.
    private static func desktopElements() -> [AXUIElement]? {
        guard let finder = NSRunningApplication.runningApplications(withBundleIdentifier: finderBundleID).first
        else { return nil }
        let app = AXUIElementCreateApplication(finder.processIdentifier)
        AXUIElementSetMessagingTimeout(app, 0.4)
        let skip: Set<String> = [kAXWindowRole as String, kAXMenuBarRole as String]
        return children(app).filter { !skip.contains(role($0) ?? "") }
    }

    /// Rola prvkov ikon sa medzi verziami macOS líši (AXImage, AXGroup…), preto
    /// berieme každý prvok s rámčekom „veľkosti ikony“ a ďalej doň nevnárame.
    /// Veľké kontajnery (celá plocha) sa preliezajú hlbšie.
    private static func collectIcons(in element: AXUIElement, depth: Int, into frames: inout [CGRect]) {
        for child in children(element) {
            let r = role(child) ?? ""
            if let f = frame(child), f.width >= 16, f.height >= 16, f.width <= 400, f.height <= 400,
               iconRoles.contains(r) || !ignoredRoles.contains(r) {
                frames.append(f)
            } else if depth < 4 {
                collectIcons(in: child, depth: depth + 1, into: &frames)
            }
        }
    }

    /// Prvky, ktoré nie sú ikony, aj keď majú malý rámček.
    private static let ignoredRoles: Set<String> = ["AXScrollBar", "AXValueIndicator", "AXMenu", "AXMenuItem"]

    // MARK: - Diagnostika (bez názvov súborov)

    @MainActor
    static func diagnostics(primaryHeight: CGFloat) -> String {
        var out = "Krajinka – diagnostika ikon na ploche\n"
        out += "Dátum: \(Date())\n"
        out += "Accessibility povolené: \(AXIsProcessTrusted() ? "áno" : "NIE")\n"
        out += "Obrazovky (Cocoa frame):\n"
        for s in NSScreen.screens {
            out += "  id=\(s.displayID ?? 0) frame=\(s.frame) scale=\(s.backingScaleFactor)\n"
        }
        guard let finder = NSRunningApplication.runningApplications(withBundleIdentifier: finderBundleID).first else {
            return out + "Finder nebeží.\n"
        }
        let app = AXUIElementCreateApplication(finder.processIdentifier)
        AXUIElementSetMessagingTimeout(app, 0.5)
        out += "\nAX strom Findera (role / subrole / pozícia / veľkosť):\n"
        var lines = 0
        dump(app, depth: 0, into: &out, lines: &lines)
        let icons = readIcons(primaryHeight: primaryHeight) ?? []
        out += "\nNájdené ikony: \(icons.count)\n"
        for r in icons { out += "  \(r)\n" }
        return out
    }

    private static func dump(_ el: AXUIElement, depth: Int, into out: inout String, lines: inout Int) {
        guard lines < 500 else { return }
        let r = role(el) ?? "?"
        let sub = string(el, kAXSubroleAttribute) ?? "-"
        let f = frame(el).map { "\($0.origin) \($0.size)" } ?? "-"
        out += String(repeating: "  ", count: depth) + "\(r) [\(sub)] \(f)\n"
        lines += 1
        // okná Findera nerozbaľujeme – zaujíma nás len plocha
        guard depth < 5, r != (kAXWindowRole as String), r != (kAXMenuBarRole as String) else { return }
        for c in children(el) { dump(c, depth: depth + 1, into: &out, lines: &lines) }
    }

    // MARK: - AX pomocníci

    private static func attribute(_ el: AXUIElement, _ name: String) -> CFTypeRef? {
        var value: CFTypeRef?
        guard AXUIElementCopyAttributeValue(el, name as CFString, &value) == .success else { return nil }
        return value
    }

    private static func children(_ el: AXUIElement) -> [AXUIElement] {
        (attribute(el, kAXChildrenAttribute) as? [AXUIElement]) ?? []
    }

    private static func string(_ el: AXUIElement, _ name: String) -> String? {
        attribute(el, name) as? String
    }

    private static func role(_ el: AXUIElement) -> String? {
        string(el, kAXRoleAttribute)
    }

    private static func frame(_ el: AXUIElement) -> CGRect? {
        guard let pv = attribute(el, kAXPositionAttribute),
              let sv = attribute(el, kAXSizeAttribute),
              CFGetTypeID(pv) == AXValueGetTypeID(),
              CFGetTypeID(sv) == AXValueGetTypeID()
        else { return nil }
        var point = CGPoint.zero
        var size = CGSize.zero
        // swiftlint:disable force_cast
        guard AXValueGetValue(pv as! AXValue, .cgPoint, &point),
              AXValueGetValue(sv as! AXValue, .cgSize, &size)
        else { return nil }
        // swiftlint:enable force_cast
        return CGRect(origin: point, size: size)
    }
}

/// Pravidelne (1 s) zisťuje polohy ikon, ale len keď je niektorá scéna viditeľná.
@MainActor
final class DesktopIconProvider: NSObject {
    var onChange: (([CGRect]) -> Void)?
    private(set) var lastIcons: [CGRect] = []
    private(set) var hasAccess = false

    private var timer: Timer?
    private var inFlight = false
    private let axQueue = DispatchQueue(label: "krajinka.ax", qos: .utility)

    var isPolling = false {
        didSet { if isPolling != oldValue { updateTimer() } }
    }

    func refreshNow() { tick() }

    private func updateTimer() {
        if isPolling {
            guard timer == nil else { return }
            let t = Timer(timeInterval: 1.0, target: self, selector: #selector(tick), userInfo: nil, repeats: true)
            t.tolerance = 0.3
            RunLoop.main.add(t, forMode: .common)
            timer = t
            tick()
        } else {
            timer?.invalidate()
            timer = nil
        }
    }

    @objc private func tick() {
        guard !inFlight else { return }
        inFlight = true
        let primaryHeight = NSScreen.screens.first?.frame.height ?? 0
        axQueue.async { [self] in
            let icons = FinderDesktopReader.readIcons(primaryHeight: primaryHeight)
            Task { @MainActor in
                self.deliver(icons)
            }
        }
    }

    private func deliver(_ icons: [CGRect]?) {
        inFlight = false
        hasAccess = icons != nil
        let list = icons ?? []
        guard list != lastIcons else { return }
        lastIcons = list
        onChange?(list)
    }
}
