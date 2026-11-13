import AppKit
import WebKit

/// Zobrazí tému Desklandu (HTML + WebGL) cez WebKit. Stránka sa načíta zo
/// zdrojov aplikácie cez vlastnú schému a od Krajinky dostáva rovnaké správy,
/// aké jej posiela pôvodný Windows hostiteľ cez WebView2:
///   {"t":"icons","vis":true,"r":[[l,t,r,b],…]}  – rámiky ikon vo fyzických pixeloch
///   {"t":"pause","v":true|false}
@MainActor
final class WebSceneView: NSView {
    static let scheme = "krajinka"

    private let web: WKWebView
    private var ready = false
    private var lastIconsJSON: String?
    private var paused = false
    private(set) var page: String

    /// Priečinok s témami Desklandu v balíku aplikácie (alebo vedľa binárky pri `swift run`).
    static var resourceRoot: URL? {
        let candidates = [
            Bundle.main.resourceURL?.appendingPathComponent("Deskland", isDirectory: true),
            Bundle.main.executableURL?.deletingLastPathComponent()
                .appendingPathComponent("../../../Resources/Deskland", isDirectory: true).standardizedFileURL,
        ]
        return candidates.compactMap { $0 }.first {
            FileManager.default.fileExists(atPath: $0.appendingPathComponent("flat.html").path)
        }
    }

    static var isAvailable: Bool { resourceRoot != nil }

    init(frame: NSRect, page: String) {
        self.page = page
        let config = WKWebViewConfiguration()
        let bridge = Bridge()
        if let root = Self.resourceRoot {
            config.setURLSchemeHandler(BundleSchemeHandler(root: root), forURLScheme: Self.scheme)
        }
        // Náhrada rozhrania window.chrome.webview z WebView2 → WebKit
        let shim = """
        (function () {
          var listeners = [];
          window.chrome = window.chrome || {};
          window.chrome.webview = {
            addEventListener: function (type, fn) { if (type === 'message') listeners.push(fn); },
            removeEventListener: function () {},
            postMessage: function (m) {
              try { window.webkit.messageHandlers.host.postMessage(typeof m === 'string' ? m : JSON.stringify(m)); } catch (e) {}
            }
          };
          window.__krajinka = function (json) { for (var i = 0; i < listeners.length; i++) listeners[i]({ data: json }); };
        })();
        """
        config.userContentController.addUserScript(WKUserScript(source: shim, injectionTime: .atDocumentStart, forMainFrameOnly: true))
        config.userContentController.add(bridge, name: "host")
        web = WKWebView(frame: NSRect(origin: .zero, size: frame.size), configuration: config)
        web.autoresizingMask = [.width, .height]
        super.init(frame: frame)
        bridge.owner = self
        wantsLayer = true
        layer?.backgroundColor = RGB(0x1AA3C4).cg()
        addSubview(web)
        load(page)
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("init(coder:) sa nepoužíva") }

    func load(_ page: String) {
        self.page = page
        ready = false
        if let url = URL(string: "\(Self.scheme)://app/\(page)?wallpaper=1") {
            web.load(URLRequest(url: url))
        }
    }

    /// - Parameters:
    ///   - icons: rámiky ikon v lokálnych súradniciach obrazovky (body, počiatok vľavo dole)
    ///   - height: výška obrazovky v bodoch
    ///   - scale: pomer pixel/bod obrazovky (devicePixelRatio stránky)
    func setIcons(_ icons: [CGRect], screenHeight height: CGFloat, scale: CGFloat) {
        let rects = icons.map { r -> String in
            let l = Int((r.minX * scale).rounded()), rr = Int((r.maxX * scale).rounded())
            let t = Int(((height - r.maxY) * scale).rounded()), b = Int(((height - r.minY) * scale).rounded())
            return "[\(l),\(t),\(rr),\(b)]"
        }
        let json = "{\"t\":\"icons\",\"vis\":true,\"r\":[\(rects.joined(separator: ","))]}"
        lastIconsJSON = json
        if ready { send(json) }
    }

    func setAnimating(_ on: Bool) {
        paused = !on
        if ready { send("{\"t\":\"pause\",\"v\":\(paused)}") }
    }

    fileprivate func pageSaidReady() {
        ready = true
        send("{\"t\":\"pause\",\"v\":\(paused)}")
        if let json = lastIconsJSON { send(json) }
    }

    private func send(_ json: String) {
        guard let data = try? JSONSerialization.data(withJSONObject: json, options: [.fragmentsAllowed]),
              let literal = String(data: data, encoding: .utf8) else { return }
        web.evaluateJavaScript("window.__krajinka && window.__krajinka(\(literal));", completionHandler: nil)
    }

    /// Slabý odkaz na pohľad – WKUserContentController si handler drží silno.
    private final class Bridge: NSObject, WKScriptMessageHandler {
        weak var owner: WebSceneView?
        func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
            guard let text = message.body as? String else { return }
            if text == "ready" {
                let target = owner
                Task { @MainActor in target?.pageSaidReady() }
            }
        }
    }
}

/// Podáva súbory tém z priečinka aplikácie pod schémou krajinka://app/…
final class BundleSchemeHandler: NSObject, WKURLSchemeHandler {
    private let root: URL

    init(root: URL) { self.root = root.standardizedFileURL }

    func webView(_ webView: WKWebView, start task: WKURLSchemeTask) {
        guard let url = task.request.url else { return }
        let relative = String(url.path.drop(while: { $0 == "/" }))
        let file = root.appendingPathComponent(relative).standardizedFileURL
        guard file.path.hasPrefix(root.path + "/"), let data = try? Data(contentsOf: file) else {
            task.didFailWithError(URLError(.fileDoesNotExist))
            return
        }
        let types = ["html": "text/html; charset=utf-8", "js": "text/javascript; charset=utf-8", "png": "image/png",
                     "webp": "image/webp", "json": "application/json", "css": "text/css"]
        let mime = types[file.pathExtension.lowercased()] ?? "application/octet-stream"
        let response = HTTPURLResponse(url: url, statusCode: 200, httpVersion: "HTTP/1.1",
                                       headerFields: ["Content-Type": mime, "Content-Length": "\(data.count)"])!
        task.didReceive(response)
        task.didReceive(data)
        task.didFinish()
    }

    func webView(_ webView: WKWebView, stop task: WKURLSchemeTask) {}
}
