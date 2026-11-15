// SPDX-License-Identifier: GPL-3.0-or-later
// Source-only overlay. NOT Swift-typechecked or tested on an iOS device.
import Foundation
import Combine

@MainActor
final class CodexSessionEngine: ObservableObject {
    @Published private(set) var state: CodexEngineState = .idle
    @Published private(set) var entries: [CodexTranscriptEntry] = []
    @Published private(set) var errorMessage: String?
    @Published private(set) var diagnostic: String?
    @Published private(set) var lastTurnStatus: String?
    @Published private(set) var browser: CodexBrowserPresentation?

    private(set) var generation: UInt64 = 0
    private(set) var loginId: String?
    private(set) var threadId: String?
    private(set) var turnId: String?
    private let bundle: Bundle
    private let transportFactory: @MainActor () -> CodexTransport
    private let requestTimeout: TimeInterval
    private let turnTimeout: TimeInterval
    private var transport: CodexTransport?
    private var nextRequestID: UInt64 = 0
    private var authenticated = false
    private var pending: [String: Pending] = [:]
    private var itemMap: [String: Int] = [:]
    private var completedItems: Set<String> = []
    private var earlyTurnEvents: [[String: Any]] = []
    private var earlyLoginEvents: [CodexLoginCompleted] = []
    private var loginTimer: Task<Void, Never>?
    private var turnTimer: Task<Void, Never>?

    private struct Pending {
        let method: String
        let receive: @MainActor (Any) -> Void
        let fail: @MainActor (CodexFailure) -> Void
        let timer: Task<Void, Never>
    }

    private func schedule(after seconds: TimeInterval,
                          action: @escaping @MainActor () -> Void) -> Task<Void, Never> {
        Task { @MainActor in
            do { try await Task<Never, Never>.sleep(nanoseconds: UInt64(max(0, seconds) * 1_000_000_000)) }
            catch { return }
            guard !Task<Never, Never>.isCancelled else { return }
            action()
        }
    }

    // Fresh transport per connection. Small injection seam for XCTest fixtures.
    init(bundle: Bundle = .main,
         requestTimeout: TimeInterval = 15,
         turnTimeout: TimeInterval = 30,
         transportFactory: @escaping @MainActor () -> CodexTransport = { CodexNativeTransport() }) {
        self.bundle = bundle
        self.requestTimeout = requestTimeout
        self.turnTimeout = turnTimeout
        self.transportFactory = transportFactory
    }

    var canConnect: Bool { transport == nil && (state == .idle || state == .failed) }
    var canLogin: Bool { state == .signedOut }
    var canSend: Bool { state == .ready && authenticated }
    var canStop: Bool { transport != nil && state != .stopping }
    var canStartNewConversation: Bool { state != .stopping }

    // Only the explicit Connect button calls this. No onAppear/task auto-start.
    func connect() {
        guard canConnect else { return }
        generation &+= 1
        let g = generation
        errorMessage = nil
        diagnostic = nil
        lastTurnStatus = nil
        authenticated = false
        state = .connecting
        let host = transportFactory()
        transport = host
        host.messageHandler = { [weak self] in self?.receive($0, generation: g) }
        host.diagnosticHandler = { [weak self] summary in
            guard let self = self, self.generation == g else { return }
            self.diagnostic = CodexWire.safeError(summary)
        }
        host.terminationHandler = { [weak self] exitCode, signal in
            guard let self = self, self.generation == g else { return }
            self.fail("Codex 核心退出（exit \(exitCode)，signal \(signal)）。")
        }
        host.start(in: bundle) { [weak self] error in
            guard let self = self, self.generation == g, self.state == .connecting else { return }
            if let error = error { self.fail(self.transportError(error)); return }
            self.initialize()
        }
    }

    private func initialize() {
        state = .initializing
        request("initialize", params: [
            "clientInfo": ["name": "openminis_codex", "title": "OpenMinis Codex", "version": "0.1"],
            "capabilities": ["experimentalApi": true, "explicitGatewayOauth": true]
        ], as: CodexInitializeReply.self) { [weak self] result in
            guard let self = self else { return }
            switch result {
            case .failure(let error): self.fail(error.message)
            case .success:
                self.sendInitialized()
            }
        }
    }

