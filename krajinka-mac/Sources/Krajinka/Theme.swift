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
    @inline(__always) func mul(_ o: RGB) -> RGB { RGB(r: r * o.r, g: g * o.g, b: b * o.b) }

    func cg(_ alpha: CGFloat = 1) -> CGColor {
        CGColor(srgbRed: CGFloat(r), green: CGFloat(g), blue: CGFloat(b), alpha: alpha)
    }

    static let white = RGB(0xFFFFFF)
    static let black = RGB(0x000000)
}

enum ThemeID: String, CaseIterable {
    case plaz, ostrovy, les, polia, mesto, zima

    var title: String {
        switch self {
        case .plaz: return "Pláž"
        case .ostrovy: return "Ostrovy"
        case .les: return "Les a oblaky"
        case .polia: return "Polia"
        case .mesto: return "Mesto"
        case .zima: return "Zima"
        }
    }
}

/// Čo je mimo dokumentov (okolie).
enum Backdrop { case sea, forest, fields, city, snowForest }
/// Na čom stoja dokumenty (oblasť okolo ikon).
enum Ground { case sand, island, cloud, pasture, park, snow }

enum Swimmers { case none, sailboats, ducks }
enum Birds { case none, gulls, songbirds, crows }

/// Parametre jednej témy. Vzhľad je vyladený v tools/terrain_preview.py.
struct Theme {
    var id: ThemeID = .plaz
    var seed: UInt32 = 11
    var backdrop: Backdrop = .sea
    var ground: Ground = .sand

    // Oblasť dokumentov: polomer okolo rámika ikony, zvlnenie okraja, plynulosť spájania
    var regionRadius: (min: Float, max: Float) = (44, 70)
    var regionWobble: Float = 40
    var regionBlend: Float = 50

    // More
    var deepest = RGB(0x0E7FA6)
    var deep = RGB(0x14A3BF)          // aj farba podkladu okna
    var shallow = RGB(0x3FD3D3)
    var shelf = RGB(0xA8F0E6)
    var seaFalloff: Float = 60

    // Chodníky (voliteľné)
    var showPaths = false
    var path = RGB(0xE2C891)
    var pathEdge = RGB(0xA88C5A)
    var wood = RGB(0xA5764A)
    var woodDark = RGB(0x6E4B2C)

    // Flóra
    var trunk = RGB(0x9C7446)
    var trunkDark = RGB(0x76532F)
    var foliageDark = RGB(0x23703A)
    var foliageMid = RGB(0x3F9444)
    var foliageLight = RGB(0x8CCB5C)

    // Animácie
    var waveStrength: Float = 0.55     // 0 = bez vĺn
    var wavePeriod: Double = 5.0
    var waveColor = RGB(0xFFFFFF)
    var shimmerOpacity: Float = 0.35
    var waterGlints: Float = 1.0
    var snowGlints: Float = 0
    var cloudShadowOpacity: Float = 1.0
    var cloudWhiteOpacity: Float = 0.85
    var cloudCountFactor: CGFloat = 1
    var snowfall = false
    var swimmers: Swimmers = .sailboats
    var birds: Birds = .gulls
    var butterflies = false
    var cars = false

    var hasSea: Bool { backdrop == .sea }

    static func named(_ id: ThemeID) -> Theme {
        var t = Theme()
        switch id {
        case .plaz:
            break   // predvolené hodnoty
        case .ostrovy:
            t.id = .ostrovy; t.seed = 1; t.ground = .island
            t.regionRadius = (30, 46); t.regionWobble = 30; t.regionBlend = 36
            t.deepest = RGB(0x0B3A68); t.deep = RGB(0x1769A0); t.shallow = RGB(0x35BEC8); t.shelf = RGB(0x9BE7E1)
            t.seaFalloff = 45
        case .les:
            t.id = .les; t.seed = 5; t.backdrop = .forest; t.ground = .cloud
            t.regionRadius = (34, 52); t.regionWobble = 30; t.regionBlend = 44
            t.deep = RGB(0x2E5E2C)
            t.path = RGB(0xD9C08E); t.pathEdge = RGB(0x8E7548)
            t.trunkDark = RGB(0x5A3D23); t.foliageDark = RGB(0x2E6B2E); t.foliageMid = RGB(0x4A8C3A); t.foliageLight = RGB(0x8CC860)
            t.waveStrength = 0; t.shimmerOpacity = 0; t.waterGlints = 0
            t.cloudShadowOpacity = 1.0; t.cloudWhiteOpacity = 0.95; t.cloudCountFactor = 1.6
            t.swimmers = .none; t.birds = .songbirds
        case .polia:
            t.id = .polia; t.seed = 7; t.backdrop = .fields; t.ground = .pasture
            t.regionRadius = (34, 52); t.regionWobble = 26; t.regionBlend = 40
            t.deep = RGB(0x9CC75B)
            t.path = RGB(0xC9A97A); t.pathEdge = RGB(0x8E7548)
            t.trunkDark = RGB(0x5A3D23); t.foliageDark = RGB(0x2E6B2E); t.foliageMid = RGB(0x4A8C3A); t.foliageLight = RGB(0x8CC860)
            t.waveStrength = 0; t.shimmerOpacity = 0; t.waterGlints = 0
            t.swimmers = .none; t.birds = .songbirds; t.butterflies = true
        case .mesto:
            t.id = .mesto; t.seed = 9; t.backdrop = .city; t.ground = .park
            t.regionRadius = (30, 44); t.regionWobble = 22; t.regionBlend = 36
            t.deep = RGB(0x4A4E55)
            t.path = RGB(0xD8D2C4); t.pathEdge = RGB(0x9A948A)
            t.trunkDark = RGB(0x5A3D23); t.foliageDark = RGB(0x2E6B2E); t.foliageMid = RGB(0x4A8C3A); t.foliageLight = RGB(0x8CC860)
            t.waveStrength = 0; t.shimmerOpacity = 0; t.waterGlints = 0
            t.cloudShadowOpacity = 0.9; t.cloudWhiteOpacity = 0.6
            t.swimmers = .none; t.birds = .songbirds; t.cars = true
        case .zima:
            t.id = .zima; t.seed = 3; t.backdrop = .snowForest; t.ground = .snow
            t.regionRadius = (36, 56); t.regionWobble = 30; t.regionBlend = 44
            t.deep = RGB(0xD9E3ED)
            t.path = RGB(0xEEF3F8); t.pathEdge = RGB(0xA3B4C7)
            t.trunkDark = RGB(0x46362A); t.foliageDark = RGB(0x1C4639); t.foliageMid = RGB(0x2B5E4D); t.foliageLight = RGB(0x4C8A70)
            t.waveStrength = 0; t.shimmerOpacity = 0; t.waterGlints = 0; t.snowGlints = 0.8
            t.cloudShadowOpacity = 0.65; t.cloudWhiteOpacity = 0.9
            t.snowfall = true; t.swimmers = .none; t.birds = .crows
        }
        return t
    }
}
