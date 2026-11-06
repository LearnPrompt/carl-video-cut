// 人脸框逐时刻检测（macOS Vision，不下载模型）：给真人画面排版时避让人脸、按脸居中裁小窗。
//   swift faces.swift <视频> [间隔秒=0.5] > faces.json
// 输出 {step, duration, frames:[{t, box:[x0,y0,x1,y1] 或 null}]}，坐标归一化、原点左上；一帧多张脸时取面积最大的。
import AVFoundation
import Vision

let args = CommandLine.arguments
guard args.count >= 2 else { FileHandle.standardError.write("用法：swift faces.swift <视频> [间隔秒]\n".data(using: .utf8)!); exit(2) }
let step = args.count >= 3 ? Double(args[2]) ?? 0.5 : 0.5
let asset = AVURLAsset(url: URL(fileURLWithPath: args[1]))
let dur = CMTimeGetSeconds(asset.duration)
let gen = AVAssetImageGenerator(asset: asset)
gen.appliesPreferredTrackTransform = true
gen.maximumSize = CGSize(width: 640, height: 360)
gen.requestedTimeToleranceBefore = CMTime(seconds: 0.04, preferredTimescale: 600)
gen.requestedTimeToleranceAfter = CMTime(seconds: 0.04, preferredTimescale: 600)

var out: [String] = []
var t = 0.0
while t < dur {
  var box = "null"
  if let cg = try? gen.copyCGImage(at: CMTime(seconds: t, preferredTimescale: 600), actualTime: nil) {
    let req = VNDetectFaceRectanglesRequest()
    try? VNImageRequestHandler(cgImage: cg).perform([req])
    if let f = (req.results ?? []).max(by: { $0.boundingBox.width * $0.boundingBox.height < $1.boundingBox.width * $1.boundingBox.height }) {
      let b = f.boundingBox
      box = String(format: "[%.4f,%.4f,%.4f,%.4f]", b.minX, 1 - b.maxY, b.maxX, 1 - b.minY)
    }
  }
  out.append(String(format: "{\"t\":%.2f,\"box\":%@}", t, box))
  t += step
}
print("{\"step\":\(step),\"duration\":\(String(format: "%.3f", dur)),\"frames\":[\n" + out.joined(separator: ",\n") + "\n]}")
