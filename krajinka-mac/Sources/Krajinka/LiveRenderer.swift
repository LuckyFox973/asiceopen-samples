import AppKit
import Metal
import MetalKit

/// Kreslí živú krajinu cez Metal: statický maľovaný podklad + animácie v shaderi
/// (príboj, vlny, kaustiky, odlesky, vietor, tiene oblakov, sneh). Pri zmene
/// ikon sa starý a nový podklad plynulo prelínajú priamo v shaderi.
final class LiveRenderer: NSObject, MTKViewDelegate {
    private struct Uniforms {
        var time: Float
        var fade: Float
        var scale: Float
        var cell: Float
        var sizePts: SIMD2<Float>
        var drawable: SIMD2<Float>
        var fieldSize: SIMD2<Float>
        var theme: Int32
        var pad: Int32
    }

    let device: MTLDevice
    private let queue: MTLCommandQueue
    private let pipeline: MTLRenderPipelineState
    private let loader: MTKTextureLoader

    private var baseTex: MTLTexture?
    private var prevTex: MTLTexture?
    private var fieldTex: MTLTexture?
    private var fieldSize = SIMD2<Float>(1, 1)
    private var cell: Float = 6
    private var fadeStart: CFTimeInterval = 0
    private let fadeDuration: CFTimeInterval = 1.0
    private let startTime = CACurrentMediaTime()
    var themeIndex: Int32 = 0

    /// Posledná chyba kompilácie shadera (pre diagnostiku); nil = všetko v poriadku.
    static var lastError: String?

    init?(view: MTKView) {
        guard let device = MTLCreateSystemDefaultDevice(), let queue = device.makeCommandQueue() else {
            Self.lastError = "Metal nie je dostupný."
            return nil
        }
        do {
            let library = try device.makeLibrary(source: LiveShader.source, options: nil)
            let desc = MTLRenderPipelineDescriptor()
            desc.vertexFunction = library.makeFunction(name: "vmain")
            desc.fragmentFunction = library.makeFunction(name: "fmain")
            desc.colorAttachments[0].pixelFormat = view.colorPixelFormat
            pipeline = try device.makeRenderPipelineState(descriptor: desc)
        } catch {
            Self.lastError = "Shader sa nepodarilo skompilovať:\n\(error)"
            Self.writeErrorLog()
            return nil
        }
        self.device = device
        self.queue = queue
        self.loader = MTKTextureLoader(device: device)
        super.init()
        view.device = device
    }

    private static func writeErrorLog() {
        guard let msg = lastError else { return }
        let dir = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Logs/Krajinka", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        try? msg.write(to: dir.appendingPathComponent("shader-chyba.txt"), atomically: true, encoding: .utf8)
    }

    /// Nový podklad a pole. Pri `animated` sa prelína so starým.
    func update(_ scene: RenderedScene, animated: Bool) {
        let opts: [MTKTextureLoader.Option: Any] = [
            .SRGB: false,
            .textureUsage: NSNumber(value: MTLTextureUsage.shaderRead.rawValue),
            .textureStorageMode: NSNumber(value: MTLStorageMode.private.rawValue),
        ]
        guard let tex = try? loader.newTexture(cgImage: scene.terrain, options: opts) else { return }
        prevTex = (animated && baseTex != nil) ? baseTex : tex
        baseTex = tex
        fadeStart = CACurrentMediaTime()

        let desc = MTLTextureDescriptor.texture2DDescriptor(pixelFormat: .rgba32Float, width: scene.fieldCols,
                                                            height: scene.fieldRows, mipmapped: false)
        desc.usage = .shaderRead
        if let ft = device.makeTexture(descriptor: desc) {
            scene.fieldData.withUnsafeBytes { raw in
                ft.replace(region: MTLRegionMake2D(0, 0, scene.fieldCols, scene.fieldRows), mipmapLevel: 0,
                           withBytes: raw.baseAddress!, bytesPerRow: scene.fieldCols * 16)
            }
            fieldTex = ft
            fieldSize = SIMD2(Float(scene.fieldCols), Float(scene.fieldRows))
            cell = scene.fieldCell
        }
    }

    func mtkView(_ view: MTKView, drawableSizeWillChange size: CGSize) {}

    func draw(in view: MTKView) {
        guard let base = baseTex, let prev = prevTex, let field = fieldTex,
              let pass = view.currentRenderPassDescriptor, let drawable = view.currentDrawable,
              let cmd = queue.makeCommandBuffer(), let enc = cmd.makeRenderCommandEncoder(descriptor: pass)
        else { return }
        let now = CACurrentMediaTime()
        let size = view.drawableSize
        let pts = view.bounds.size
        var u = Uniforms(time: Float((now - startTime).truncatingRemainder(dividingBy: 3500)),
                         fade: Float(min(max((now - fadeStart) / fadeDuration, 0), 1)),
                         scale: Float(size.width / max(pts.width, 1)),
                         cell: cell,
                         sizePts: SIMD2(Float(pts.width), Float(pts.height)),
                         drawable: SIMD2(Float(size.width), Float(size.height)),
                         fieldSize: fieldSize,
                         theme: themeIndex, pad: 0)
        enc.setRenderPipelineState(pipeline)
        enc.setFragmentBytes(&u, length: MemoryLayout<Uniforms>.stride, index: 0)
        enc.setFragmentTexture(base, index: 0)
        enc.setFragmentTexture(prev, index: 1)
        enc.setFragmentTexture(field, index: 2)
        enc.drawPrimitives(type: .triangle, vertexStart: 0, vertexCount: 3)
        enc.endEncoding()
        cmd.present(drawable)
        cmd.commit()
    }
}
