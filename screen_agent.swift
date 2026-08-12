import Foundation
import AppKit
import ApplicationServices
import ScreenCaptureKit
import Vision

struct AgentField: Decodable {
    let id: String
    let label: String
    let value: String
}

struct AgentJob: Decodable {
    let version: Int
    let jobId: String
    let caseId: String
    let targetUrl: String
    let targetMarker: String
    let fields: [AgentField]
}

struct AgentLog: Codable {
    let at: String
    let type: String
    let message: String
}

struct AgentStatus: Codable {
    let jobId: String
    let caseId: String
    let state: String
    let message: String
    let completedFields: Int
    let totalFields: Int
    let logs: [AgentLog]
    let updatedAt: String
}

struct OCRLine {
    let text: String
    let box: CGRect
}

enum AgentFailure: Error, CustomStringConvertible {
    case invalidArguments
    case unsafeTarget
    case invalidJob(String)
    case permissionRequired(String)
    case screenUnavailable
    case targetMarkerMissing
    case operatorStopped

    var description: String {
        switch self {
        case .invalidArguments:
            return "缺少 --job 或 --url 参数"
        case .unsafeTarget:
            return "安全策略拒绝：桌面 Agent 仅允许访问本机 Visa Form Practice Lab 导入页"
        case .invalidJob(let message):
            return "任务无效：\(message)"
        case .permissionRequired(let message):
            return message
        case .screenUnavailable:
            return "无法读取主屏幕，请检查屏幕录制权限"
        case .targetMarkerMissing:
            return "未识别到 VISA FORM PRACTICE LAB 水印，Agent 已停止，未执行点击"
        case .operatorStopped:
            return "顾问按下 ESC 或点击急停，Agent 已停止"
        }
    }
}

final class LocalScreenAgent {
    private static let allowedFields: [String: String] = [
        "personal.surname": "Surname",
        "personal.givenNames": "Given Names",
        "personal.dateOfBirth": "Date of Birth",
        "personal.placeOfBirth": "Place of Birth",
        "passport.number": "Passport Number",
        "passport.issueDate": "Passport Issue Date",
        "passport.expiration": "Passport Expiration Date",
        "travel.visaType": "Purpose of Trip",
        "travel.arrivalDate": "Intended Date of Arrival",
        "contact.usAddress": "Address Where You Will Stay",
        "contact.organizationName": "U.S. Contact Organization",
        "contact.phone": "U.S. Contact Phone",
        "work.employerName": "Present Employer or School",
        "education.schoolName": "School Name",
        "education.sevisId": "SEVIS ID",
        "education.programNumber": "Program Number",
    ]
    private let job: AgentJob
    private let jobURL: URL
    private let targetURL: URL
    private let statusURL: URL
    private let stopURL: URL
    private var logs: [AgentLog] = []
    private var completedFields = 0
    private let displayID = CGMainDisplayID()

    init(jobURL: URL, targetURL: URL) throws {
        let data = try Data(contentsOf: jobURL)
        let decoded = try JSONDecoder().decode(AgentJob.self, from: data)
        guard decoded.version == 1 else {
            throw AgentFailure.invalidJob("不支持的任务版本")
        }
        guard decoded.targetMarker == "VISA FORM PRACTICE LAB" else {
            throw AgentFailure.invalidJob("本地安全水印不匹配")
        }
        guard Self.isSafeTarget(targetURL, jobId: decoded.jobId), decoded.targetUrl == targetURL.absoluteString else {
            throw AgentFailure.unsafeTarget
        }
        let filteredFields = decoded.fields.filter { field in
            Self.allowedFields[field.id] == field.label
                && !field.value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                && field.value.count <= 500
        }
        guard !filteredFields.isEmpty, filteredFields.count == decoded.fields.count else {
            throw AgentFailure.invalidJob("包含未知字段、空值或超长字段")
        }
        self.job = AgentJob(
            version: decoded.version,
            jobId: decoded.jobId,
            caseId: decoded.caseId,
            targetUrl: decoded.targetUrl,
            targetMarker: decoded.targetMarker,
            fields: filteredFields
        )
        self.jobURL = jobURL
        self.targetURL = targetURL
        self.statusURL = jobURL.deletingPathExtension().appendingPathExtension("status.json")
        self.stopURL = jobURL.deletingPathExtension().appendingPathExtension("stop")
    }

