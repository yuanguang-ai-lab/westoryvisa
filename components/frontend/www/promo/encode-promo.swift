import AppKit
import AVFoundation
import CoreVideo
import ImageIO

let arguments = CommandLine.arguments
guard arguments.count == 3 else {
    fputs("Usage: swift encode-promo.swift <frames-directory> <output.mp4>\n", stderr)
    exit(2)
}

let frameDirectory = URL(fileURLWithPath: arguments[1], isDirectory: true)
let outputURL = URL(fileURLWithPath: arguments[2])
let fileManager = FileManager.default
let frameURLs = try fileManager.contentsOfDirectory(
    at: frameDirectory,
    includingPropertiesForKeys: nil,
    options: [.skipsHiddenFiles]
).filter { $0.pathExtension.lowercased() == "png" }
 .sorted { $0.lastPathComponent < $1.lastPathComponent }

guard let firstURL = frameURLs.first,
      let firstSource = CGImageSourceCreateWithURL(firstURL as CFURL, nil),
      let firstImage = CGImageSourceCreateImageAtIndex(firstSource, 0, nil) else {
    fputs("No readable PNG frames found.\n", stderr)
    exit(3)
}

let width = firstImage.width
let height = firstImage.height
let frameRate: Int32 = 25

if fileManager.fileExists(atPath: outputURL.path) {
    try fileManager.removeItem(at: outputURL)
}

let writer = try AVAssetWriter(outputURL: outputURL, fileType: .mp4)
let videoSettings: [String: Any] = [
    AVVideoCodecKey: AVVideoCodecType.h264,
    AVVideoWidthKey: width,
    AVVideoHeightKey: height,
    AVVideoCompressionPropertiesKey: [
        AVVideoAverageBitRateKey: 10_000_000,
        AVVideoExpectedSourceFrameRateKey: frameRate,
        AVVideoMaxKeyFrameIntervalKey: 50,
        AVVideoProfileLevelKey: AVVideoProfileLevelH264HighAutoLevel
    ]
]
let input = AVAssetWriterInput(mediaType: .video, outputSettings: videoSettings)
input.expectsMediaDataInRealTime = false

let pixelAttributes: [String: Any] = [
    kCVPixelBufferPixelFormatTypeKey as String: Int(kCVPixelFormatType_32ARGB),
    kCVPixelBufferWidthKey as String: width,
    kCVPixelBufferHeightKey as String: height,
    kCVPixelBufferIOSurfacePropertiesKey as String: [:]
]
let adaptor = AVAssetWriterInputPixelBufferAdaptor(
    assetWriterInput: input,
    sourcePixelBufferAttributes: pixelAttributes
)

guard writer.canAdd(input) else {
    fputs("Unable to add the H.264 video input.\n", stderr)
    exit(4)
}
writer.add(input)
guard writer.startWriting() else {
    fputs("Unable to start video writer: \(writer.error?.localizedDescription ?? "unknown error")\n", stderr)
    exit(5)
}
writer.startSession(atSourceTime: .zero)

let colorSpace = CGColorSpaceCreateDeviceRGB()

for (index, frameURL) in frameURLs.enumerated() {
    while !input.isReadyForMoreMediaData {
        usleep(1_000)
    }

    try autoreleasepool {
        guard let source = CGImageSourceCreateWithURL(frameURL as CFURL, nil),
              let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else {
            throw NSError(domain: "DocFlowPromo", code: 10, userInfo: [NSLocalizedDescriptionKey: "Unreadable frame: \(frameURL.lastPathComponent)"])
        }

        var pixelBuffer: CVPixelBuffer?
        let status = CVPixelBufferCreate(
            kCFAllocatorDefault,
            width,
            height,
            kCVPixelFormatType_32ARGB,
            pixelAttributes as CFDictionary,
            &pixelBuffer
        )
        guard status == kCVReturnSuccess, let buffer = pixelBuffer else {
            throw NSError(domain: "DocFlowPromo", code: 11, userInfo: [NSLocalizedDescriptionKey: "Unable to create pixel buffer"])
        }

        CVPixelBufferLockBaseAddress(buffer, [])
        defer { CVPixelBufferUnlockBaseAddress(buffer, []) }
        guard let baseAddress = CVPixelBufferGetBaseAddress(buffer),
              let context = CGContext(
                data: baseAddress,
                width: width,
                height: height,
                bitsPerComponent: 8,
                bytesPerRow: CVPixelBufferGetBytesPerRow(buffer),
                space: colorSpace,
                bitmapInfo: CGImageAlphaInfo.premultipliedFirst.rawValue | CGBitmapInfo.byteOrder32Big.rawValue
              ) else {
            throw NSError(domain: "DocFlowPromo", code: 12, userInfo: [NSLocalizedDescriptionKey: "Unable to create frame context"])
        }

        context.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))
        let presentationTime = CMTime(value: CMTimeValue(index), timescale: frameRate)
        guard adaptor.append(buffer, withPresentationTime: presentationTime) else {
            throw NSError(domain: "DocFlowPromo", code: 13, userInfo: [NSLocalizedDescriptionKey: writer.error?.localizedDescription ?? "Unable to append frame"])
        }
    }

    if index % 100 == 0 || index == frameURLs.count - 1 {
        print("Encoded \(index + 1) / \(frameURLs.count) frames")
    }
}

input.markAsFinished()
let semaphore = DispatchSemaphore(value: 0)
writer.finishWriting { semaphore.signal() }
semaphore.wait()

guard writer.status == .completed else {
    fputs("Video encoding failed: \(writer.error?.localizedDescription ?? "unknown error")\n", stderr)
    exit(6)
}

print(outputURL.path)
