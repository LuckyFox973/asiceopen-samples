import AppKit

@main
enum KrajinkaApp {
    @MainActor
    static func main() {
        let app = NSApplication.shared
        let delegate = AppDelegate()
        app.delegate = delegate
        app.setActivationPolicy(.accessory)   // len ikona v menu bare, nie v Docku
        app.run()
        withExtendedLifetime(delegate) {}     // NSApplication drží delegáta len slabo
    }
}
