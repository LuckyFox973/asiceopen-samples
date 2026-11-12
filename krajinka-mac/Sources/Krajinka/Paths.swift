import CoreGraphics

/// Úsek chodníka. `isBridge` = vedie cez vodu → kreslí sa ako drevený mostík.
struct PathSegment {
    let isBridge: Bool
    let points: [CGPoint]
}

/// Chodníky medzi ikonami: minimálna kostra (MST) nad stredmi ikon,
/// mierne zakrivené, orezané na okraj čistín. Ak by most bol dlhší než
/// ~220 bodov, chodník sa vynechá.
enum PathPlanner {
    /// - Parameter overWater: pri mori sa úseky cez vodu kreslia ako mostíky; inde je celý chodník po zemi.
    static func plan(field: TerrainField, icons: [CGRect], overWater: Bool) -> [PathSegment] {
        let centers = icons.map { CGPoint(x: $0.midX, y: $0.midY) }
        guard centers.count >= 2 else { return [] }
        var out: [PathSegment] = []

        for (i, j) in minimumSpanningTree(centers) {
            let a = centers[i], b = centers[j]
            let length = hypot(b.x - a.x, b.y - a.y)
            guard length >= 150, length <= 900 else { continue }

            let mid = CGPoint(x: (a.x + b.x) / 2, y: (a.y + b.y) / 2)
            let perp = CGPoint(x: -(b.y - a.y) / length, y: (b.x - a.x) / length)
            let bend = CGFloat(hash2(Int32(i), Int32(j), 77) - 0.5) * 0.35 * length
            let control = CGPoint(x: mid.x + perp.x * bend, y: mid.y + perp.y * bend)

            let n = max(2, Int(length / 3))
            let pts: [CGPoint] = (0...n).map { k in
                let t: CGFloat = CGFloat(k) / CGFloat(n)
                let u: CGFloat = 1 - t
                let wa: CGFloat = u * u
                let wc: CGFloat = 2 * u * t
                let wb: CGFloat = t * t
                let x: CGFloat = wa * a.x + wc * control.x + wb * b.x
                let y: CGFloat = wa * a.y + wc * control.y + wb * b.y
                return CGPoint(x: x, y: y)
            }
            guard let first = pts.firstIndex(where: { field.clear($0) < 0.5 }),
                  let last = pts.lastIndex(where: { field.clear($0) < 0.5 }),
                  last - first >= 2
            else { continue }
            let p = Array(pts[first...last])
            guard overWater else {
                out.append(PathSegment(isBridge: false, points: p))
                continue
            }
            let water = p.map { field.sdf($0) < -0.5 }

            var runs: [(water: Bool, start: Int, end: Int)] = []
            var k = 0
            while k < p.count {
                let w = water[k]
                var e = k
                while e + 1 < p.count && water[e + 1] == w { e += 1 }
                runs.append((w, k, e))
                k = e + 1
            }
            if runs.contains(where: { $0.water && ($0.end - $0.start) * 3 > 220 }) { continue }

            for run in runs {
                var s = run.start, e = run.end
                if run.water { s = max(0, s - 2); e = min(p.count - 1, e + 2) }
                guard e > s else { continue }
                out.append(PathSegment(isBridge: run.water, points: Array(p[s...e])))
            }
        }
        return out
    }

    /// Primov algoritmus, O(n²) – ikon je málo.
    private static func minimumSpanningTree(_ pts: [CGPoint]) -> [(Int, Int)] {
        let n = pts.count
        var used = [Bool](repeating: false, count: n)
        var best = [(dist: CGFloat, from: Int)](repeating: (.greatestFiniteMagnitude, -1), count: n)
        best[0] = (0, -1)
        var edges: [(Int, Int)] = []
        for _ in 0..<n {
            var u = -1
            for i in 0..<n where !used[i] && (u < 0 || best[i].dist < best[u].dist) { u = i }
            guard u >= 0 else { break }
            used[u] = true
            if best[u].from >= 0 { edges.append((best[u].from, u)) }
            for v in 0..<n where !used[v] {
                let d = hypot(pts[u].x - pts[v].x, pts[u].y - pts[v].y)
                if d < best[v].dist { best[v] = (d, u) }
            }
        }
        return edges
    }
}
