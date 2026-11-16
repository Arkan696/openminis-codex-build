// SPDX-License-Identifier: GPL-3.0-or-later
// Source-only overlay. Not locally Swift-typechecked or tested on an iOS device.
import Foundation
import Combine
import SwiftUI
import UIKit

// Feedback is deliberately closed over fixed messages, never provider content.
enum OMProviderPasteStatus: CaseIterable, Equatable, Sendable {
    case ready, loading, success, empty, nonText, tooLong, invalidText, loadFailed, cancelled

    var message: String {
        switch self {
        case .ready: return "系统粘贴；请先在来源App复制文字"
        case .loading: return "正在接收系统粘贴…"
        case .success: return "已粘贴到当前输入框。"
        case .empty: return "粘贴失败：文字为空；未更改输入。"
        case .nonText: return "粘贴失败：没有可接收的文字；未更改输入。"
        case .tooLong: return "粘贴失败：文字过长；未更改输入。"
        case .invalidText: return "粘贴失败：文字含无效字符或内部换行；未更改输入。"
        case .loadFailed: return "粘贴失败：系统未能提供文字；请重新复制后重试。"
        case .cancelled: return "输入或目标已变化；已忽略本次粘贴。"
        }
    }

    var isError: Bool {
        switch self {
        case .ready, .loading, .success: return false
        default: return true
        }
    }
}

enum OMProviderPasteInputError: Error, Sendable {
    case empty, tooLong, invalidText

    var status: OMProviderPasteStatus {
        switch self {
        case .empty: return .empty
        case .tooLong: return .tooLong
        case .invalidText: return .invalidText
        }
    }
}

enum OMProviderPasteValidation {
    static func validate(_ raw: String, secret: Bool) -> Result<String, OMProviderPasteInputError> {
        let text = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return .failure(.empty) }
        guard !text.contains("\0"), text.rangeOfCharacter(from: .newlines) == nil else {
            return .failure(.invalidText)
        }
        guard text.count <= (secret ? 8_192 : 16_384) else { return .failure(.tooLong) }
        // No key-prefix, URL scheme, path, or interior whitespace normalization.
        return .success(text)
    }
}

// This injection seam carries no NSError descriptions and needs no clipboard/UI.
enum OMProviderPasteLoadedText: Sendable {
    case text(String), nonText, failed
}
typealias OMProviderPasteCompletion = @Sendable (OMProviderPasteLoadedText) -> Void
typealias OMProviderPasteTextLoader = (@escaping OMProviderPasteCompletion) -> Void

@MainActor
final class OMProviderPasteState: ObservableObject {
    @Published private(set) var status: OMProviderPasteStatus = .ready

    private var receiverID: UUID?
    private var lifetime = UUID()
    private var generation: UInt64 = 0
    private var feedbackGeneration: UInt64 = 0
    private var destination: Binding<String>?
    private var fieldID: String?
    private var secret = true
    private var active = false
    private var pending: Request?

    private struct Request: Sendable {
        let receiverID: UUID
        let lifetime: UUID
        let generation: UInt64
        let fieldID: String
        let secret: Bool
        let initialValue: String
    }

    func attach(receiverID: UUID, text: Binding<String>, fieldID: String,
                secret: Bool, deferFeedback: Bool = false) {
        invalidateRequest()
        lifetime = UUID()
        self.receiverID = receiverID
        self.destination = text
        self.fieldID = fieldID
        self.secret = secret
        active = true
        publish(.ready, deferred: deferFeedback)
    }

    func rebind(receiverID: UUID, text: Binding<String>, fieldID: String,
                secret: Bool, deferFeedback: Bool = false) {
        guard self.receiverID == receiverID else { return }
        let changedTarget = self.fieldID != fieldID || self.secret != secret
        self.destination = text
        self.fieldID = fieldID
        self.secret = secret
        if changedTarget {
            invalidateRequest()
            lifetime = UUID()
            publish(.ready, deferred: deferFeedback)
        } else if let request = pending, text.wrappedValue != request.initialValue {
            invalidateRequest()
            publish(.cancelled, deferred: deferFeedback)
        }
        // Same identity/value on a status redraw does NOT retire the operation.
        // Rebinding a suspended receiver does NOT make it active again.
    }

    // An observed edit retires the request even if the value is later restored.
    func destinationDidChange(fieldID: String) {
        guard self.fieldID == fieldID, pending != nil else { return }
        invalidateRequest()
        publish(.cancelled)
    }

