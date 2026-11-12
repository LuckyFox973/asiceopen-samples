// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "Krajinka",
    platforms: [.macOS(.v13)],
    targets: [
        .executableTarget(
            name: "Krajinka",
            path: "Sources/Krajinka"
        )
    ]
)
