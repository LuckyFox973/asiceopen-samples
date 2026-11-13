import CoreGraphics

struct RGB {
    var r: Float, g: Float, b: Float

    init(r: Float, g: Float, b: Float) { self.r = r; self.g = g; self.b = b }

    init(_ hex: UInt32) {
        r = Float((hex >> 16) & 0xFF) / 255
        g = Float((hex >> 8) & 0xFF) / 255
        b = Float(hex & 0xFF) / 255
    }

    @inline(__always) func mix(_ o: RGB, _ t: Float) -> RGB {
        RGB(r: r + (o.r - r) * t, g: g + (o.g - g) * t, b: b + (o.b - b) * t)
    }
    @inline(__always) func offset(_ d: Float) -> RGB { RGB(r: r + d, g: g + d, b: b + d) }
    @inline(__always) func scaled(_ k: Float) -> RGB { RGB(r: r * k, g: g * k, b: b * k) }

    func cg(_ alpha: CGFloat = 1) -> CGColor {
        CGColor(srgbRed: CGFloat(r), green: CGFloat(g), blue: CGFloat(b), alpha: alpha)
    }

    static let white = RGB(0xFFFFFF)
    static let black = RGB(0x000000)
}

enum ThemeID: String, CaseIterable {
    case dlPlaz = "deskland-plaz", dlLuka = "deskland-luka", corgi = "luka-corgi"
    case plaz, ostrovy, les, polia, mesto

    var title: String {
        switch self {
        case .dlPlaz: return "Pláž – Deskland"
        case .dlLuka: return "Lúka – Deskland"
        case .corgi: return "Lúka s corgim"
        case .plaz: return "Pláž"
        case .ostrovy: return "Ostrovy"
        case .les: return "Les a oblaky"
        case .polia: return "Polia"
        case .mesto: return "Mesto"
        }
    }
}

/// Parametre jednej témy. Farby a maľba sú v Painters.swift (vyladené v tools/terrain_preview.py).
struct Theme {
    var id: ThemeID = .plaz
    var seed: UInt32 = 11

    // Oblasť dokumentov: polomer okolo rámika ikony, zvlnenie okraja, plynulosť spájania
    var regionRadius: (min: Float, max: Float) = (46, 72)
    var regionWobble: Float = 40
    var regionBlend: Float = 52

    /// Farba podkladu okna (kým sa nevykreslí prvý obraz).
    var base = RGB(0x23B4C8)

    // Chodníky (voliteľné)
    var showPaths = false
    var path = RGB(0xEDE2CC)
    var pathEdge = RGB(0xC9B48F)
    var wood = RGB(0xB08A62)
    var woodDark = RGB(0x7E5E40)

    // Animácie
    var wind = false                   // poryvy vetra (svetlo prebiehajúce trávou)
    var cloudShadowOpacity: Float = 0.10

    var hasSea: Bool { id == .plaz || id == .ostrovy }

    /// Téma Desklandu (HTML/WebGL cez WebKit) – názov stránky; nil = vlastný Metal render.
    var webPage: String? {
        switch id {
        case .dlPlaz: return "flat.html"
        case .dlLuka: return "meadow.html"
        case .corgi: return "meadow-corgi.html"
        default: return nil
        }
    }

    static func named(_ id: ThemeID) -> Theme {
        var t = Theme()
        t.id = id
        switch id {
        case .dlPlaz, .dlLuka, .corgi, .plaz:
            break
        case .ostrovy:
            t.seed = 1; t.regionRadius = (34, 52); t.regionWobble = 30; t.regionBlend = 40
            t.base = RGB(0x1E9CC0)
        case .les:
            t.seed = 5; t.regionRadius = (36, 54); t.regionWobble = 30; t.regionBlend = 44
            t.base = RGB(0x4E7F3B); t.wind = true; t.cloudShadowOpacity = 0.12
            t.path = RGB(0xD9C9A0); t.pathEdge = RGB(0x9C8A62)
        case .polia:
            t.seed = 7; t.regionRadius = (36, 54); t.regionWobble = 26; t.regionBlend = 40
            t.base = RGB(0xA9C672); t.wind = true; t.cloudShadowOpacity = 0.12
            t.path = RGB(0xD9C9A0); t.pathEdge = RGB(0x9C8A62)
        case .mesto:
            t.seed = 9; t.regionRadius = (32, 46); t.regionWobble = 22; t.regionBlend = 36
            t.base = RGB(0xD9D3C7); t.cloudShadowOpacity = 0.10
            t.path = RGB(0xEDE8DE); t.pathEdge = RGB(0xB8B1A4)
        }
        return t
    }
}
