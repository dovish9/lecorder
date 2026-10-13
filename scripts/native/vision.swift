import Foundation
import Vision
import AppKit
if CommandLine.arguments.count == 2 && CommandLine.arguments[1] == "--help" {
    print("Usage: vision-ocr IMAGE_PATH")
    exit(0)
}
guard CommandLine.arguments.count == 2 else {
    fputs("Usage: vision-ocr IMAGE_PATH\n", stderr)
    exit(2)
}
let url = URL(fileURLWithPath: CommandLine.arguments[1])
let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.recognitionLanguages = ["ko-KR", "en-US"]
request.usesLanguageCorrection = true
try VNImageRequestHandler(url: url).perform([request])
let lines: [[String: Any]] = (request.results ?? []).compactMap { item in
    guard let text = item.topCandidates(1).first else { return nil }
    let b = item.boundingBox
    return ["text": text.string, "confidence": text.confidence,
            "box": [b.minX, 1-b.maxY, b.width, b.height]]
}
let data = try JSONSerialization.data(withJSONObject: lines)
print(String(data: data, encoding: .utf8)!)