    private static func isSafeTarget(_ url: URL, jobId: String) -> Bool {
        guard let components = URLComponents(url: url, resolvingAgainstBaseURL: false),
              components.scheme == "http",
              ["127.0.0.1", "localhost", "::1"].contains(components.host ?? ""),
              components.path == "/screen-agent-import.html" else {
            return false
        }
        return components.queryItems?.contains(where: {
            $0.name == "job" && $0.value == jobId
        }) == true
    }

    func run() {
        defer { redactJobValues() }
        do {
            try updateStatus(state: "checking_permissions", message: "正在检查 macOS 屏幕录制和辅助功能权限")
            try requirePermissions()
            try ensureNotStopped()
            addLog(type: "info", message: "权限检查通过，准备打开独立的 Visa Form Practice Lab")
            try updateStatus(state: "opening", message: "正在打开 Visa Form Practice Lab 本机导入页")
            NSWorkspace.shared.open(targetURL)
            Thread.sleep(forTimeInterval: 3.0)
            postShortcut(keyCode: 126, flags: .maskCommand)
            Thread.sleep(forTimeInterval: 0.6)

            let initialLines = try recognizeScreen()
            guard containsMarker(initialLines) else {
                throw AgentFailure.targetMarkerMissing
            }
            addLog(type: "success", message: "Screen Observer 已识别 Practice Lab 本地安全水印")
            try updateStatus(state: "running", message: "正在通过屏幕 OCR 定位并填写客户档案客观字段")

            for field in job.fields {
                try ensureNotStopped()
                let filled = try locateAndFill(field)
                if filled {
                    completedFields += 1
                    addLog(type: "success", message: "已填写 \(field.label)")
                } else {
                    addLog(type: "warning", message: "未在屏幕中定位到 \(field.label)，已跳过")
                }
                try updateStatus(
                    state: "running",
                    message: "已处理 \(completedFields) / \(job.fields.count) 个字段"
                )
            }

            try ensureNotStopped()
            try moveToSafetyGate()
            addLog(type: "warning", message: "Safety Guard 已在 Security and Background 前强制暂停")
            addLog(type: "info", message: "未处理验证码、法律声明、付款或最终提交")
            try updateStatus(
                state: "handoff",
                message: "客观字段填写完成，安全与背景问题等待顾问人工接管"
            )
            print("DocFlow Screen Agent finished safely: \(completedFields)/\(job.fields.count) fields")
        } catch AgentFailure.operatorStopped {
            addLog(type: "warning", message: "顾问急停了本地 Screen Agent")
            try? updateStatus(state: "stopped", message: AgentFailure.operatorStopped.description)
        } catch {
            let message = (error as? AgentFailure)?.description ?? "Screen Agent 运行失败：\(error.localizedDescription)"
            addLog(type: "error", message: message)
            try? updateStatus(state: "blocked", message: message)
            fputs("\(message)\n", stderr)
        }
    }

    private func requirePermissions() throws {
        let promptKey = kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String
        let options = [promptKey: true] as CFDictionary
        guard AXIsProcessTrustedWithOptions(options) else {
            throw AgentFailure.permissionRequired(
                "需要辅助功能权限：系统设置 → 隐私与安全性 → 辅助功能，允许 Terminal 或启动本项目的终端"
            )
        }
        if !CGPreflightScreenCaptureAccess() {
            _ = CGRequestScreenCaptureAccess()
            throw AgentFailure.permissionRequired(
                "需要屏幕录制权限：系统设置 → 隐私与安全性 → 屏幕与系统录音，允许 Terminal 后重新启动 Agent"
            )
        }
    }

    private func locateAndFill(_ field: AgentField) throws -> Bool {
        let expectedCompleted = completedFields + 1
        for attempt in 0..<12 {
            try ensureNotStopped()
            let lines = try recognizeScreen()
            guard containsMarker(lines) else {
                throw AgentFailure.targetMarkerMissing
            }
            if let line = bestMatch(label: field.label, in: lines) {
                let point = attempt.isMultiple(of: 2)
                    ? textPoint(for: line)
                    : inputPoint(for: line)
                moveMouse(to: point)
                Thread.sleep(forTimeInterval: 0.22)
                click(at: point)
                Thread.sleep(forTimeInterval: 0.24)
                replaceFocusedText(with: field.value)
                Thread.sleep(forTimeInterval: 0.52)

                let verificationLines = try recognizeScreen()
                guard containsMarker(verificationLines) else {
                    throw AgentFailure.targetMarkerMissing
                }
                if confirmsField(
                    field,
                    completedCount: expectedCompleted,
                    in: verificationLines
                ) {
                    postKey(keyCode: 48)
                    Thread.sleep(forTimeInterval: 0.24)
                    return true
                }
            }
            if attempt < 11 {
                postKey(keyCode: 121)
                Thread.sleep(forTimeInterval: 0.42)
            }
        }
        return false
    }