    func suspend(fieldID: String) {
        guard self.fieldID == fieldID else { return }
        active = false
        lifetime = UUID()
        invalidateRequest()
        publish(.ready)
    }

    func resume(fieldID: String) {
        guard self.fieldID == fieldID, receiverID != nil, destination != nil, !active else { return }
        active = true
        lifetime = UUID()
        invalidateRequest()
        publish(.ready)
    }

    func detach(receiverID: UUID, deferFeedback: Bool = false) {
        // Dismantling an old control must not invalidate its replacement.
        guard self.receiverID == receiverID else { return }
        active = false
        lifetime = UUID()
        invalidateRequest()
        destination = nil
        fieldID = nil
        self.receiverID = nil
        publish(.ready, deferred: deferFeedback)
    }

    func request(receiverID: UUID, loader: OMProviderPasteTextLoader) {
        guard active, self.receiverID == receiverID,
              let destination = destination, let fieldID = fieldID else { return }
        invalidateRequest()
        let request = Request(receiverID: receiverID, lifetime: lifetime,
                              generation: generation, fieldID: fieldID,
                              secret: secret, initialValue: destination.wrappedValue)
        pending = request
        publish(.loading)
        // A missing provider completion must not leave an unbounded spinner.
        DispatchQueue.main.asyncAfter(deadline: .now() + 12) { [weak self] in
            self?.finish(.failed, request: request)
        }
        loader { [weak self] result in
            // NSItemProvider may complete off-main. All bindings/state stay main-actor.
            // Always enqueue, including synchronous synthetic loaders.
            DispatchQueue.main.async { [weak self] in
                self?.finish(result, request: request)
            }
        }
    }

    private func isCurrent(_ request: Request) -> Bool {
        active && receiverID == request.receiverID && lifetime == request.lifetime
            && generation == request.generation && fieldID == request.fieldID
            && secret == request.secret
    }

    private func finish(_ result: OMProviderPasteLoadedText, request: Request) {
        guard isCurrent(request), let pending = pending,
              pending.generation == request.generation, pending.lifetime == request.lifetime,
              let destination = destination else { return }
        // Also catches edits before SwiftUI has delivered onChange/updateUIView.
        guard destination.wrappedValue == request.initialValue else {
            invalidateRequest()
            publish(.cancelled)
            return
        }
        self.pending = nil // Consume once, before a Binding setter can cause reentry.
        switch result {
        case .text(let raw):
            switch OMProviderPasteValidation.validate(raw, secret: request.secret) {
            case .success(let text):
                destination.wrappedValue = text
                if isCurrent(request) { publish(.success) }
            case .failure(let error): publish(error.status)
            }
        case .nonText: publish(.nonText)
        case .failed: publish(.loadFailed)
        }
    }

    private func invalidateRequest() {
        generation &+= 1
        pending = nil
    }

    private func publish(_ value: OMProviderPasteStatus, deferred: Bool = false) {
        feedbackGeneration &+= 1
        let version = feedbackGeneration
        guard status != value else { return }
        if deferred {
            // UIKit make/update/dismantle may run inside a SwiftUI render pass.
            // Versioning prevents a queued reset from replacing newer feedback.
            DispatchQueue.main.async { [weak self] in
                guard let self = self, self.feedbackGeneration == version else { return }
                if self.status != value { self.status = value }
            }
        } else {
            status = value
        }
    }
}
// A retained responder is essential: UIPasteControl.target is weak.
@MainActor
final class OMProviderPasteReceiver: UIResponder {
    let receiverID = UUID()
    let state: OMProviderPasteState
    private var secret = true

    func configure(secret: Bool) {
        guard self.secret != secret else { return }
        self.secret = secret
        pasteConfiguration = secret
            ? UIPasteConfiguration(forAccepting: NSString.self)
            : UIPasteConfiguration(acceptableTypeIdentifiers: ["public.text", "public.url"])
    }

    init(state: OMProviderPasteState) {
        self.state = state
        super.init()
        pasteConfiguration = UIPasteConfiguration(forAccepting: NSString.self)
    }

    override func canPaste(_ itemProviders: [NSItemProvider]) -> Bool {
        guard !itemProviders.isEmpty, itemProviders.count <= 16 else { return false }
        return itemProviders.contains {
            $0.canLoadObject(ofClass: NSString.self) || (!secret && $0.canLoadObject(ofClass: NSURL.self))
        }
    }

