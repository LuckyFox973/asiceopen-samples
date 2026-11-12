import CoreGraphics

/// Rozmiestňuje a kreslí stromy, palmy, kamene… Nikdy nie do blízkosti ikon.
enum Decorator {
    private static let gridCell: CGFloat = 52

    private struct Item {
        let point: CGPoint
        let kind: DecorationKind
        let size: CGFloat
    }

    static func draw(in ctx: CGContext, size: CGSize, field: TerrainField, icons: [CGRect], theme: Theme) {
        let cols = Int(size.width / gridCell) + 1
        let rows = Int(size.height / gridCell) + 1
        var items: [Item] = []

        for j in 0..<rows {
            for i in 0..<cols {
                let ii = Int32(i), jj = Int32(j)
                let jx = hash2(ii, jj, theme.seed &+ 11)
                let jy = hash2(ii, jj, theme.seed &+ 23)
                let roll = hash2(ii, jj, theme.seed &+ 37)
                let sz = hash2(ii, jj, theme.seed &+ 53)

                let p = CGPoint(x: (CGFloat(i) + 0.15 + 0.7 * CGFloat(jx)) * gridCell,
                                y: (CGFloat(j) + 0.15 + 0.7 * CGFloat(jy)) * gridCell)
                let f = field.sample(Float(p.x), Float(p.y)).height
                guard let kind = theme.decoration(height: f, roll: roll) else { continue }

                let s = kind.baseSize * (0.8 + 0.45 * CGFloat(sz))
                // celý obrys dekorácie (vrátane koruny a tieňa) + rezerva
                let bounds = CGRect(x: p.x - s * 0.75, y: p.y - s * 0.3,
                                    width: s * 1.5, height: s * 1.45).insetBy(dx: -14, dy: -14)
                if icons.contains(where: { $0.intersects(bounds) }) { continue }

                items.append(Item(point: p, kind: kind, size: s))
            }
        }

        // pohľad zhora-spredu: vzdialenejšie (vyššie) kreslíme skôr
        items.sort { $0.point.y > $1.point.y }
        for item in items {
            drawItem(item, in: ctx, theme: theme)
        }
    }

    private static func drawItem(_ item: Item, in ctx: CGContext, theme: Theme) {
        let p = item.point, s = item.size
        switch item.kind {
        case .palm: palm(ctx, p, s, theme)
        case .tree: tree(ctx, p, s, theme)
        case .pine: pine(ctx, p, s, theme)
        case .bush: bush(ctx, p, s, theme)
        case .rock: rock(ctx, p, s, theme)
        case .lilypad: lilypad(ctx, p, s, theme)
        }
    }

    // MARK: - Tvary

    private static func shadow(_ ctx: CGContext, _ p: CGPoint, _ s: CGFloat) {
        ctx.setFillColor(CGColor(srgbRed: 0, green: 0, blue: 0, alpha: 0.16))
        ctx.fillEllipse(in: CGRect(x: p.x - s * 0.32, y: p.y - s * 0.2, width: s * 1.0, height: s * 0.36))
    }

    private static func circle(_ ctx: CGContext, _ c: CGPoint, _ r: CGFloat) {
        ctx.fillEllipse(in: CGRect(x: c.x - r, y: c.y - r, width: r * 2, height: r * 2))
    }

    private static func palm(_ ctx: CGContext, _ p: CGPoint, _ s: CGFloat, _ t: Theme) {
        shadow(ctx, p, s)
        let top = CGPoint(x: p.x + s * 0.22, y: p.y + s * 0.95)
        ctx.setStrokeColor(t.trunk)
        ctx.setLineWidth(s * 0.13)
        ctx.setLineCap(.round)
        ctx.beginPath()
        ctx.move(to: p)
        ctx.addQuadCurve(to: top, control: CGPoint(x: p.x + s * 0.02, y: p.y + s * 0.6))
        ctx.strokePath()

        ctx.setFillColor(t.foliageDark)
        for k in 0..<6 {
            let angle = CGFloat(k) / 6 * 2 * .pi + 0.3
            ctx.saveGState()
            ctx.translateBy(x: top.x, y: top.y)
            ctx.rotate(by: angle)
            ctx.fillEllipse(in: CGRect(x: 0, y: -s * 0.09, width: s * 0.62, height: s * 0.18))
            ctx.restoreGState()
        }
        ctx.setFillColor(t.foliageLight)
        circle(ctx, top, s * 0.1)
    }

