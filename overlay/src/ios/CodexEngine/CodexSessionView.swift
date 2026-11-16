// SPDX-License-Identifier: GPL-3.0-or-later
// Source-only overlay. NOT Swift-typechecked or tested on an iOS device.
import SwiftUI
import SafariServices

@MainActor
struct CodexSessionView: View {
    @StateObject private var engine = CodexSessionEngine()
    @State private var draft = ""
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            VStack(spacing: 12) {
                status
                controls
                transcript
                composer
            }
            .padding(.horizontal)
            .navigationTitle("Codex 独立会话")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button("关闭") {
                        engine.stop()
                        dismiss()
                    }
                }
            }
        }
        .sheet(item: browserBinding) { presentation in
            CodexOfficialSafariView(url: presentation.url) {
                engine.browserDidClose(presentationID: presentation.id)
            }
            .ignoresSafeArea()
            // Use Safari's explicit Cancel button; an interactive sheet nil
            // setter does not carry the identity of the departing controller.
            .interactiveDismissDisabled()
            .onDisappear {
                // The departing page owns this immutable identity, even if a
                // newer login is already active when SwiftUI calls us back.
                engine.browserDidClose(presentationID: presentation.id)
            }
        }
        .onChange(of: scenePhase) { phase in
            if phase == .background { engine.movedToBackground() }
        }
        .onDisappear { engine.stop() }
    }

    private var browserBinding: Binding<CodexBrowserPresentation?> {
        // Dismissal is handled by the page-specific Safari delegate/onDisappear
        // above. The sheet's generic nil setter carries no page identity and
        // must not cancel whichever login happens to be current at that instant.
        Binding(get: { engine.browser }, set: { _ in })
    }

    private var status: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(engine.state.title).font(.headline)
            Text("临时登录 · 仅当前前台核心有效。停止、新对话、关闭页面或进入后台后需重新登录。")
                .font(.caption).foregroundColor(.secondary)
            Text("纯文本安全配置：Codex 工作目录只读、OpenMinis 工具不接入；临时目录不等于 iOS 内核安全沙箱。首版不导入原会话历史。")
                .font(.caption).foregroundColor(.secondary)
            if let error = engine.errorMessage {
                Text(error).font(.callout).foregroundColor(.red).textSelection(.enabled)
            }
            if let diagnostic = engine.diagnostic {
                Text(diagnostic).font(.caption).foregroundColor(.secondary).textSelection(.enabled)
            }
            if let outcome = engine.lastTurnStatus {
                Text(outcomeTitle(outcome)).font(.caption).foregroundColor(.secondary)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.top, 8)
    }

    private var controls: some View {
        HStack(spacing: 12) {
            Button("连接") { engine.connect() }.disabled(!engine.canConnect)
            Button("官方登录") { engine.loginChatGPT() }.disabled(!engine.canLogin)
            Button("停止", role: .destructive) { engine.interruptOrStop() }
                .disabled(!engine.canStop)
            Spacer(minLength: 0)
            Button("新对话") {
                draft = ""
                engine.newConversation()
            }
            .disabled(!engine.canStartNewConversation)
        }
        .font(.subheadline)
    }

    private var transcript: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 16) {
                    if engine.entries.isEmpty {
                        VStack(alignment: .leading, spacing: 10) {
                            Text("尚无消息").font(.title3)
                            Text("先点“连接”，再点“官方登录”。登录经核心确认后即可发送文本。")
                            Text("官方授权将在应用内 Safari 打开。此页面不读取 Cookie 或密码；关闭授权页面会取消尚未确认的登录。")
                            Text("当前补丁尚未在 Xcode typecheck 或 iOS 16.5 真机验证。连接错误会显示在上方。")
                        }
                        .font(.callout).foregroundColor(.secondary)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.vertical, 24)
                    }
                    ForEach(engine.entries) { entry in
                        VStack(alignment: .leading, spacing: 5) {
                            Text(entry.role == .user ? "你" : "Codex")
                                .font(.caption).foregroundColor(.secondary)
                            Text(entry.text.isEmpty ? "等待核心正文…" : entry.text)
                                .textSelection(.enabled)
                                .frame(maxWidth: .infinity, alignment: .leading)
                            if entry.role == .assistant && !entry.finished {
                                ProgressView().controlSize(.small)
                            }
                        }
                        .id(entry.id)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.vertical, 8)
            }
            .onChange(of: engine.entries.count) { _ in
                if let id = engine.entries.last?.id { proxy.scrollTo(id, anchor: .bottom) }
            }
            .onChange(of: engine.entries.last?.text) { _ in
                if let id = engine.entries.last?.id { proxy.scrollTo(id, anchor: .bottom) }
            }
        }
    }

    private var composer: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("消息").font(.caption).foregroundColor(.secondary)
            TextEditor(text: $draft)
                .frame(minHeight: 72, maxHeight: 120)
                .padding(6)
                .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.secondary.opacity(0.4)))
                .accessibilityLabel("发送给 Codex 的文本")
                .disabled(!engine.canSend)
            HStack {
                Text("仅内存会话，不自动重发。")
                    .font(.caption).foregroundColor(.secondary)
                Spacer()
                Button("发送") {
                    let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
                    guard !text.isEmpty, text.utf8.count <= 32_768 else { return }
                    engine.send(text: text)
                    draft = ""
                }
                .buttonStyle(.borderedProminent)
                .disabled(!engine.canSend || draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
            if draft.utf8.count > 32_768 {
                Text("消息超过 32 KiB，请缩短后发送。")
                    .font(.caption).foregroundColor(.red)
            }
        }
        .padding(.bottom, 8)
    }

    private func outcomeTitle(_ value: String) -> String {
        switch value {
        case "completed": return "上一回合已完成"
        case "interrupted": return "上一回合已中断"
        case "failed": return "上一回合失败"
        case "stopped": return "上一操作已停止"
        default: return ""
        }
    }
}

// Native Safari keeps the host app foreground. No cookie/password API is used.
// Initial auth URL validation lives in CodexWire; Safari handles official redirects.
@MainActor
private struct CodexOfficialSafariView: UIViewControllerRepresentable {
    let url: URL
    let onClose: () -> Void

    func makeCoordinator() -> Coordinator { Coordinator(onClose: onClose) }
    func makeUIViewController(context: Context) -> SFSafariViewController {
        let configuration = SFSafariViewController.Configuration()
        configuration.entersReaderIfAvailable = false
        let controller = SFSafariViewController(url: url, configuration: configuration)
        controller.delegate = context.coordinator
        controller.dismissButtonStyle = .cancel
        return controller
    }
    func updateUIViewController(_ controller: SFSafariViewController, context: Context) {}

    final class Coordinator: NSObject, SFSafariViewControllerDelegate {
        private let onClose: () -> Void
        init(onClose: @escaping () -> Void) { self.onClose = onClose }
        func safariViewControllerDidFinish(_ controller: SFSafariViewController) { onClose() }
    }
}
