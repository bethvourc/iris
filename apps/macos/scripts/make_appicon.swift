#!/usr/bin/env swift
//
// make_appicon.swift — generate the macOS AppIcon asset from the Iris brand
// mark (the SiriNew wave-in-circle), so the app, Dock, Finder, and DMG all show
// the logo. Run once and commit the result; re-run to update the artwork.
//
//   swift apps/macos/scripts/make_appicon.swift [AppIcon.appiconset dir]
//
// The icon is deliberately restrained, matching the app's design language: a
// dark rounded-rect ("squircle") with the white brand glyph centered. macOS does
// not auto-mask app icons, so the rounded shape is drawn here.

import AppKit

let wavePath =
    "M11.7805 14C10.4461 15.3922 8.56592 17 7 17C4.23858 17 2 14.7614 2 12C2 "
    + "9.23858 4.23858 7 7 7C12.0899 7 13.5399 15.5 18.5217 15.5C20.4427 15.5 "
    + "22 13.933 22 12C22 10.067 20.4427 8.5 18.5217 8.5C17.6263 8.5 16.4746 "
    + "9.26045 15.5 10.0724"

/// Brand glyph as SVG, stroked white, sized for rasterization.
func glyphSVG(size: Int) -> String {
    """
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" \
    width="\(size)" height="\(size)" fill="none" stroke="white" \
    stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">\
    <circle cx="12" cy="12" r="10"/><path d="\(wavePath)"/></svg>
    """
}

func color(_ hex: UInt32) -> NSColor {
    NSColor(
        srgbRed: CGFloat((hex >> 16) & 0xFF) / 255,
        green: CGFloat((hex >> 8) & 0xFF) / 255,
        blue: CGFloat(hex & 0xFF) / 255, alpha: 1
    )
}

/// Render one square icon at an exact pixel size.
func renderPNG(pixel: Int) -> Data {
    let n = CGFloat(pixel)
    let rep = NSBitmapImageRep(
        bitmapDataPlanes: nil, pixelsWide: pixel, pixelsHigh: pixel,
        bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
        colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0
    )!
    let ctx = NSGraphicsContext(bitmapImageRep: rep)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = ctx

    // Squircle background (macOS icon grid: ~80% of the canvas, ~22% radius).
    let margin = n * 0.098
    let rect = CGRect(x: margin, y: margin, width: n - 2 * margin, height: n - 2 * margin)
    let path = NSBezierPath(
        roundedRect: rect, xRadius: rect.width * 0.224, yRadius: rect.width * 0.224
    )
    color(0x25_25_27).setFill()   // DesignSystem surface (dark)
    path.fill()

    // Centered brand glyph.
    let glyph = n * 0.52
    if let image = NSImage(data: Data(glyphSVG(size: Int(glyph)).utf8)) {
        let origin = (n - glyph) / 2
        image.draw(
            in: CGRect(x: origin, y: origin, width: glyph, height: glyph),
            from: .zero, operation: .sourceOver, fraction: 1
        )
    }

    NSGraphicsContext.restoreGraphicsState()
    return rep.representation(using: .png, properties: [:])!
}

// --- write the appiconset ----------------------------------------------------
let args = CommandLine.arguments
let scriptDir = URL(fileURLWithPath: args[0]).deletingLastPathComponent()
let defaultSet = scriptDir
    .deletingLastPathComponent()        // apps/macos
    .appending(path: "Iris/Assets.xcassets/AppIcon.appiconset")
let outDir = args.count > 1 ? URL(fileURLWithPath: args[1]) : defaultSet

let fm = FileManager.default
try? fm.createDirectory(at: outDir, withIntermediateDirectories: true)
// Asset catalog root needs its own Contents.json.
let catalogRoot = outDir.deletingLastPathComponent()
try? Data(#"{"info":{"author":"xcode","version":1}}"#.utf8)
    .write(to: catalogRoot.appending(path: "Contents.json"))

for px in [16, 32, 64, 128, 256, 512, 1024] {
    let url = outDir.appending(path: "icon_\(px).png")
    try! renderPNG(pixel: px).write(to: url)
    FileHandle.standardError.write(Data("wrote \(url.lastPathComponent)\n".utf8))
}

// size, scale, source-pixel-file (reusing the @2x file as the next @1x).
let entries: [(String, String, Int)] = [
    ("16x16", "1x", 16), ("16x16", "2x", 32),
    ("32x32", "1x", 32), ("32x32", "2x", 64),
    ("128x128", "1x", 128), ("128x128", "2x", 256),
    ("256x256", "1x", 256), ("256x256", "2x", 512),
    ("512x512", "1x", 512), ("512x512", "2x", 1024),
]
let images = entries.map { size, scale, px in
    """
        {"idiom":"mac","size":"\(size)","scale":"\(scale)","filename":"icon_\(px).png"}
    """.trimmingCharacters(in: .whitespaces)
}.joined(separator: ",\n    ")
let contents = """
{
  "images": [
    \(images)
  ],
  "info": {"author":"xcode","version":1}
}
"""
try! Data(contents.utf8).write(to: outDir.appending(path: "Contents.json"))
FileHandle.standardError.write(Data("wrote AppIcon.appiconset/Contents.json\n".utf8))