    private func sendInitialized() {
        guard let host = transport else { return }
        let g = generation
        host.send(["method": "initialized"]) { [weak self] error in
            guard let self = self, self.generation == g else { return }
            if let error = error { self.fail(self.transportError(error)); return }
            self.readAccount(confirmingLogin: false)
        }
    }

    private func readAccount(confirmingLogin: Bool) {
        state = confirmingLogin ? .confirmingLogin : .checkingAccount
        request("account/read", params: ["refreshToken": false], as: CodexAccountReply.self) { [weak self] result in
            guard let self = self else { return }
            switch result {
            case .failure(let error): self.fail(error.message)
            case .success(let reply):
                self.authenticated = reply.account?.type == "chatgpt"
                if confirmingLogin && !self.authenticated {
                    self.fail("登录通知已完成，但 account/read 未确认 ChatGPT 账户。")
                    return
                }
                self.loginTimer?.cancel()
                self.loginTimer = nil
                self.loginId = nil
                self.browser = nil
                self.state = self.authenticated ? .ready : .signedOut
            }
        }
    }

    func loginChatGPT() {
        guard canLogin else { return }
        errorMessage = nil
        state = .loggingIn
        loginId = nil
        earlyLoginEvents.removeAll()
        request("account/login/start", params: ["type": "chatgpt"], as: CodexLoginReply.self) { [weak self] result in
            guard let self = self else { return }
            switch result {
            case .failure(let error): self.fail(error.message)
            case .success(let reply):
                guard reply.type == "chatgpt", let id = reply.loginId, !id.isEmpty,
                      let rawURL = reply.authUrl,
                      let url = CodexWire.officialAuthorizationURL(rawURL) else {
                    self.fail("核心未返回有效的官方 HTTPS ChatGPT 浏览器登录地址。")
                    return
                }
                self.loginId = id
                self.browser = CodexBrowserPresentation(url: url)
                let g = self.generation
                self.loginTimer = self.schedule(after: 300) { [weak self] in
                    guard let self = self, self.generation == g,
                          self.state == .loggingIn else { return }
                    self.fail("官方登录等待超时，核心已停止。请手动重连并登录。")
                }
                let buffered = self.earlyLoginEvents
                self.earlyLoginEvents.removeAll()
                for event in buffered where self.generation == g { self.loginCompleted(event) }
            }
        }
    }

    func browserDidClose() {
        // Programmatic dismissal after success is harmless. A manual early close
        // cancels the login by stopping THIS host, never by starting another login.
        guard state == .loggingIn || state == .confirmingLogin else { return }
        stop(reason: "官方登录已取消，原核心已停止。")
    }

    private func loginCompleted(_ event: CodexLoginCompleted) {
        guard state == .loggingIn else { return }
        guard let expected = loginId else {
            guard earlyLoginEvents.count < 8 else { fail("登录事件队列超出限制。"); return }
            earlyLoginEvents.append(event)
            return
        }
        guard event.loginId == expected else { return }
        guard event.success else {
            fail("官方登录失败：" + CodexWire.safeError(event.error ?? "核心未提供错误详情。"))
            return
        }
        loginTimer?.cancel()
        loginTimer = nil
        // Keep Safari presented until account/read confirms the account.
        readAccount(confirmingLogin: true)
    }

    private func request<T: Decodable>(_ method: String, params: [String: Any],
                                       as type: T.Type,
                                       completion: @escaping @MainActor (Result<T, CodexFailure>) -> Void) {
        guard let host = transport else {
            completion(.failure(CodexFailure(message: "核心未连接。")))
            return
        }
        nextRequestID &+= 1
        let id = String(nextRequestID)
        let g = generation
        let timer = schedule(after: requestTimeout) { [weak self] in
            guard let self = self, self.generation == g, self.pending[id] != nil else { return }
            self.fail("RPC \(method) 等待超时；核心已停止，未自动重发。")
        }
        pending[id] = Pending(method: method, receive: { object in
            do { completion(.success(try CodexWire.decode(type, from: object))) }
            catch { completion(.failure(CodexFailure(message: "RPC \(method) 返回了无法解析的响应。"))) }
        }, fail: { completion(.failure($0)) }, timer: timer)
        host.send(["id": id, "method": method, "params": params]) { [weak self] error in
            guard let self = self, self.generation == g, let error = error else { return }
            self.fail(self.transportError(error))
        }
    }

