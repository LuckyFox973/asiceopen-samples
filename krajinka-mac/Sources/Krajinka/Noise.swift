import Foundation

/// Rýchly celočíselný hash -> [0, 1]. Deterministický, takže scenéria je stabilná
/// medzi prekresleniami (stromy sa „nepresúvajú“, len pribúdajú/miznú).
@inline(__always)
func hash2(_ x: Int32, _ y: Int32, _ seed: UInt32) -> Float {
    var h = UInt32(bitPattern: x) &* 374_761_393
    h = h &+ UInt32(bitPattern: y) &* 668_265_263
    h = h &+ seed &* 2_246_822_519
    h = (h ^ (h >> 13)) &* 1_274_126_177
    h ^= h >> 16
    return Float(h & 0xFF_FFFF) / Float(0xFF_FFFF)
}

@inline(__always) func clamp01(_ v: Float) -> Float { min(max(v, 0), 1) }
@inline(__always) func smooth(_ t: Float) -> Float { t * t * (3 - 2 * t) }

/// Jednoduchý value noise + fBm. Pracuje v logických bodoch, preto je výsledok
/// nezávislý od rozlíšenia a pomeru strán monitora.
struct ValueNoise {
    let seed: UInt32

    @inline(__always)
    func sample(_ x: Float, _ y: Float) -> Float {
        let xf = x.rounded(.down), yf = y.rounded(.down)
        let tx = smooth(x - xf), ty = smooth(y - yf)
        let ix = Int32(xf), iy = Int32(yf)
        let a = hash2(ix, iy, seed)
        let b = hash2(ix &+ 1, iy, seed)
        let c = hash2(ix, iy &+ 1, seed)
        let d = hash2(ix &+ 1, iy &+ 1, seed)
        let top = a + (b - a) * tx
        let bottom = c + (d - c) * tx
        return top + (bottom - top) * ty
    }

    func fbm(_ x: Float, _ y: Float, octaves: Int) -> Float {
        var sum: Float = 0, amp: Float = 0.5, freq: Float = 1, norm: Float = 0
        for o in 0..<octaves {
            // posun medzi oktávami, aby sa mriežky neprekrývali
            let off = Float(o) * 17.31
            sum += amp * sample(x * freq + off, y * freq - off)
            norm += amp
            amp *= 0.5
            freq *= 2
        }
        return sum / norm
    }
}

/// Deterministický generátor pre rozmiestnenie oblakov, iskier a pod.
struct SplitMix64: RandomNumberGenerator {
    private var state: UInt64
    init(seed: UInt64) { state = seed }
    mutating func next() -> UInt64 {
        state &+= 0x9E37_79B9_7F4A_7C15
        var z = state
        z = (z ^ (z >> 30)) &* 0xBF58_476D_1CE4_E5B9
        z = (z ^ (z >> 27)) &* 0x94D0_49BB_1331_11EB
        return z ^ (z >> 31)
    }
}
