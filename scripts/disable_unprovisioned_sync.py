"""Pinned source transformation for an unprovisioned, independent test identity.
Returns proposed bytes; never writes files or touches a live installation.
"""
from pathlib import Path
from overlay_lib import Refused, blob_hash

APP_ID = 'local.openminis.codex'
PINNED = {
 'Agent/Sync/CloudSyncEngine.swift': '33981f5b2fd05f2b4c92b18ab5cdcd01fcd0074a',
 'Agent/Sync/V2/SyncV2Bootstrap.swift': '54d5f2d53debf2dbdf917bc297b3f9c2b51fb7c6',
 'Views/Settings/CloudSyncSettingsView.swift': '709c8c85ab7838f8685677097d00aa62dae5214f',
 'Views/Sync/CloudSyncSettingsV2View.swift': '8b34e85c84a8a5df083a832709cc41113b3598f0',
}

def once(text, old, new):
    if text.count(old) != 1:
        raise Refused('Cloud sync source anchor differs: ' + old[:70])
    return text.replace(old, new, 1)

def disabled_settings(text, container, end):
    start = '    var body: some View {\n        ' + container + ' {'
    replacement = '''    @ViewBuilder
    var body: some View {
        if Bundle.main.bundleIdentifier == "local.openminis.codex" {
            VStack(alignment: .leading, spacing: 12) {
                Text("iCloud 同步不可用").font(.headline)
                Text("此独立 TrollStore 测试版没有 iCloud 授权，云同步已禁用。此页面不会连接或删除云端数据。")
                    .foregroundColor(.secondary)
            }
            .padding()
            .navigationTitle("iCloud Sync")
        } else {
        ''' + container + ' {'
    # The legacy file contains several helper views with the same List body.
    # Its fixed main-body terminator uniquely separates the page from those
    # helpers. Restrict replacement to that prefix rather than editing all views.
    if text.count(end) != 1:
        raise Refused('Cloud sync main settings boundary differs')
    main, tail = text.split(end, 1)
    main = once(main, start, replacement)
    return main + '        }\n' + end + tail

def proposed_changes(ios):
    ios = Path(ios)
    result = {}
    # Validate every source before preparing any change.
    for rel, expected in PINNED.items():
        p = ios / rel
        if p.is_symlink() or not p.is_file() or not p.resolve().is_relative_to(ios.resolve()):
            raise Refused('Missing or linked cloud sync source: ' + rel)
        data = p.read_bytes()
        if blob_hash(data) != expected:
            raise Refused('Cloud sync source differs from fixed upstream: ' + rel)
        result[rel] = data.decode('utf-8')
    rel = 'Agent/Sync/CloudSyncEngine.swift'
    text = result[rel]
    text = once(text,
        '        self.isEnabled = UserDefaults.standard.bool(forKey: "cloudSync.enabled")',
        '        self.isEnabled = Bundle.main.bundleIdentifier != "local.openminis.codex" && UserDefaults.standard.bool(forKey: "cloudSync.enabled")')
    text = once(text, '    func start() async {\n', '''    func start() async {
        // Independent test identity has no CloudKit provisioning entitlement.
        guard Bundle.main.bundleIdentifier != "local.openminis.codex" else {
            syncStatus = .disabled
            return
        }
''')
    # Configuration writes must not report enabled or start sync in this build.
    text = once(text, '    @Published var isEnabled: Bool {\n        didSet {\n', '''    @Published var isEnabled: Bool {
        didSet {
            if Bundle.main.bundleIdentifier == "local.openminis.codex" {
                isEnabled = false
                UserDefaults.standard.set(false, forKey: "cloudSync.enabled")
                syncStatus = .disabled
                return
            }
''')
    result[rel] = text
    rel = 'Agent/Sync/V2/SyncV2Bootstrap.swift'
    text = result[rel]
    text = once(text, '    static var isEnabled: Bool {\n', '''    static var isEnabled: Bool {
        guard Bundle.main.bundleIdentifier != "local.openminis.codex" else { return false }
''')
    text = once(text, '    static func setEnabled(_ enabled: Bool) {\n', '''    static func setEnabled(_ enabled: Bool) {
        guard Bundle.main.bundleIdentifier != "local.openminis.codex" else {
            UserDefaults.standard.set(false, forKey: "cloudSync.v2.enabled")
            return
        }
''')
    text = once(text, '    static func startIfEnabled() async {\n', '''    static func startIfEnabled() async {
        guard Bundle.main.bundleIdentifier != "local.openminis.codex" else { return }
''')
    result[rel] = text
    rel = 'Views/Settings/CloudSyncSettingsView.swift'
    result[rel] = disabled_settings(result[rel], 'List', '    }\n\n    // MARK: - Sections')
    rel = 'Views/Sync/CloudSyncSettingsV2View.swift'
    result[rel] = disabled_settings(result[rel], 'Form', '    }\n\n    /// [T-ios-migration-timer-sessionlist-uaf-crash] Self-cancelling 5s')
    return result