    func send(text: String) {
        let text = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard canSend, !text.isEmpty else { return }
        guard text.utf8.count <= 32_768 else {
            errorMessage = "本切片每条消息最多 32 KiB 文本。"
            return
        }
        errorMessage = nil
        lastTurnStatus = nil
        entries.append(CodexTranscriptEntry(id: "user-" + UUID().uuidString,
                                           role: .user, text: text, finished: true))
        if let thread = threadId {
            startTurn(text: text, thread: thread)
            return
        }
        guard let workspace = transport?.workingDirectory else {
            fail("核心没有提供独立工作目录，拒绝启动会话。")
            return
        }
        state = .startingThread
        request("thread/start", params: [
            "cwd": workspace + "/empty",
            "sandbox": "read-only", "approvalPolicy": "untrusted",
            "ephemeral": true, "environments": [Any]()
        ], as: CodexThreadReply.self) { [weak self] result in
            guard let self = self else { return }
            switch result {
            case .failure(let error): self.fail(error.message)
            case .success(let reply):
                guard !reply.thread.id.isEmpty else { self.fail("核心返回了空 thread ID。"); return }
                self.threadId = reply.thread.id
                self.startTurn(text: text, thread: reply.thread.id)
            }
        }
    }

    private func startTurn(text: String, thread: String) {
        state = .startingTurn
        turnId = nil
        earlyTurnEvents.removeAll()
        itemMap.removeAll()
        completedItems.removeAll()
        request("turn/start", params: [
            "threadId": thread,
            "input": [["type": "text", "text": text, "text_elements": [Any]()]],
            "environments": [Any](), "approvalPolicy": "untrusted",
            "sandboxPolicy": ["type": "readOnly", "networkAccess": false]
        ], as: CodexTurnReply.self) { [weak self] result in
            guard let self = self else { return }
            switch result {
            case .failure(let error): self.fail(error.message)
            case .success(let reply):
                guard !reply.turn.id.isEmpty else { self.fail("核心返回了空 turn ID。"); return }
                self.turnId = reply.turn.id
                self.state = .streaming
                self.armTurnTimeout()
                let buffered = self.earlyTurnEvents
                self.earlyTurnEvents.removeAll()
                let g = self.generation
                for event in buffered where self.generation == g {
                    self.receiveNotification(event)
                }
                guard self.generation == g, self.turnId == reply.turn.id else { return }
                // In-progress snapshots may already contain buffered delta text.
                // Use notifications for streaming; only terminal snapshots calibrate.
                if reply.turn.status != "inProgress" { self.finishTurn(reply.turn) }
            }
        }
    }

    func interruptOrStop() {
        guard canStop else { return }
        // Before start responses there is no authoritative turn ID to interrupt.
        // Kill and reap the host instead; ignoring an early Stop could run a turn.
        guard state == .streaming, let thread = threadId, let turn = turnId else {
            stop(reason: "操作已停止；需要时请手动连接。")
            return
        }
        state = .interrupting
        armTurnTimeout(seconds: requestTimeout)
        request("turn/interrupt", params: ["threadId": thread, "turnId": turn],
                as: CodexEmptyReply.self) { [weak self] result in
            guard let self = self else { return }
            if case .failure(let error) = result { self.fail(error.message) }
            // An interrupt ACK is not a completed turn. Wait for its terminal event.
        }
    }

    func stop(reason: String? = nil) {
        guard transport != nil else {
            if let reason = reason { diagnostic = reason }
            return
        }
        shutdown(error: nil, reason: reason)
    }

    func newConversation() {
        guard canStartNewConversation else { return }
        stop(reason: "已清空临时对话及登录；请手动连接。")
        entries.removeAll()
        lastTurnStatus = nil
    }

