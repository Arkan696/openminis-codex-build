// SPDX-License-Identifier: GPL-3.0-or-later
// NOT EXECUTED: add to an iOS XCTest target with the 3 overlay Swift files and
// OMCodexProcessHost bridging header. Fixture transport never launches the core.
import XCTest
import Foundation
import SwiftUI
import UIKit

@MainActor
private final class FixtureTransport: CodexTransport {
    var workingDirectory: String? = "/tmp/fixture-workspace"
    var messageHandler: (([String: Any]) -> Void)?
    var diagnosticHandler: ((String) -> Void)?
    var terminationHandler: ((Int32, Int32) -> Void)?
    var sent: [[String: Any]] = []
    var stopCount = 0
    var holdStopCompletion = false
    var deferredStop: (() -> Void)?
    func start(in bundle: Bundle, completion: @escaping (Error?) -> Void) { completion(nil) }
    func send(_ message: [String: Any], completion: @escaping (Error?) -> Void) {
        sent.append(message)
        completion(nil)
    }
    func stop(completion: @escaping () -> Void) {
        stopCount += 1
        if holdStopCompletion { deferredStop = completion }
        else { completion() }
    }
    func finishStop() {
        let callback = deferredStop
        deferredStop = nil
        callback?()
    }
    func last(_ method: String) -> [String: Any] {
        sent.last { $0["method"] as? String == method }!
    }
    func reply(_ method: String, _ result: [String: Any]) {
        messageHandler?(["id": last(method)["id"]!, "result": result])
    }
    func event(_ method: String, _ params: [String: Any]) {
        messageHandler?(["method": method, "params": params])
    }
}

@MainActor
final class CodexSessionEngineTests: XCTestCase {
    private func connected(_ host: FixtureTransport, timeout: TimeInterval = 15) -> CodexSessionEngine {
        let engine = CodexSessionEngine(requestTimeout: timeout, transportFactory: { host })
        engine.connect()
        host.reply("initialize", ["userAgent": "fixture"])
        XCTAssertEqual(host.sent[1]["method"] as? String, "initialized")
        host.reply("account/read", ["account": ["type": "chatgpt"], "requiresOpenaiAuth": true])
        XCTAssertEqual(engine.state, .ready)
        return engine
    }
    private func streaming(_ host: FixtureTransport) -> CodexSessionEngine {
        let engine = connected(host)
        engine.send(text: "fixture prompt")
        host.reply("thread/start", ["thread": ["id": "thread"]])
        host.reply("turn/start", ["turn": turn("inProgress")])
        XCTAssertEqual(engine.state, .streaming)
        return engine
    }
    private func turn(_ status: String, items: [[String: Any]] = []) -> [String: Any] {
        ["id": "turn", "status": status, "items": items, "error": NSNull()]
    }
    private func delta(_ host: FixtureTransport, _ text: String, turn: String = "turn") {
        host.event("item/agentMessage/delta", ["threadId": "thread", "turnId": turn,
                                                "itemId": "item", "delta": text])
    }

    func testHandshakeAndReadOnlyPolicy() {
        let host = FixtureTransport()
        let engine = connected(host)
        let initParams = host.last("initialize")["params"] as! [String: Any]
        let info = initParams["clientInfo"] as! [String: Any]
        let caps = initParams["capabilities"] as! [String: Any]
        XCTAssertEqual(info["version"] as? String, "0.1")
        XCTAssertEqual(caps["explicitGatewayOauth"] as? Bool, true)
        XCTAssertEqual(caps["experimentalApi"] as? Bool, true)
        engine.send(text: "hello")
        let thread = host.last("thread/start")["params"] as! [String: Any]
        XCTAssertEqual(thread["ephemeral"] as? Bool, true)
        XCTAssertEqual(thread["sandbox"] as? String, "read-only")
        XCTAssertEqual(thread["approvalPolicy"] as? String, "untrusted")
        XCTAssertEqual((thread["environments"] as? [Any])?.count, 0)
        host.reply("thread/start", ["thread": ["id": "thread"]])
        let params = host.last("turn/start")["params"] as! [String: Any]
        let policy = params["sandboxPolicy"] as! [String: Any]
        XCTAssertEqual(policy["type"] as? String, "readOnly")
        XCTAssertEqual(policy["networkAccess"] as? Bool, false)
        XCTAssertEqual((params["environments"] as? [Any])?.count, 0)
        let input = (params["input"] as! [[String: Any]])[0]
        XCTAssertEqual(input["type"] as? String, "text")
        XCTAssertEqual((input["text_elements"] as? [Any])?.count, 0)
        XCTAssertTrue(host.sent.allSatisfy { $0["jsonrpc"] == nil })
        engine.stop()
    }

