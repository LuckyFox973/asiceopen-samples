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

    func cg(_ alpha: CGFloat = 1) -> CGColor {
        CGColor(srgbRed: CGFloat(r), green: CGFloat(g), blue: CGFloat(b), alpha: alpha)
    }
}

/// Prahy výškového poľa. Pod `shore` je voda, nad `clearing` je pokojná plocha pod ikonami.
enum Band {
    static let deep: Float = 0.30
    static let shore: Float = 0.42
    static let land: Float = 0.47
    static let clearing: Float = 0.74
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

enum DecorationKind {
    case palm, tree, pine, bush, rock, lilypad

    var baseSize: CGFloat {
        switch self {
        case .palm: return 30
        case .tree: return 34
        case .pine: return 30
        case .bush: return 16
        case .rock: return 14
        case .lilypad: return 12
        }
    }
}

struct Theme {
    let id: ThemeID
    let seed: UInt32
    /// Nižšia hodnota = viac vody mimo ikon.
    let waterLevel: Float

    // Terén (od najnižšieho po najvyšší)
    let deepest: RGB
    let deep: RGB
    let shallow: RGB
    let shore: RGB
    let land: RGB
    let landAlt: RGB
    let clearing: RGB
    let outline: RGB

    // Animované vrstvy
    let foamColor: CGColor
    let foamOpacity: ClosedRange<Float>
    let sparkleOpacity: Float      // 0 = bez iskier na vode
    let snowfall: Bool
    let cloudShadowOpacity: Float

    // Dekorácie
    let trunk: CGColor
    let foliageDark: CGColor
    let foliageLight: CGColor
    let rock: CGColor
    let rockLight: CGColor
    let accent: CGColor            // sneh na vrcholkoch / kvet na lekne

    static func named(_ id: ThemeID) -> Theme {
        switch id {
        case .beach:
            return Theme(
                id: .beach, seed: 1, waterLevel: 0.30,
                deepest: RGB(0x1D5A86), deep: RGB(0x2A7DAE), shallow: RGB(0x46B4C6),
                shore: RGB(0xD6C08A), land: RGB(0xD2B77C), landAlt: RGB(0xC6A96B),
                clearing: RGB(0xC2A66C), outline: RGB(0xF2EFE2),
                foamColor: RGB(0xFFFFFF).cg(), foamOpacity: 0.30...0.85,
                sparkleOpacity: 0.85, snowfall: false, cloudShadowOpacity: 0.10,
                trunk: RGB(0x8A6A3F).cg(), foliageDark: RGB(0x3B8A38).cg(),
                foliageLight: RGB(0x67AE4D).cg(), rock: RGB(0x8C8981).cg(),
                rockLight: RGB(0xB3AEA3).cg(), accent: RGB(0xF4E9C8).cg()
            )
        case .meadow:
            return Theme(
                id: .meadow, seed: 2, waterLevel: 0.50,
                deepest: RGB(0x214D66), deep: RGB(0x2E6A86), shallow: RGB(0x4E8DA4),
                shore: RGB(0x7A8F52), land: RGB(0x6C9E48), landAlt: RGB(0x5D8F3F),
                clearing: RGB(0x7BA954), outline: RGB(0x3E6638),
                foamColor: RGB(0xD3EBDB).cg(), foamOpacity: 0.12...0.38,
                sparkleOpacity: 0.6, snowfall: false, cloudShadowOpacity: 0.12,
                trunk: RGB(0x6B4F2E).cg(), foliageDark: RGB(0x3A7633).cg(),
                foliageLight: RGB(0x5C9C44).cg(), rock: RGB(0x7F8079).cg(),
                rockLight: RGB(0xA6A79E).cg(), accent: RGB(0xF2B8C6).cg()
            )
        case .winter:
            return Theme(
                id: .winter, seed: 3, waterLevel: 0.46,
                deepest: RGB(0x3B5A77), deep: RGB(0x527796), shallow: RGB(0x7CA0BC),
                shore: RGB(0xD3DEE8), land: RGB(0xC7D3E0), landAlt: RGB(0xBCC9D7),
                clearing: RGB(0x9AADC2), outline: RGB(0xE6EDF3),
                foamColor: RGB(0xFFFFFF).cg(), foamOpacity: 0.10...0.28,
                sparkleOpacity: 0.45, snowfall: true, cloudShadowOpacity: 0.08,
                trunk: RGB(0x5E4A3A).cg(), foliageDark: RGB(0x2E5949).cg(),
                foliageLight: RGB(0x3E725F).cg(), rock: RGB(0x7A8593).cg(),
                rockLight: RGB(0xA9B3BF).cg(), accent: RGB(0xF2F6FA).cg()
            )
        }
    }

    /// Čo (ak vôbec) postaviť na danom mieste. `roll` je deterministické náhodné číslo 0–1.
    func decoration(height f: Float, roll: Float) -> DecorationKind? {
        let onLand = f > 0.49 && f < 0.71
        switch id {
        case .beach:
            if onLand && roll < 0.50 { return roll < 0.36 ? .palm : .bush }
            if f > 0.31 && f < 0.39 && roll < 0.07 { return .rock }
        case .meadow:
            if onLand && roll < 0.52 { return roll < 0.42 ? .tree : .bush }
            if f < 0.38 && f > 0.22 && roll < 0.06 { return .lilypad }
        case .winter:
            if onLand && roll < 0.62 { return .pine }
            if onLand && roll < 0.68 { return .rock }
        }
        return nil
    }

    @inline(__always)
    func color(height f: Float, detail d: Float) -> RGB {
        if f < Band.deep {
            return deepest.mix(deep, clamp01(f / Band.deep))
        } else if f < Band.shore {
            let t = (f - Band.deep) / (Band.shore - Band.deep)
            return deep.mix(shallow, smooth(t))
        } else if f < Band.land {
            let t = (f - Band.shore) / (Band.land - Band.shore)
            return shore.mix(land, t * t)
        } else if f < Band.clearing {
            let patch = smooth(clamp01((d - 0.38) / 0.24))
            let base = land.mix(landAlt, patch)
            let t = clamp01((f - (Band.clearing - 0.05)) / 0.05)
            return base.mix(clearing, t)
        } else {
            return clearing.offset((d - 0.5) * 0.03)
        }
    }
}