    func movedToBackground() {
        guard transport != nil else { return }
        stop(reason: "已进入后台，Codex 已停止。回到前台后需手动连接并登录。")
    }

    private func armTurnTimeout(seconds: TimeInterval? = nil) {
        turnTimer?.cancel()
        let g = generation
        let turn = turnId
        turnTimer = schedule(after: seconds ?? turnTimeout) { [weak self] in
            guard let self = self, self.generation == g, self.turnId == turn,
                  self.state == .streaming || self.state == .interrupting else { return }
            self.fail("回合等待超时，核心已停止；未自动重发，请检查后手动重连。")
        }
    }

    private func receive(_ message: [String: Any], generation g: UInt64) {
        guard generation == g, transport != nil, state != .stopping else { return }
        if message["method"] != nil {
            // The native host rejects unauthorized requests. Retain fail-closed
            // behavior for injected transports or an incompatible future host too.
            guard message["id"] == nil else {
                fail("收到此文本切片不支持的服务器请求，核心已停止。")
                return
            }
            receiveNotification(message)
            return
        }
        guard let id = message["id"] as? String, let callback = pending.removeValue(forKey: id) else { return }
        callback.timer.cancel()
        if let error = message["error"] as? [String: Any] {
            let code = (error["code"] as? NSNumber)?.intValue
            let detail = (error["message"] as? String).map(CodexWire.safeError) ?? "未提供错误信息"
            callback.fail(CodexFailure(message: "RPC 失败\(code.map { "（\($0)）" } ?? "")：\(detail)"))
        } else if let result = message["result"], message["error"] == nil {
            callback.receive(result)
        } else {
            callback.fail(CodexFailure(message: "核心返回了无效 RPC envelope。"))
        }
    }

    private func receiveNotification(_ message: [String: Any]) {
        guard let method = message["method"] as? String,
              let params = message["params"] as? [String: Any] else { return }
        if method == "account/login/completed" {
            guard let event = try? CodexWire.decode(CodexLoginCompleted.self, from: params) else {
                if state == .loggingIn { fail("登录完成通知格式无效。") }
                return
            }
            loginCompleted(event)
            return
        }
        let turnMethods: Set<String> = [
            "item/started", "item/agentMessage/delta", "item/completed", "turn/completed",
            "item/reasoning/summaryTextDelta", "item/reasoning/textDelta", "error"
        ]
        guard turnMethods.contains(method), let thread = threadId,
              params["threadId"] as? String == thread else { return }
        if state == .startingTurn && turnId == nil {
            guard earlyTurnEvents.count < 256,
                  let data = try? JSONSerialization.data(withJSONObject: message),
                  data.count <= 262_144 else {
                fail("回合启动期间事件队列超出限制。")
                return
            }
            earlyTurnEvents.append(message)
            return
        }
        guard state == .streaming || state == .interrupting, let turn = turnId else { return }
        let eventTurn = method == "turn/completed"
            ? (params["turn"] as? [String: Any])?["id"] as? String
            : params["turnId"] as? String
        guard eventTurn == turn else { return }
        // Do not extend the cancellation deadline with late streaming deltas.
        if state == .streaming { armTurnTimeout() }
        switch method {
        case "item/agentMessage/delta":
            guard let item = params["itemId"] as? String, !item.isEmpty,
                  let delta = params["delta"] as? String else { fail("文本增量格式无效。"); return }
            updateItem(id: item, text: delta, final: false)
        case "item/completed":
            guard let raw = params["item"],
                  let item = try? CodexWire.decode(CodexItem.self, from: raw) else {
                fail("item/completed 格式无效。")
                return
            }
            completeItem(item)
        case "turn/completed":
            guard let raw = params["turn"],
                  let event = try? CodexWire.decode(CodexTurn.self, from: raw) else {
                fail("turn/completed 格式无效。")
                return
            }
            finishTurn(event)
        case "error":
            if let error = params["error"] as? [String: Any], let message = error["message"] as? String {
                errorMessage = "核心报告：" + CodexWire.safeError(message)
            }
        default: break
        }
    }