    func testStopBeforeThreadResponseStopsHostAndIgnoresLateResponse() {
        let host = FixtureTransport()
        let engine = connected(host)
        engine.send(text: "hello")
        let late = host.messageHandler!
        let id = host.last("thread/start")["id"]!
        engine.interruptOrStop()
        late(["id": id, "result": ["thread": ["id": "late-thread"]]])
        XCTAssertEqual(host.stopCount, 1)
        XCTAssertEqual(engine.state, .idle)
        XCTAssertNil(engine.threadId)
        XCTAssertFalse(host.sent.contains { $0["method"] as? String == "turn/start" })
    }

    func testEarlyDeltaReplayedAndFinalTextReplacesRatherThanAppends() {
        let host = FixtureTransport()
        let engine = connected(host)
        engine.send(text: "hello")
        host.reply("thread/start", ["thread": ["id": "thread"]])
        delta(host, "A")
        delta(host, "ignored", turn: "stale-turn")
        host.reply("turn/start", ["turn": turn("inProgress")])
        delta(host, "B")
        let item: [String: Any] = ["id": "item", "type": "agentMessage", "text": "ABC"]
        let params: [String: Any] = ["threadId": "thread", "turnId": "turn", "item": item]
        host.event("item/completed", params)
        host.event("item/completed", params)
        delta(host, "late")
        host.event("turn/completed", ["threadId": "thread", "turn": turn("completed", items: [item])])
        XCTAssertEqual(engine.entries.filter { $0.role == .assistant }.map(\.text), ["ABC"])
        XCTAssertEqual(engine.lastTurnStatus, "completed")
        XCTAssertEqual(engine.state, .ready)
        engine.stop()
    }

    func testInterruptIsNotTerminalUntilCompletedNotification() {
        let host = FixtureTransport()
        let engine = streaming(host)
        engine.interruptOrStop()
        host.reply("turn/interrupt", [:])
        XCTAssertEqual(engine.state, .interrupting)
        host.event("turn/completed", ["threadId": "thread", "turn": turn("interrupted")])
        XCTAssertEqual(engine.lastTurnStatus, "interrupted")
        XCTAssertEqual(engine.state, .ready)
        engine.stop()
    }

    func testLoginIDMustMatchAndAccountMustConfirm() {
        let host = FixtureTransport()
        let engine = CodexSessionEngine(transportFactory: { host })
        engine.connect()
        host.reply("initialize", ["userAgent": "fixture"])
        host.reply("account/read", ["account": NSNull(), "requiresOpenaiAuth": true])
        engine.loginChatGPT()
        host.reply("account/login/start", ["type": "chatgpt", "loginId": "login",
                                             "authUrl": "https://auth.openai.com/authorize?code=fixture"])
        host.event("account/login/completed", ["loginId": "wrong", "success": true])
        XCTAssertEqual(engine.state, .loggingIn)
        host.event("account/login/completed", ["loginId": "login", "success": true])
        XCTAssertEqual(engine.state, .confirmingLogin)
        XCTAssertNotNil(engine.browser)
        let presentationID = engine.browser!.id
        host.reply("account/read", ["account": ["type": "chatgpt"], "requiresOpenaiAuth": true])
        XCTAssertEqual(engine.state, .ready)
        XCTAssertNil(engine.browser)
        engine.browserDidClose(presentationID: presentationID)
        XCTAssertEqual(host.stopCount, 0)
        engine.stop()
    }