    override func paste(itemProviders: [NSItemProvider]) {
        // No read of UIPasteboard. The system delivers providers only after its
        // own explicit Paste interaction. Never stringify provider/NSError data.
        let textProvider = itemProviders.count <= 16
            ? itemProviders.first { $0.canLoadObject(ofClass: NSString.self) } : nil
        let urlProvider = !secret && itemProviders.count <= 16
            ? itemProviders.first { $0.canLoadObject(ofClass: NSURL.self) } : nil
        state.request(receiverID: receiverID) { completion in
            if let provider = textProvider {
                _ = provider.loadObject(ofClass: NSString.self) { object, error in
                    if error != nil { completion(.failed) }
                    else if let text = object as? NSString { completion(.text(text as String)) }
                    else { completion(.nonText) }
                }
            } else if let provider = urlProvider {
                _ = provider.loadObject(ofClass: NSURL.self) { object, error in
                    if error != nil { completion(.failed) }
                    else if let url = object as? NSURL, !url.isFileURL {
                        completion(.text((url as URL).absoluteString))
                    } else { completion(.nonText) }
                }
            } else { completion(.nonText) }
        }
    }
}

@MainActor
private struct OMProviderSystemPasteControl: UIViewRepresentable {
    @Binding var text: String
    let fieldID: String
    let secret: Bool
    let state: OMProviderPasteState

    func makeCoordinator() -> OMProviderPasteReceiver { OMProviderPasteReceiver(state: state) }

    func makeUIView(context: Context) -> UIPasteControl {
        let config = UIPasteControl.Configuration()
        config.displayMode = .iconAndLabel
        config.cornerStyle = .capsule
        let control = UIPasteControl(configuration: config)
        // UIKit owns metadata/permission handling; coordinator owns the target.
        context.coordinator.configure(secret: secret)
        control.target = context.coordinator
        control.accessibilityLabel = secret ? "系统粘贴 API Key" : "系统粘贴服务地址"
        control.accessibilityIdentifier = "provider-system-paste-" + fieldID
        state.attach(receiverID: context.coordinator.receiverID, text: $text,
                     fieldID: fieldID, secret: secret, deferFeedback: true)
        return control
    }

    func updateUIView(_ control: UIPasteControl, context: Context) {
        state.rebind(receiverID: context.coordinator.receiverID, text: $text,
                     fieldID: fieldID, secret: secret, deferFeedback: true)
        context.coordinator.configure(secret: secret)
        control.target = context.coordinator
        control.accessibilityLabel = secret ? "系统粘贴 API Key" : "系统粘贴服务地址"
        control.accessibilityIdentifier = "provider-system-paste-" + fieldID
    }

    static func dismantleUIView(_ control: UIPasteControl, coordinator: OMProviderPasteReceiver) {
        control.target = nil
        coordinator.state.detach(receiverID: coordinator.receiverID, deferFeedback: true)
    }
}

/// Extra row beneath existing input fields, not a replacement text editor.
/// Changing only the supplied Binding preserves upstream mask/eye/save logic.
@MainActor
struct OMProviderPasteRow: View {
    @Binding var text: String
    let fieldID: String
    let secret: Bool
    @StateObject private var state = OMProviderPasteState()
    @Environment(\.scenePhase) private var scenePhase

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack(spacing: 12) {
                OMProviderSystemPasteControl(text: $text, fieldID: fieldID, secret: secret, state: state)
                    .frame(width: 138, height: 44)
                if state.status == .loading { ProgressView().controlSize(.small) }
                Spacer(minLength: 0)
            }
            Text(state.status.message)
                .font(.caption)
                .foregroundColor(state.status.isError ? .red : .secondary)
                .accessibilityIdentifier("provider-paste-feedback-" + fieldID)
            if state.status == .ready {
                Text("按钮灰色时，请先在来源 App 复制纯文字。粘贴替换这个输入框；保存沿用本页原有方式。")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
        }
        .onAppear { state.resume(fieldID: fieldID) }
        .onDisappear { state.suspend(fieldID: fieldID) }
        .onChange(of: text) { _ in state.destinationDidChange(fieldID: fieldID) }
        .onChange(of: scenePhase) { phase in
            // A system permission prompt may be inactive, not background.
            // Only an actual background transition retires the paste request.
            if phase == .background { state.suspend(fieldID: fieldID) }
            else if phase == .active { state.resume(fieldID: fieldID) }
        }
    }
}