    private func updateItem(id: String, text: String, final: Bool) {
        guard let thread = threadId, let turn = turnId else { return }
        if !final && completedItems.contains(id) { return }
        if let index = itemMap[id] {
            if final { entries[index].text = text }
            else { entries[index].text += text }
            entries[index].finished = final
        } else {
            guard itemMap.count < 512 else { fail("回合文本项数量超出限制。"); return }
            itemMap[id] = entries.count
            entries.append(CodexTranscriptEntry(id: thread + ":" + turn + ":" + id,
                                               role: .assistant, text: text, finished: final))
        }
        if final { completedItems.insert(id) }
    }

    private func completeItem(_ item: CodexItem) {
        guard item.type == "agentMessage" else { return }
        guard !item.id.isEmpty, let text = item.text else { fail("完成的文本项缺少 ID 或正文。"); return }
        // Replacement, never append: repeated completed/snapshot cannot duplicate text.
        updateItem(id: item.id, text: text, final: true)
    }

    private func finishTurn(_ turn: CodexTurn) {
        guard turn.id == turnId else { return }
        guard ["completed", "interrupted", "failed"].contains(turn.status) else {
            fail("turn/completed 包含非终态状态：" + CodexWire.safeError(turn.status))
            return
        }
        if turn.status == "completed" && turn.error != nil {
            fail("核心返回 completed 状态及错误，无法确认回合成功。")
            return
        }
        let g = generation
        for item in turn.items {
            completeItem(item)
            guard generation == g else { return }
        }
        for index in itemMap.values { entries[index].finished = true }
        turnTimer?.cancel()
        turnTimer = nil
        let interruptIDs = pending.compactMap { $0.value.method == "turn/interrupt" ? $0.key : nil }
        for id in interruptIDs {
            guard let callback = pending.removeValue(forKey: id) else { continue }
            callback.timer.cancel()
            // The real terminal event retires this ACK wait; a late ACK is ignored.
        }
        lastTurnStatus = turn.status
        turnId = nil
        earlyTurnEvents.removeAll()
        itemMap.removeAll()
        completedItems.removeAll()
        if turn.status == "failed" {
            fail("回合失败：" + CodexWire.safeError(turn.error?.message ?? "核心未提供错误详情。"))
        } else {
            state = .ready
        }
    }

    private func transportError(_ error: Error) -> String {
        let ns = error as NSError
        return "核心传输错误（\(CodexWire.safeError(ns.domain)) \(ns.code)）：" +
            CodexWire.safeError(ns.localizedDescription)
    }

    private func fail(_ message: String) {
        guard state != .stopping else { return }
        shutdown(error: CodexFailure(message: CodexWire.safeError(message)), reason: nil)
    }

    private func shutdown(error: CodexFailure?, reason: String?) {
        guard state != .stopping else { return }
        // Invalidate BEFORE calling pending failures/stop. Old callbacks can never
        // mutate the next connection; don't reconnect until waitpid/cleanup finishes.
        generation &+= 1
        let g = generation
        let oldHost = transport
        for index in entries.indices { entries[index].finished = true }
        state = .stopping
        errorMessage = error?.message
        if let reason = reason { diagnostic = reason }
        if turnId != nil || !earlyTurnEvents.isEmpty {
            lastTurnStatus = error == nil ? "stopped" : "failed"
        }
        loginTimer?.cancel()
        loginTimer = nil
        turnTimer?.cancel()
        turnTimer = nil
        let callbacks = Array(pending.values)
        pending.removeAll()
        for callback in callbacks {
            callback.timer.cancel()
            callback.fail(error ?? .cancelled)
        }
        authenticated = false
        loginId = nil
        threadId = nil
        turnId = nil
        browser = nil
        earlyTurnEvents.removeAll()
        earlyLoginEvents.removeAll()
        itemMap.removeAll()
        completedItems.removeAll()
        oldHost?.messageHandler = nil
        oldHost?.diagnosticHandler = nil
        oldHost?.terminationHandler = nil
        guard let host = oldHost else {
            transport = nil
            state = error == nil ? .idle : .failed
            return
        }
        host.stop { [weak self] in
            guard let self = self, self.generation == g else { return }
            self.transport = nil
            self.state = error == nil ? .idle : .failed
        }
    }
}