    func testClosingSafariCancelsUnconfirmedAccountRead() {
        let host = FixtureTransport()
        let engine = CodexSessionEngine(transportFactory: { host })
        engine.connect()
        host.reply("initialize", ["userAgent": "fixture"])
        host.reply("account/read", ["account": NSNull(), "requiresOpenaiAuth": true])
        engine.loginChatGPT()
        host.reply("account/login/start", ["type": "chatgpt", "loginId": "login",
                                             "authUrl": "https://chatgpt.com/auth"])
        host.event("account/login/completed", ["loginId": "login", "success": true])
        engine.browserDidClose(presentationID: engine.browser!.id)
        XCTAssertEqual(host.stopCount, 1)
        XCTAssertNil(engine.loginId)
        XCTAssertEqual(engine.state, .idle)
    }

    func testRequestTimeoutNeverResendsTurn() async throws {
        let host = FixtureTransport()
        let engine = connected(host, timeout: 0.02)
        engine.send(text: "hello")
        host.reply("thread/start", ["thread": ["id": "thread"]])
        let oldHandler = host.messageHandler!
        let id = host.last("turn/start")["id"]!
        try await Task<Never, Never>.sleep(nanoseconds: 100_000_000)
        oldHandler(["id": id, "result": ["turn": turn("inProgress")]])
        XCTAssertEqual(host.stopCount, 1)
        XCTAssertEqual(engine.state, .failed)
        XCTAssertNil(engine.turnId)
        XCTAssertEqual(host.sent.filter { $0["method"] as? String == "turn/start" }.count, 1)
    }

    func testNonterminalTurnCompletedFailsClosed() {
        let host = FixtureTransport()
        let engine = streaming(host)
        host.event("turn/completed", ["threadId": "thread", "turn": turn("inProgress")])
        XCTAssertEqual(engine.state, .failed)
        XCTAssertEqual(host.stopCount, 1)
        XCTAssertNotNil(engine.errorMessage)
    }

    func testBackgroundStopsWithoutReconnect() {
        let host = FixtureTransport()
        let engine = streaming(host)
        engine.movedToBackground()
        XCTAssertEqual(host.stopCount, 1)
        XCTAssertEqual(engine.state, .idle)
        XCTAssertNil(engine.threadId)
        XCTAssertFalse(engine.canSend)
    }

    func testTerminalEventBeforeInterruptAckRetiresPendingAck() {
        let host = FixtureTransport()
        let engine = streaming(host)
        engine.interruptOrStop()
        host.event("turn/completed", ["threadId": "thread", "turn": turn("interrupted")])
        XCTAssertEqual(engine.state, .ready)
        XCTAssertEqual(engine.lastTurnStatus, "interrupted")
        host.reply("turn/interrupt", [:])
        XCTAssertEqual(engine.state, .ready)
        XCTAssertNil(engine.errorMessage)
        engine.stop()
    }

    func testCannotReconnectUntilOldHostHasReapedAndFinishedStop() {
        let host = FixtureTransport()
        let engine = streaming(host)
        delta(host, "unfinished")
        host.holdStopCompletion = true
        engine.stop()
        XCTAssertEqual(engine.state, .stopping)
        XCTAssertFalse(engine.canConnect)
        XCTAssertTrue(engine.entries.allSatisfy { $0.finished })
        engine.connect()
        XCTAssertEqual(host.sent.filter { $0["method"] as? String == "initialize" }.count, 1)
        host.finishStop()
        XCTAssertEqual(engine.state, .idle)
        XCTAssertTrue(engine.canConnect)
    }