    private static func tree(_ ctx: CGContext, _ p: CGPoint, _ s: CGFloat, _ t: Theme) {
        shadow(ctx, p, s)
        ctx.setFillColor(t.trunk)
        ctx.fill(CGRect(x: p.x - s * 0.07, y: p.y, width: s * 0.14, height: s * 0.4))
        ctx.setFillColor(t.foliageDark)
        circle(ctx, CGPoint(x: p.x - s * 0.22, y: p.y + s * 0.5), s * 0.3)
        circle(ctx, CGPoint(x: p.x + s * 0.22, y: p.y + s * 0.5), s * 0.3)
        circle(ctx, CGPoint(x: p.x, y: p.y + s * 0.66), s * 0.42)
        ctx.setFillColor(t.foliageLight)
        circle(ctx, CGPoint(x: p.x - s * 0.12, y: p.y + s * 0.78), s * 0.2)
    }

    private static func pine(_ ctx: CGContext, _ p: CGPoint, _ s: CGFloat, _ t: Theme) {
        shadow(ctx, p, s)
        ctx.setFillColor(t.trunk)
        ctx.fill(CGRect(x: p.x - s * 0.05, y: p.y, width: s * 0.1, height: s * 0.25))
        for k in 0..<3 {
            let kf = CGFloat(k)
            let baseY = p.y + s * (0.15 + 0.28 * kf)
            let half = s * (0.45 - 0.11 * kf)
            let apexY = baseY + s * 0.5
            ctx.setFillColor(t.foliageDark)
            ctx.beginPath()
            ctx.move(to: CGPoint(x: p.x - half, y: baseY))
            ctx.addLine(to: CGPoint(x: p.x + half, y: baseY))
            ctx.addLine(to: CGPoint(x: p.x, y: apexY))
            ctx.closePath()
            ctx.fillPath()
            if t.snowfall {
                // snehová čiapka pri vrchole každého poschodia
                let capH = s * 0.18
                let capHalf = half * capH / (apexY - baseY)
                ctx.setFillColor(t.accent)
                ctx.beginPath()
                ctx.move(to: CGPoint(x: p.x - capHalf, y: apexY - capH))
                ctx.addLine(to: CGPoint(x: p.x + capHalf, y: apexY - capH))
                ctx.addLine(to: CGPoint(x: p.x, y: apexY))
                ctx.closePath()
                ctx.fillPath()
            }
        }
    }

    private static func bush(_ ctx: CGContext, _ p: CGPoint, _ s: CGFloat, _ t: Theme) {
        shadow(ctx, p, s * 0.8)
        ctx.setFillColor(t.foliageDark)
        circle(ctx, CGPoint(x: p.x - s * 0.22, y: p.y + s * 0.2), s * 0.28)
        circle(ctx, CGPoint(x: p.x + s * 0.22, y: p.y + s * 0.2), s * 0.28)
        circle(ctx, CGPoint(x: p.x, y: p.y + s * 0.36), s * 0.32)
        ctx.setFillColor(t.foliageLight)
        circle(ctx, CGPoint(x: p.x - s * 0.06, y: p.y + s * 0.46), s * 0.13)
    }

    private static func rock(_ ctx: CGContext, _ p: CGPoint, _ s: CGFloat, _ t: Theme) {
        ctx.setFillColor(CGColor(srgbRed: 0, green: 0, blue: 0, alpha: 0.14))
        ctx.fillEllipse(in: CGRect(x: p.x - s * 0.4, y: p.y - s * 0.38, width: s * 1.0, height: s * 0.5))
        ctx.setFillColor(t.rock)
        ctx.fillEllipse(in: CGRect(x: p.x - s * 0.45, y: p.y - s * 0.3, width: s * 0.9, height: s * 0.6))
        ctx.setFillColor(t.snowfall ? t.accent : t.rockLight)
        ctx.fillEllipse(in: CGRect(x: p.x - s * 0.3, y: p.y - s * 0.02, width: s * 0.5, height: s * 0.25))
    }

    private static func lilypad(_ ctx: CGContext, _ p: CGPoint, _ s: CGFloat, _ t: Theme) {
        ctx.setFillColor(t.foliageLight)
        ctx.fillEllipse(in: CGRect(x: p.x - s * 0.5, y: p.y - s * 0.38, width: s, height: s * 0.76))
        ctx.setFillColor(t.foliageDark)
        ctx.fillEllipse(in: CGRect(x: p.x - s * 0.18, y: p.y - s * 0.12, width: s * 0.36, height: s * 0.26))
        if Int(p.x + p.y) % 3 == 0 {
            ctx.setFillColor(t.accent)
            circle(ctx, CGPoint(x: p.x + s * 0.12, y: p.y + s * 0.06), s * 0.14)
        }
    }
}
