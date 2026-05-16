import Foundation
import Vision
import AppKit

guard CommandLine.arguments.count == 3 else {
    fputs("Usage: remove_bg <input.png> <output.png>\n", stderr)
    exit(1)
}

let inputURL  = URL(fileURLWithPath: CommandLine.arguments[1])
let outputURL = URL(fileURLWithPath: CommandLine.arguments[2])

guard let image = NSImage(contentsOf: inputURL),
      let cgImage = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
    fputs("Failed to load image\n", stderr)
    exit(1)
}

let request = VNGenerateForegroundInstanceMaskRequest()
let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])

do {
    try handler.perform([request])
    guard let result = request.results?.first else {
        fputs("No foreground found\n", stderr)
        exit(1)
    }
    let masked = try result.generateMaskedImage(
        ofInstances: result.allInstances,
        from: handler,
        croppedToInstancesExtent: false)
    let rep = NSBitmapImageRep(cgImage: masked)
    guard let data = rep.representation(using: .png, properties: [:]) else {
        fputs("Failed to encode PNG\n", stderr)
        exit(1)
    }
    try data.write(to: outputURL)
    print("OK")
} catch {
    fputs("Error: \(error)\n", stderr)
    exit(1)
}