    func testEarlyLoginCompletedBufferedUntilMatchingLoginIDArrives() {
        let host = FixtureTransport()
        let engine = CodexSessionEngine(transportFactory: { host })
        engine.connect()
        host.reply("initialize", ["userAgent": "fixture"])
        host.reply("account/read", ["account": NSNull(), "requiresOpenaiAuth": true])
        engine.loginChatGPT()
        host.event("account/login/completed", ["loginId": "login", "success": true])
        XCTAssertEqual(engine.state, .loggingIn)
        host.reply("account/login/start", ["type": "chatgpt", "loginId": "login",
                                            "authUrl": "https://auth.openai.com/oauth/authorize?state=fixture"])
        XCTAssertEqual(engine.state, .confirmingLogin)
        host.reply("account/read", ["account": ["type": "chatgpt"], "requiresOpenaiAuth": true])
        XCTAssertTrue(engine.canSend)
        engine.stop()
    }

    func testWrongThreadOrTurnCannotCompleteCurrentGeneration() {
        let host = FixtureTransport()
        let engine = streaming(host)
        host.event("turn/completed", ["threadId": "wrong", "turn": turn("completed")])
        host.event("turn/completed", ["threadId": "thread", "turn": ["id": "wrong-turn", "status": "completed", "items": []]])
        XCTAssertEqual(engine.state, .streaming)
        XCTAssertEqual(engine.turnId, "turn")
        engine.stop()
    }

    func testWrongBrowserIdentityIsIgnoredAndMatchingCloseStopsOnce() {
        let host = FixtureTransport()
        let engine = CodexSessionEngine(transportFactory: { host })
        engine.connect()
        host.reply("initialize", ["userAgent": "fixture"])
        host.reply("account/read", ["account": NSNull(), "requiresOpenaiAuth": true])
        engine.loginChatGPT()
        host.reply("account/login/start", ["type": "chatgpt", "loginId": "login",
                                             "authUrl": "https://auth.openai.com/oauth/authorize"])
        let pageID = engine.browser!.id
        engine.browserDidClose(presentationID: UUID())
        XCTAssertEqual(engine.state, .loggingIn)
        XCTAssertEqual(engine.browser?.id, pageID)
        XCTAssertEqual(host.stopCount, 0)
        engine.browserDidClose(presentationID: pageID)
        engine.browserDidClose(presentationID: pageID)
        XCTAssertEqual(host.stopCount, 1)
        XCTAssertEqual(engine.state, .idle)
        XCTAssertNil(engine.browser)
    }

    func testOldSafariCloseCannotStopNewLoginAfterReconnect() {
        let first = FixtureTransport()
        let second = FixtureTransport()
        var connections = 0
        let engine = CodexSessionEngine(transportFactory: {
            let next = connections == 0 ? first : second
            connections += 1
            return next
        })
        engine.connect()
        first.reply("initialize", ["userAgent": "fixture"])
        first.reply("account/read", ["account": NSNull(), "requiresOpenaiAuth": true])
        engine.loginChatGPT()
        first.reply("account/login/start", ["type": "chatgpt", "loginId": "first-login",
                                              "authUrl": "https://auth.openai.com/oauth/authorize"])
        let oldPageID = engine.browser!.id
        engine.stop()
        engine.connect()
        second.reply("initialize", ["userAgent": "fixture"])
        second.reply("account/read", ["account": NSNull(), "requiresOpenaiAuth": true])
        engine.loginChatGPT()
        second.reply("account/login/start", ["type": "chatgpt", "loginId": "second-login",
                                               "authUrl": "https://auth.openai.com/oauth/authorize"])
        let currentPageID = engine.browser!.id
        XCTAssertNotEqual(oldPageID, currentPageID)
        engine.browserDidClose(presentationID: oldPageID)
        XCTAssertEqual(engine.state, .loggingIn)
        XCTAssertEqual(engine.loginId, "second-login")
        XCTAssertEqual(second.stopCount, 0)
        second.event("account/login/completed", ["loginId": "second-login", "success": true])
        engine.browserDidClose(presentationID: oldPageID)
        XCTAssertEqual(engine.state, .confirmingLogin)
        XCTAssertEqual(second.stopCount, 0)
        second.reply("account/read", ["account": ["type": "chatgpt"], "requiresOpenaiAuth": true])
        engine.browserDidClose(presentationID: oldPageID)
        engine.browserDidClose(presentationID: currentPageID)
        XCTAssertTrue(engine.canSend)
        XCTAssertEqual(first.stopCount, 1)
        XCTAssertEqual(second.stopCount, 0)
        engine.stop()
    }

