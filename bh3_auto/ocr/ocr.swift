// macOS Vision OCR：swift ocr.swift <image.png> [--min-conf N]
// 输出 JSON 行: {"text": "...", "conf": 0.x, "x": cx, "y": cy, "w": w, "h": h}（图像原始像素坐标）
import Foundation
import Vision
import AppKit

let args = CommandLine.arguments
guard args.count >= 2 else { print("usage: ocr.swift <image>"); exit(1) }
let path = args[1]
guard let img = NSImage(contentsOfFile: path),
      let tiff = img.tiffRepresentation,
      let rep = NSBitmapImageRep(data: tiff),
      let cg = rep.cgImage else { print("load fail"); exit(1) }

let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.recognitionLanguages = ["zh-Hans", "en-US"]
request.usesLanguageCorrection = false

let handler = VNImageRequestHandler(cgImage: cg, options: [:])
try handler.perform([request])

let scaleX = Double(cg.width)
let scaleY = Double(cg.height)
for obs in request.results ?? [] {
    guard let cand = obs.topCandidates(1).first else { continue }
    let b = obs.boundingBox // 归一化，左下原点
    let x = b.origin.x * scaleX
    let y = (1 - b.origin.y - b.height) * scaleY
    let w = b.width * scaleX
    let h = b.height * scaleY
    let text = cand.string.replacingOccurrences(of: "\"", with: "'")
    print("{\"text\": \"\(text)\", \"conf\": \(cand.confidence), \"x\": \(Int(x)), \"y\": \(Int(y)), \"w\": \(Int(w)), \"h\": \(Int(h))}")
}
