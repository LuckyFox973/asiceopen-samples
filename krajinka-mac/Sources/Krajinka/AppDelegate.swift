import AppKit
import ServiceManagement

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    private enum Keys {
        static let theme = "theme"
        static let islandsEverywhere = "islandsEverywhere"
        static let paths = "paths"
        static let paused = "paused"
    }

    private var scenes: [CGDirectDisplayID: ScreenScene] = [:]
    private let iconProvider = DesktopIconProvider()
    private var statusItem: NSStatusItem?
    private var theme = AppDelegate.makeTheme(ThemeID(rawValue: UserDefaults.standard.string(forKey: Keys.theme) ?? "") ?? .beach)

    private static func makeTheme(_ id: ThemeID) -> Theme {
        var t = Theme.named(id)
        t.landOnlyAtIcons = !UserDefaults.standard.bool(forKey: Keys.islandsEverywhere)
        t.showPaths = UserDefaults.standard.bool(forKey: Keys.paths)
        return t
    }
    private var userPaused = UserDefaults.standard.bool(forKey: Keys.paused)
    private var screensAsleep = false
    private var globalIcons: [CGRect] = []

    // MARK: - Životný cyklus

    func applicationDidFinishLaunching(_ notification: Notification) {
        setupStatusItem()

        iconProvider.onChange = { [weak self] icons in
            self?.iconsChanged(icons)
        }
        rebuildScreens()

        // Bez povolenia Prístupnosť krajina ikony nevidí → systém sa opýta pri každom štarte,
        // kým povolenie chýba (po novom builde staré povolenie neplatí).
        if !FinderDesktopReader.isTrusted {
            FinderDesktopReader.requestTrust()
        }

        let nc = NotificationCenter.default
        nc.addObserver(self, selector: #selector(screensChanged),
                       name: NSApplication.didChangeScreenParametersNotification, object: nil)
        nc.addObserver(self, selector: #selector(powerStateChanged),
                       name: Notification.Name.NSProcessInfoPowerStateDidChange, object: nil)

        let wc = NSWorkspace.shared.notificationCenter
        wc.addObserver(self, selector: #selector(screensDidSleep),
                       name: NSWorkspace.screensDidSleepNotification, object: nil)
        wc.addObserver(self, selector: #selector(screensDidWake),
                       name: NSWorkspace.screensDidWakeNotification, object: nil)
        wc.addObserver(self, selector: #selector(screensDidSleep),
                       name: NSWorkspace.sessionDidResignActiveNotification, object: nil)
        wc.addObserver(self, selector: #selector(screensDidWake),
                       name: NSWorkspace.sessionDidBecomeActiveNotification, object: nil)
        wc.addObserver(self, selector: #selector(spaceChanged),
                       name: NSWorkspace.activeSpaceDidChangeNotification, object: nil)

        iconProvider.refreshNow()
        updateActivity()
    }

    // MARK: - Obrazovky

    /// Jedna scéna na každý monitor. Pri zmene rozlíšenia, pripojení/odpojení
    /// monitora alebo zmene mierky sa dotknutá scéna postaví nanovo.
    private func rebuildScreens() {
        var seen = Set<CGDirectDisplayID>()
        for screen in NSScreen.screens {
            guard let id = screen.displayID else { continue }
            seen.insert(id)
            if let existing = scenes[id], existing.matches(screen) {
                existing.updateScreen(screen)
                continue
            }
            scenes[id]?.close()
            let scene = ScreenScene(screen: screen, displayID: id, theme: theme)
            scene.onVisibilityChange = { [weak self] in self?.updateActivity() }
            scenes[id] = scene
        }
        for (id, scene) in Array(scenes) where !seen.contains(id) {
            scene.close()
            scenes[id] = nil
        }
        distributeIcons()
        updateActivity()
    }

    private func iconsChanged(_ icons: [CGRect]) {
        globalIcons = icons
        distributeIcons()
    }

    /// Globálne súradnice → lokálne súradnice každej obrazovky.
    private func distributeIcons() {
        for scene in scenes.values {
            let f = scene.screen.frame
            let local = globalIcons
                .filter { $0.intersects(f) }
                .map { $0.offsetBy(dx: -f.minX, dy: -f.minY) }
            scene.setIcons(local)
        }
    }

    /// Animuje sa len to, čo je vidno. Polohy ikon sa zisťujú len keď je
    /// aspoň jedna plocha viditeľná.
    private func updateActivity() {
        let lowPower = ProcessInfo.processInfo.isLowPowerModeEnabled
        var anyVisible = false
        for scene in scenes.values {
            let visible = scene.isVisible && !screensAsleep
            anyVisible = anyVisible || visible
            scene.view.setAnimating(visible && !userPaused && !lowPower)
        }
        iconProvider.isPolling = anyVisible
    }

    @objc private func screensChanged(_ n: Notification) { rebuildScreens() }
    @objc private func screensDidSleep(_ n: Notification) { screensAsleep = true; updateActivity() }
    @objc private func screensDidWake(_ n: Notification) { screensAsleep = false; updateActivity() }
    @objc private func spaceChanged(_ n: Notification) { updateActivity() }
    @objc nonisolated private func powerStateChanged(_ n: Notification) {
        // môže prísť z ľubovoľného vlákna
        Task { @MainActor in self.updateActivity() }
    }

    // MARK: - Menu bar

    private func setupStatusItem() {
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        let image = NSImage(systemSymbolName: "mountain.2", accessibilityDescription: "Krajinka")
            ?? NSImage(systemSymbolName: "photo", accessibilityDescription: "Krajinka")
        image?.isTemplate = true
        item.button?.image = image
        let menu = NSMenu()
        menu.delegate = self
        item.menu = menu
        statusItem = item
    }

    func menuNeedsUpdate(_ menu: NSMenu) {
        menu.removeAllItems()

        let header = NSMenuItem(title: "Krajinka", action: nil, keyEquivalent: "")
        header.isEnabled = false
        menu.addItem(header)

        if FinderDesktopReader.isTrusted {
            let info = NSMenuItem(title: "Ikony na ploche: \(globalIcons.count)", action: nil, keyEquivalent: "")
            info.isEnabled = false
            menu.addItem(info)
        } else {
            menu.addItem(item("⚠️ Krajinka nevidí ikony – povoliť Prístupnosť…", #selector(openAccessibilitySettings)))
        }
        menu.addItem(.separator())

        let themeItem = NSMenuItem(title: "Téma", action: nil, keyEquivalent: "")
        let themeMenu = NSMenu()
        for id in ThemeID.allCases {
            let t = item(id.title, #selector(selectTheme(_:)))
            t.representedObject = id.rawValue
            t.state = id == theme.id ? .on : .off
            themeMenu.addItem(t)
        }
        themeItem.submenu = themeMenu
        menu.addItem(themeItem)

        let paths = item("Chodníky medzi dokumentmi", #selector(togglePaths))
        paths.state = theme.showPaths ? .on : .off
        menu.addItem(paths)

        let islands = item("Ostrovy aj mimo ikon", #selector(toggleIslands))
        islands.state = theme.landOnlyAtIcons ? .off : .on
        menu.addItem(islands)

        let pause = item("Pozastaviť animácie", #selector(togglePause))
        pause.state = userPaused ? .on : .off
        menu.addItem(pause)
        menu.addItem(item("Prekresliť", #selector(redraw)))
        menu.addItem(.separator())

        let login = item("Spúšťať pri prihlásení", #selector(toggleLaunchAtLogin))
        login.state = SMAppService.mainApp.status == .enabled ? .on : .off
        menu.addItem(login)
        menu.addItem(item("Uložiť diagnostiku ikon…", #selector(saveDiagnostics)))
        menu.addItem(.separator())
        menu.addItem(item("Ukončiť Krajinku", #selector(quit), key: "q"))
    }

    private func item(_ title: String, _ action: Selector, key: String = "") -> NSMenuItem {
        let i = NSMenuItem(title: title, action: action, keyEquivalent: key)
        i.target = self
        return i
    }

    // MARK: - Akcie

    @objc private func selectTheme(_ sender: NSMenuItem) {
        guard let raw = sender.representedObject as? String, let id = ThemeID(rawValue: raw) else { return }
        UserDefaults.standard.set(raw, forKey: Keys.theme)
        theme = Self.makeTheme(id)
        scenes.values.forEach { $0.setTheme(theme) }
        updateActivity()
    }

    @objc private func togglePaths() {
        UserDefaults.standard.set(!theme.showPaths, forKey: Keys.paths)
        theme = Self.makeTheme(theme.id)
        scenes.values.forEach { $0.setTheme(theme) }
        updateActivity()
    }

    @objc private func toggleIslands() {
        let everywhere = !UserDefaults.standard.bool(forKey: Keys.islandsEverywhere)
        UserDefaults.standard.set(everywhere, forKey: Keys.islandsEverywhere)
        theme = Self.makeTheme(theme.id)
        scenes.values.forEach { $0.setTheme(theme) }
        updateActivity()
    }

    @objc private func togglePause() {
        userPaused.toggle()
        UserDefaults.standard.set(userPaused, forKey: Keys.paused)
        updateActivity()
    }

    @objc private func redraw() {
        iconProvider.refreshNow()
        scenes.values.forEach { $0.forceRedraw() }
    }

    @objc private func openAccessibilitySettings() {
        FinderDesktopReader.requestTrust()
        if let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility") {
            NSWorkspace.shared.open(url)
        }
    }

    @objc private func toggleLaunchAtLogin() {
        let service = SMAppService.mainApp
        do {
            if service.status == .enabled {
                try service.unregister()
            } else {
                try service.register()
            }
        } catch {
            showAlert("Nepodarilo sa zmeniť spúšťanie pri prihlásení.",
                      detail: "\(error.localizedDescription)\n\nTip: presuňte Krajinka.app do priečinka Aplikácie a skúste znova.")
        }
    }

    @objc private func saveDiagnostics() {
        let primaryHeight = NSScreen.screens.first?.frame.height ?? 0
        let text = FinderDesktopReader.diagnostics(primaryHeight: primaryHeight)
        let dir = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Logs/Krajinka", isDirectory: true)
        let url = dir.appendingPathComponent("diagnostika-ikon.txt")
        do {
            try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
            try text.write(to: url, atomically: true, encoding: .utf8)
            NSWorkspace.shared.activateFileViewerSelecting([url])
        } catch {
            showAlert("Diagnostiku sa nepodarilo uložiť.", detail: error.localizedDescription)
        }
    }

    @objc private func quit() {
        NSApp.terminate(nil)
    }

    private func showAlert(_ message: String, detail: String) {
        NSApp.activate(ignoringOtherApps: true)
        let alert = NSAlert()
        alert.messageText = message
        alert.informativeText = detail
        alert.runModal()
    }
}