    func testOfficialURLBoundaryAndRedaction() {
        for allowed in ["https://openai.com/auth", "https://auth.openai.com/a", "https://chatgpt.com:443/a"] {
            XCTAssertNotNil(CodexWire.officialAuthorizationURL(allowed))
        }
        for denied in ["http://openai.com", "https://openai.com.evil.test", "https://evilopenai.com",
                       "https://openai.com@evil.test", "https://user@openai.com", "https://openai.com:444/a",
                       "https://openai.com./auth", "file:///openai.com"] {
            XCTAssertNil(CodexWire.officialAuthorizationURL(denied))
        }
        let safe = CodexWire.safeError("error https://auth.openai.com/?code=sensitive a@b.com token=secretcode")
        XCTAssertFalse(safe.contains("https://"))
        XCTAssertFalse(safe.contains("a@b.com"))
        XCTAssertFalse(safe.contains("secretcode"))
        XCTAssertFalse(safe.contains("sensitive"))
    }
}

// Synthetic inputs only; never reads the actual clipboard or any Keychain item.
@MainActor
private final class ProviderPasteFixtureBox {
    var value: String
    init(_ value: String) { self.value = value }
    var binding: Binding<String> { Binding(get: { self.value }, set: { self.value = $0 }) }
}

@MainActor
final class OMProviderPasteTests: XCTestCase {
    private func flushMain() async {
        await withCheckedContinuation { (continuation: CheckedContinuation<Void, Never>) in
            DispatchQueue.main.async { continuation.resume() }
        }
    }

    func testPasteTextUsesCurrentBindingAndSafeFeedback() async {
        let box = ProviderPasteFixtureBox("old-input")
        let state = OMProviderPasteState(), receiver = UUID()
        state.attach(receiverID: receiver, text: box.binding, fieldID: "fixture-key", secret: true)
        state.request(receiverID: receiver) { $0(.text("  synthetic-fixture-only-key\n")) }
        XCTAssertEqual(state.status, .loading)
        await flushMain()
        XCTAssertEqual(box.value, "synthetic-fixture-only-key")
        XCTAssertEqual(state.status, .success)
        XCTAssertFalse(state.status.message.contains(box.value))
    }

    func testPasteURLPreservesVersionQueryAndFragment() async {
        let url = "https://fixture.invalid/v1beta?api-version=fixture#test"
        let box = ProviderPasteFixtureBox("")
        let state = OMProviderPasteState(), receiver = UUID()
        state.attach(receiverID: receiver, text: box.binding, fieldID: "fixture-url", secret: false)
        state.request(receiverID: receiver) { $0(.text("\r\n" + url + "\n")) }
        await flushMain()
        XCTAssertEqual(box.value, url)
        XCTAssertEqual(state.status, .success)
        XCTAssertFalse(state.status.message.contains(url))
    }

    func testEmptyNontextAndLoadFailureDoNotBlankExistingInput() async {
        let box = ProviderPasteFixtureBox("keep-fixture-input")
        let state = OMProviderPasteState(), receiver = UUID()
        state.attach(receiverID: receiver, text: box.binding, fieldID: "fixture-key", secret: true)
        let cases: [(OMProviderPasteLoadedText, OMProviderPasteStatus)] = [
            (.text(" \n\r "), .empty), (.nonText, .nonText), (.failed, .loadFailed)
        ]
        for (result, expected) in cases {
            state.request(receiverID: receiver) { $0(result) }
            await flushMain()
            XCTAssertEqual(state.status, expected)
            XCTAssertEqual(box.value, "keep-fixture-input")
        }
    }

