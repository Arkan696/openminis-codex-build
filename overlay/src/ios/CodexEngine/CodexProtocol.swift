// SPDX-License-Identifier: GPL-3.0-or-later
// Source-only overlay. NOT Swift-typechecked or tested on an iOS device.
import Foundation

// The Objective-C host delivers callbacks on main, synchronously or asynchronously.
// These closures are deliberately non-Sendable: dictionaries/pending callbacks stay
// on the main actor; no synchronous callback timing is assumed by the engine.
@MainActor
protocol CodexTransport: AnyObject {
    var workingDirectory: String? { get }
    var messageHandler: (([String: Any]) -> Void)? { get set }
    var diagnosticHandler: ((String) -> Void)? { get set }
    var terminationHandler: ((Int32, Int32) -> Void)? { get set }
    func start(in bundle: Bundle, completion: @escaping (Error?) -> Void)
    func send(_ message: [String: Any], completion: @escaping (Error?) -> Void)
    func stop(completion: @escaping () -> Void)
}

@MainActor
final class CodexNativeTransport: CodexTransport {
    private let host = OMCodexProcessHost()
    var workingDirectory: String? { host.workingDirectory }
    var messageHandler: (([String: Any]) -> Void)?
    var diagnosticHandler: ((String) -> Void)?
    var terminationHandler: ((Int32, Int32) -> Void)?

    init() {
        host.messageHandler = { [weak self] in self?.messageHandler?($0) }
        host.diagnosticHandler = { [weak self] in self?.diagnosticHandler?($0) }
        host.terminationHandler = { [weak self] in self?.terminationHandler?($0, $1) }
    }
    func start(in bundle: Bundle, completion: @escaping (Error?) -> Void) {
        host.start(in: bundle, completion: completion)
    }
    func send(_ message: [String: Any], completion: @escaping (Error?) -> Void) {
        host.send(message, completion: completion)
    }
    func stop(completion: @escaping () -> Void) { host.stop(completion: completion) }
}

enum CodexEngineState: String {
    case idle, connecting, initializing, checkingAccount, signedOut, ready
    case loggingIn, confirmingLogin, startingThread, startingTurn, streaming
    case interrupting, stopping, failed

    var title: String {
        switch self {
        case .idle: return "未连接"
        case .connecting: return "正在启动核心"
        case .initializing: return "正在握手"
        case .checkingAccount: return "正在确认账户"
        case .signedOut: return "请使用官方 ChatGPT 登录"
        case .ready: return "可以发送"
        case .loggingIn: return "等待官方浏览器授权"
        case .confirmingLogin: return "正在验证登录结果"
        case .startingThread: return "正在创建临时对话"
        case .startingTurn: return "正在启动回合"
        case .streaming: return "正在生成"
        case .interrupting: return "正在中断回合"
        case .stopping: return "正在停止并清理"
        case .failed: return "连接失败，可手动重连"
        }
    }
}

struct CodexTranscriptEntry: Identifiable {
    enum Role { case user, assistant }
    let id: String
    let role: Role
    var text: String
    var finished: Bool
}

struct CodexBrowserPresentation: Identifiable {
    let id = UUID()
    let url: URL
}

struct CodexFailure: Error, LocalizedError {
    let message: String
    var errorDescription: String? { message }
    static let cancelled = CodexFailure(message: "操作已取消，原核心已停止。")
}

// Only fields actually consumed by this slice are decoded. No email/token is kept.
struct CodexInitializeReply: Decodable { let userAgent: String }
struct CodexAccountReply: Decodable {
    struct Account: Decodable { let type: String }
    let account: Account?
    let requiresOpenaiAuth: Bool
}
struct CodexLoginReply: Decodable {
    let type: String
    let loginId: String?
    let authUrl: String?
}
struct CodexLoginCompleted: Decodable {
    let loginId: String?
    let success: Bool
    let error: String?
}
struct CodexThreadReply: Decodable {
    struct Thread: Decodable { let id: String }
    let thread: Thread
}
struct CodexTurnReply: Decodable { let turn: CodexTurn }
struct CodexTurn: Decodable {
    struct Failure: Decodable { let message: String }
    let id: String
    let status: String
    let items: [CodexItem]
    let error: Failure?
}
struct CodexItem: Decodable {
    let id: String
    let type: String
    let text: String?
}
struct CodexEmptyReply: Decodable {}

enum CodexWire {
    static func decode<T: Decodable>(_ type: T.Type, from object: Any) throws -> T {
        guard JSONSerialization.isValidJSONObject(object) else {
            throw CodexFailure(message: "RPC 结果不是 JSON 对象或数组。")
        }
        let data = try JSONSerialization.data(withJSONObject: object)
        return try JSONDecoder().decode(type, from: data)
    }

    static func officialAuthorizationURL(_ value: String) -> URL? {
        guard let c = URLComponents(string: value),
              c.scheme?.lowercased() == "https", c.user == nil, c.password == nil,
              c.port == nil || c.port == 443, let host = c.host?.lowercased(),
              !host.hasSuffix("."),
              ["openai.com", "chatgpt.com"].contains(where: {
                  host == $0 || host.hasSuffix("." + $0)
              }), let url = c.url else { return nil }
        return url
    }

    // Used for visible diagnostics/errors only; never log envelopes or auth URLs.
    static func safeError(_ value: String) -> String {
        var text = value
        let patterns = [
            #"(?i)\b(?:https?://|www\.)[^\s<>\"']+"#,
            #"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"#,
            #"(?i)(?:access[_-]?token|refresh[_-]?token|id[_-]?token|token|api[_-]?key|authorization|password|secret|code)\s*[\"']?\s*[:=]\s*[\"']?[^\s,;\"'}]+"#,
            #"(?i)\bBearer\s+\S+"#,
            #"\b[A-Za-z0-9_-]{32,}\b"#,
            #"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)?\b"#,
            #"\bsk-[A-Za-z0-9_-]+\b"#
        ]
        for pattern in patterns {
            text = text.replacingOccurrences(of: pattern, with: "[已隐藏]", options: .regularExpression)
        }
        return String(text.prefix(600))
    }
}
