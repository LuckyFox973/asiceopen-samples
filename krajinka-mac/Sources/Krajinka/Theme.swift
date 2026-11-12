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

/// Prah výškového poľa: pod ním je voda.
enum Band {
    static let shore: Float = 0.42
}

enum ThemeID: String, CaseIterable {
    case beach, meadow, winter

    var title: String {
        switch self {
        case .beach: return "Pláž"
        case .meadow: return "Lúka"
        case .winter: return "Zima"
        }
    }
}

enum Swimmers { case none, sailboats, ducks }
enum Birds { case none, gulls, songbirds, crows }

/// Všetky parametre jednej témy. Hodnoty sú vyladené cez tools/terrain_preview.py.
struct Theme {
    var id: ThemeID = .beach
    var seed: UInt32 = 1
    /// Nižšia hodnota = viac vody mimo ikon.
    var waterLevel: Float = 0.30
    /// true = pevnina len okolo ikon, inde voda (prehľadnejšie);
    /// false = náhodné ostrovy aj mimo ikon.
    var landOnlyAtIcons = true
    /// Chodníky (a mostíky) medzi dokumentmi.
    var showPaths = false

    // Voda (hĺbka sa počíta v bodoch od pobrežia)
    var deepest = RGB(0x0B3A68)
    var deep = RGB(0x1769A0)
    var shallow = RGB(0x35BEC8)
    var shelf = RGB(0x9BE7E1)          // podvodný prah a odraz oblohy
    var lip = RGB(0xFFFFFF)            // biely lem tesne pri brehu
    var lipAlpha: Float = 0.80

    // Pevnina
    var sand = RGB(0xEDD5A1)
    var wetSand = RGB(0xCDB079)
    var beachWidth: Float = 15
    var land = RGB(0x7FB550)
    var landAlt = RGB(0x5E9A3C)
    var microAmount: Float = 0.07
    var clearing = RGB(0xD9BD86)       // čistinka pod ikonou
    var clearingEdge = RGB(0xB59A64)
    var shadowTint = RGB(0x2F5D46)     // farba tieňov reliéfu
    var shadeStrength: Float = 1.0

    // Chodníky a mosty
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
    var rock = RGB(0x77858C)
    var rockLight = RGB(0xB9C3C6)
    var accent = RGB(0xF0806E)
    var flowers: [RGB] = [RGB(0xFFD166), RGB(0xFF8FA3), RGB(0xFFFFFF)]

    // Animácie
    var waveStrength: Float = 0.55     // 0 = bez vĺn (zamrznuté)
    var wavePeriod: Double = 5.0
    var waveColor = RGB(0xFFFFFF)
    var shimmerOpacity: Float = 0.35
    var waterGlints: Float = 1.0       // relatívna hustota zábleskov na vode
    var snowGlints: Float = 0
    var cloudShadowOpacity: Float = 1.0    // násobí priehľadnosť zapečenú v sprite
    var cloudWhiteOpacity: Float = 0.85
    var snowfall = false
    var swimmers: Swimmers = .sailboats
    var birds: Birds = .gulls
    var butterflies = false

    static func named(_ id: ThemeID) -> Theme {
        var t = Theme()
        switch id {
        case .beach:
            break   // predvolené hodnoty = Pláž
        case .meadow:
            t.id = .meadow; t.seed = 2; t.waterLevel = 0.50
            t.deepest = RGB(0x173F5A); t.deep = RGB(0x245F7C); t.shallow = RGB(0x4A92A3); t.shelf = RGB(0x8CC6C7)
            t.lip = RGB(0xE4F2EA); t.lipAlpha = 0.40
            t.sand = RGB(0x8E8F57); t.wetSand = RGB(0x6C7243); t.beachWidth = 4
            t.land = RGB(0x7DB352); t.landAlt = RGB(0x5F9741); t.microAmount = 0.07
            t.clearing = RGB(0x9DC76D); t.clearingEdge = RGB(0x739F4B); t.shadowTint = RGB(0x24502E)
            t.path = RGB(0xD1B57F); t.pathEdge = RGB(0x8E7548); t.wood = RGB(0x9A6E45); t.woodDark = RGB(0x654527)
            t.trunk = RGB(0x7A5533); t.trunkDark = RGB(0x5A3D23)
            t.foliageDark = RGB(0x285F2B); t.foliageMid = RGB(0x41853A); t.foliageLight = RGB(0x93CD62)
            t.rock = RGB(0x7D7F77); t.rockLight = RGB(0xB0B2A8); t.accent = RGB(0xE86A92)
            t.flowers = [RGB(0xFFFFFF), RGB(0xF7D046), RGB(0xE86A92), RGB(0xB58CE0)]
            t.waveStrength = 0.28; t.wavePeriod = 7.0; t.waveColor = RGB(0xE4F2EA)
            t.shimmerOpacity = 0.22; t.waterGlints = 0.5
            t.cloudShadowOpacity = 1.0; t.cloudWhiteOpacity = 0.75
            t.swimmers = .ducks; t.birds = .songbirds; t.butterflies = true
        case .winter:
            t.id = .winter; t.seed = 3; t.waterLevel = 0.46
            t.deepest = RGB(0x6283A4); t.deep = RGB(0x84A5C3); t.shallow = RGB(0xAFCBDF); t.shelf = RGB(0xD6E7F2)
            t.lip = RGB(0xFFFFFF); t.lipAlpha = 0.55
            t.sand = RGB(0xE6EEF5); t.wetSand = RGB(0xCCD9E6); t.beachWidth = 5
            t.land = RGB(0xF1F5F9); t.landAlt = RGB(0xE0E9F1); t.microAmount = 0.035
            t.clearing = RGB(0xBACADB); t.clearingEdge = RGB(0x9EB2C7); t.shadowTint = RGB(0x7F97B8)
            t.shadeStrength = 1.25
            t.path = RGB(0xD5DFEA); t.pathEdge = RGB(0xA3B4C7); t.wood = RGB(0x8C6A4E); t.woodDark = RGB(0x5C432F)
            t.trunk = RGB(0x5E4A3A); t.trunkDark = RGB(0x46362A)
            t.foliageDark = RGB(0x1C4639); t.foliageMid = RGB(0x2B5E4D); t.foliageLight = RGB(0x4C8A70)
            t.rock = RGB(0x6B7684); t.rockLight = RGB(0xA4AFBC); t.accent = RGB(0xFFFFFF)
            t.flowers = []
            t.waveStrength = 0; t.shimmerOpacity = 0; t.waterGlints = 0; t.snowGlints = 1.0
            t.cloudShadowOpacity = 0.65; t.cloudWhiteOpacity = 0.9
            t.snowfall = true; t.swimmers = .none; t.birds = .crows
        }
        return t
    }
}