    func testOversizeNULAndInternalNewlineAreRejected() {
        for (text, secret) in [(String(repeating: "x", count: 8193), true),
                               (String(repeating: "x", count: 16385), false),
                               ("fixture\0value", true), ("https://fixture.invalid/\nsecond", false)] {
            if case .success = OMProviderPasteValidation.validate(text, secret: secret) {
                XCTFail("Expected bounded text validation to reject synthetic input")
            }
        }
    }

    func testManualEditEvenRestoredRetiresPendingPaste() async {
        let box = ProviderPasteFixtureBox("original-fixture")
        let state = OMProviderPasteState(), receiver = UUID()
        state.attach(receiverID: receiver, text: box.binding, fieldID: "fixture-key", secret: true)
        var completion: OMProviderPasteCompletion?
        state.request(receiverID: receiver) { completion = $0 }
        box.value = "manual-edit"
        state.destinationDidChange(fieldID: "fixture-key")
        box.value = "original-fixture"
        completion?(.text("late-fixture-paste"))
        await flushMain()
        XCTAssertEqual(box.value, "original-fixture")
        XCTAssertEqual(state.status, .cancelled)
    }

    func testRebindAndNewerPasteRejectOldCompletion() async {
        let old = ProviderPasteFixtureBox("old-field"), current = ProviderPasteFixtureBox("new-field")
        let state = OMProviderPasteState(), receiver = UUID()
        state.attach(receiverID: receiver, text: old.binding, fieldID: "provider-A", secret: true)
        var first: OMProviderPasteCompletion?
        state.request(receiverID: receiver) { first = $0 }
        state.rebind(receiverID: receiver, text: current.binding, fieldID: "provider-B", secret: true)
        first?(.text("must-not-cross-provider"))
        await flushMain()
        XCTAssertEqual(old.value, "old-field")
        XCTAssertEqual(current.value, "new-field")
        var second: OMProviderPasteCompletion?, third: OMProviderPasteCompletion?
        state.request(receiverID: receiver) { second = $0 }
        state.request(receiverID: receiver) { third = $0 }
        third?(.text("latest-fixture-input"))
        await flushMain()
        second?(.text("late-second-fixture"))
        await flushMain()
        XCTAssertEqual(current.value, "latest-fixture-input")
        XCTAssertEqual(state.status, .success)
    }

    func testDisappearanceAndOldDismantleCannotAffectReplacement() async {
        let box = ProviderPasteFixtureBox("keep-fixture")
        let state = OMProviderPasteState(), old = UUID(), replacement = UUID()
        state.attach(receiverID: old, text: box.binding, fieldID: "fixture-key", secret: true)
        var stale: OMProviderPasteCompletion?
        state.request(receiverID: old) { stale = $0 }
        state.suspend(fieldID: "fixture-key")
        stale?(.text("background-fixture"))
        await flushMain()
        XCTAssertEqual(box.value, "keep-fixture")
        state.attach(receiverID: replacement, text: box.binding, fieldID: "fixture-key", secret: true)
        var fresh: OMProviderPasteCompletion?
        state.request(receiverID: replacement) { fresh = $0 }
        state.detach(receiverID: old)
        fresh?(.text("fresh-fixture"))
        await flushMain()
        fresh?(.text("duplicate-completion"))
        await flushMain()
        XCTAssertEqual(box.value, "fresh-fixture")
        XCTAssertEqual(state.status, .success)
    }

    func testFeedbackAndNativeResponderUseNoContentLogging() {
        for status in OMProviderPasteStatus.allCases {
            XCTAssertFalse(status.message.isEmpty)
            XCTAssertFalse(status.message.contains("synthetic-fixture-only-key"))
            XCTAssertFalse(status.message.contains("https://fixture.invalid"))
        }
        let receiver = OMProviderPasteReceiver(state: OMProviderPasteState())
        receiver.configure(secret: false)
        let provider = NSItemProvider(object: NSURL(string: "https://fixture.invalid/v1")!)
        XCTAssertTrue(receiver.canPaste([provider]))
        XCTAssertFalse(receiver.canPaste([]))
        // Type metadata only, not an actual paste or remote request.
    }
}