    private func moveToSafetyGate() throws {
        for attempt in 0..<12 {
            try ensureNotStopped()
            let lines = try recognizeScreen()
            guard containsMarker(lines) else {
                throw AgentFailure.targetMarkerMissing
            }
            if let line = bestMatch(label: "Security and Background", in: lines) {
                moveMouse(to: textPoint(for: line))
                return
            }
            if attempt < 11 {
                scroll(lines: -7)
                Thread.sleep(forTimeInterval: 0.38)
            }
        }
    }

    private func recognizeScreen() throws -> [OCRLine] {
        let image = try captureScreen()
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.usesLanguageCorrection = false
        request.recognitionLanguages = ["en-US", "zh-Hans"]
        let handler = VNImageRequestHandler(cgImage: image, options: [:])
        try handler.perform([request])
        return (request.results ?? []).compactMap { observation in
            guard let candidate = observation.topCandidates(1).first else { return nil }
            return OCRLine(text: candidate.string, box: observation.boundingBox)
        }
    }

    private func captureScreen() throws -> CGImage {
        let semaphore = DispatchSemaphore(value: 0)
        var capturedImage: CGImage?
        var captureError: Error?
        Task {
            do {
                let content = try await SCShareableContent.excludingDesktopWindows(
                    false,
                    onScreenWindowsOnly: true
                )
                guard let display = content.displays.first(where: { $0.displayID == displayID })
                    ?? content.displays.first else {
                    throw AgentFailure.screenUnavailable
                }
                let filter = SCContentFilter(display: display, excludingWindows: [])
                let configuration = SCStreamConfiguration()
                configuration.width = display.width
                configuration.height = display.height
                configuration.showsCursor = true
                capturedImage = try await SCScreenshotManager.captureImage(
                    contentFilter: filter,
                    configuration: configuration
                )
            } catch {
                captureError = error
            }
            semaphore.signal()
        }
        semaphore.wait()
        if let captureError { throw captureError }
        guard let capturedImage else { throw AgentFailure.screenUnavailable }
        return capturedImage
    }

    private func containsMarker(_ lines: [OCRLine]) -> Bool {
        let text = normalized(lines.map(\.text).joined(separator: " "))
        return text.contains("VISAFORMPRACTICELAB")
    }

    private func bestMatch(label: String, in lines: [OCRLine]) -> OCRLine? {
        let expected = normalized(label)
        return lines.first(where: { normalized($0.text) == expected })
            ?? lines.first(where: {
                let candidate = normalized($0.text)
                return candidate.count >= 5 && (candidate.contains(expected) || expected.contains(candidate))
            })
    }

    private func confirmsField(
        _ field: AgentField,
        completedCount: Int,
        in lines: [OCRLine]
    ) -> Bool {
        let screenText = normalized(lines.map(\.text).joined(separator: " "))
        let expectedProgress = "FIELDSFILLED\(completedCount)OF\(job.fields.count)"
        let expectedField = "LASTFILLED\(normalized(field.label))"
        return screenText.contains(expectedProgress) && screenText.contains(expectedField)
    }

    private func normalized(_ value: String) -> String {
        String(value.uppercased().unicodeScalars.filter {
            CharacterSet.alphanumerics.contains($0)
        })
    }

    private func textPoint(for line: OCRLine) -> CGPoint {
        let bounds = CGDisplayBounds(displayID)
        return CGPoint(
            x: bounds.minX + line.box.midX * bounds.width,
            y: bounds.minY + (1.0 - line.box.midY) * bounds.height
        )
    }

    private func inputPoint(for line: OCRLine) -> CGPoint {
        let bounds = CGDisplayBounds(displayID)
        let labelRight = bounds.minX + line.box.maxX * bounds.width
        return CGPoint(
            x: min(bounds.maxX - 150, labelRight + min(320, bounds.width * 0.24)),
            y: bounds.minY + (1.0 - line.box.midY) * bounds.height
        )
    }

    private func moveMouse(to point: CGPoint) {
        CGEvent(mouseEventSource: nil, mouseType: .mouseMoved, mouseCursorPosition: point, mouseButton: .left)?.post(tap: .cghidEventTap)
    }

    private func click(at point: CGPoint) {
        CGEvent(mouseEventSource: nil, mouseType: .leftMouseDown, mouseCursorPosition: point, mouseButton: .left)?.post(tap: .cghidEventTap)
        Thread.sleep(forTimeInterval: 0.05)
        CGEvent(mouseEventSource: nil, mouseType: .leftMouseUp, mouseCursorPosition: point, mouseButton: .left)?.post(tap: .cghidEventTap)
    }

    private func replaceFocusedText(with value: String) {
        postShortcut(keyCode: 0, flags: .maskCommand)
        Thread.sleep(forTimeInterval: 0.05)
        var characters = Array(value.utf16)
        guard !characters.isEmpty else { return }
        let down = CGEvent(keyboardEventSource: nil, virtualKey: 0, keyDown: true)
        down?.keyboardSetUnicodeString(stringLength: characters.count, unicodeString: &characters)
        down?.post(tap: .cghidEventTap)
        let up = CGEvent(keyboardEventSource: nil, virtualKey: 0, keyDown: false)
        up?.keyboardSetUnicodeString(stringLength: characters.count, unicodeString: &characters)
        up?.post(tap: .cghidEventTap)
    }

    private func postShortcut(keyCode: CGKeyCode, flags: CGEventFlags) {
        let down = CGEvent(keyboardEventSource: nil, virtualKey: keyCode, keyDown: true)
        down?.flags = flags
        down?.post(tap: .cghidEventTap)
        let up = CGEvent(keyboardEventSource: nil, virtualKey: keyCode, keyDown: false)
        up?.flags = flags
        up?.post(tap: .cghidEventTap)
    }

    private func postKey(keyCode: CGKeyCode) {
        postShortcut(keyCode: keyCode, flags: [])
    }

    private func scroll(lines: Int32) {
        CGEvent(
            scrollWheelEvent2Source: nil,
            units: .line,
            wheelCount: 1,
            wheel1: lines,
            wheel2: 0,
            wheel3: 0
        )?.post(tap: .cghidEventTap)
    }

    private func ensureNotStopped() throws {
        if FileManager.default.fileExists(atPath: stopURL.path)
            || CGEventSource.keyState(.combinedSessionState, key: 53) {
            throw AgentFailure.operatorStopped
        }
    }

    private func addLog(type: String, message: String) {
        logs.append(AgentLog(at: Self.timestamp(), type: type, message: message))
        if logs.count > 100 { logs.removeFirst(logs.count - 100) }
    }

    private func redactJobValues() {
        guard let data = try? Data(contentsOf: jobURL),
              var payload = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              var fields = payload["fields"] as? [[String: Any]] else {
            return
        }
        for index in fields.indices {
            fields[index]["value"] = ""
            fields[index].removeValue(forKey: "source")
        }
        payload["fields"] = fields
        payload["redactedAt"] = Self.timestamp()
        guard let redacted = try? JSONSerialization.data(
            withJSONObject: payload,
            options: [.prettyPrinted, .sortedKeys]
        ) else { return }
        try? redacted.write(to: jobURL, options: .atomic)
        chmod(jobURL.path, S_IRUSR | S_IWUSR)
    }

    private func updateStatus(state: String, message: String) throws {
        let status = AgentStatus(
            jobId: job.jobId,
            caseId: job.caseId,
            state: state,
            message: message,
            completedFields: completedFields,
            totalFields: job.fields.count,
            logs: logs,
            updatedAt: Self.timestamp()
        )
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        let data = try encoder.encode(status)
        try data.write(to: statusURL, options: .atomic)
        chmod(statusURL.path, S_IRUSR | S_IWUSR)
    }

    private static func timestamp() -> String {
        ISO8601DateFormatter().string(from: Date())
    }
}

func argumentValue(_ name: String) -> String? {
    guard let index = CommandLine.arguments.firstIndex(of: name), index + 1 < CommandLine.arguments.count else {
        return nil
    }
    return CommandLine.arguments[index + 1]
}

do {
    guard let jobPath = argumentValue("--job"), let target = argumentValue("--url"), let targetURL = URL(string: target) else {
        throw AgentFailure.invalidArguments
    }
    let agent = try LocalScreenAgent(jobURL: URL(fileURLWithPath: jobPath), targetURL: targetURL)
    agent.run()
} catch {
    let message = (error as? AgentFailure)?.description ?? error.localizedDescription
    fputs("\(message)\n", stderr)
    exit(1)
}
