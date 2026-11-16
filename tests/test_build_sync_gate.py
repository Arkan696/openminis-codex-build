"""Tests pinned source transformation, NOT Swift compilation or CloudKit runtime."""
from pathlib import Path
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import disable_unprovisioned_sync as gate
import repair_provider_paste as paste
import prepare_ipa_build as prep
from overlay_lib import EXPECTED, SOURCE, FILES, Refused, blob_hash

UPSTREAM=Path(os.environ.get('CODEX_IPA_TEST_UPSTREAM',str(ROOT/'build/upstream-full'/('OpenMinis-'+EXPECTED))))
IOS=UPSTREAM/'src/ios'
REQUIRED=('Info.plist','Minis.entitlements','ShareExtension/ShareExtension.entitlements',
          'FileProvider/FileProvider.entitlements','AgentWidget/AgentWidget.entitlements',
          'Configs/ProviderCustomization.xcconfig.example','Shared/DeepLinkRouter.swift',
          'ShareExtension/ShareViewController.swift','Minis.xcodeproj/project.pbxproj')

@unittest.skipUnless(all((IOS/p).is_file() for p in (*gate.PINNED,*paste.PINNED,*REQUIRED)),
                     'Requires the fixed public upstream source fixture; no Apple commands run')
class SyncGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='codex-build-sync-test-')
        self.repo=Path(self.tmp.name)
        self.ios=self.repo/'src/ios'
        self.upstream_before={p:(IOS/p).read_bytes() for p in (*gate.PINNED,*paste.PINNED,*REQUIRED)}
        for rel,data in self.upstream_before.items():
            p=self.ios/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
        engine=self.ios/'CodexEngine';engine.mkdir()
        for name in FILES:shutil.copyfile(SOURCE/name,engine/name)
    def tearDown(self):
        # The read-only public source fixture must be unchanged by every test.
        for rel,data in self.upstream_before.items():self.assertEqual((IOS/rel).read_bytes(),data,rel)
        self.tmp.cleanup()
    def prepare_fixture(self):
        def pinned_head(command,**kwargs):
            self.assertEqual(command,['git','rev-parse','HEAD'])
            return subprocess.CompletedProcess(command,0,EXPECTED+'\n','')
        # Only Git's fixed HEAD response is simulated; the full source and plist
        # transformations below really run on the temporary copied files.
        with mock.patch.object(prep.subprocess,'run',side_effect=pinned_head):
            return prep.prepare(self.repo)
    def files(self):
        return {p.relative_to(self.repo).as_posix():p.read_bytes()
                for p in self.repo.rglob('*') if p.is_file()}

    def test_pinned_proposals_gate_engine_start_and_enable_paths(self):
        before=self.files()
        result=gate.proposed_changes(self.ios)
        self.assertEqual(set(result),set(gate.PINNED))
        self.assertEqual(before,self.files())
        v1=result['Agent/Sync/CloudSyncEngine.swift']
        start=v1.split('    func start() async {',1)[1]
        self.assertLess(start.index('guard Bundle.main.bundleIdentifier'),start.index('guard isEnabled'))
        self.assertIn('syncStatus = .disabled\n            return',start)
        observer=v1.split('@Published var isEnabled: Bool {',1)[1].split('@Published var syncSessions:',1)[0]
        self.assertLess(observer.index('isEnabled = false'),observer.index('if isEnabled {'))
        self.assertIn('Bundle.main.bundleIdentifier != "local.openminis.codex" && UserDefaults',v1)
        v2=result['Agent/Sync/V2/SyncV2Bootstrap.swift']
        for signature in ('static var isEnabled: Bool {','static func setEnabled(_ enabled: Bool) {',
                          'static func startIfEnabled() async {'):
            first=v2.split(signature,1)[1].splitlines()[1]
            self.assertIn('guard Bundle.main.bundleIdentifier != "local.openminis.codex"',first)

    def test_settings_only_exposes_notice_for_independent_identity(self):
        result=gate.proposed_changes(self.ios)
        for name in ('Views/Settings/CloudSyncSettingsView.swift','Views/Sync/CloudSyncSettingsV2View.swift'):
            text=result[name];original=(self.ios/name).read_text()
            self.assertIn('@ViewBuilder\n    var body: some View',text)
            branch=text.split('if Bundle.main.bundleIdentifier == "local.openminis.codex" {',1)[1].split('        } else {',1)[0]
            self.assertIn('iCloud 同步不可用',branch)
            for token in ('Toggle(','.task','CloudSyncEngine','CKContainer','performDeleteCloudData'):
                self.assertNotIn(token,branch)
            self.assertIn('Toggle(',text.split('        } else {',1)[1])
            # Basic structure guard, explicitly not a substitute for swiftc.
            self.assertEqual(text.count('{')-text.count('}'),original.count('{')-original.count('}'))
            boundary=('    }\n\n    // MARK: - Sections' if '/Settings/' in name else
                      '    }\n\n    /// [T-ios-migration-timer-sessionlist-uaf-crash] Self-cancelling 5s')
            # All helper views and methods after the main body stay byte-identical.
            self.assertEqual(text.split(boundary,1)[1],original.split(boundary,1)[1])

    def test_preparation_applies_gates_and_independent_permissions(self):
        result=self.prepare_fixture()
        self.assertTrue(result['unprovisioned_icloud_sync_disabled_in_source'])
        self.assertFalse(result['xcode_built'])
        for rel in gate.PINNED:
            self.assertIn('local.openminis.codex',(self.ios/rel).read_text())
        for identity,ent in result['signing_entitlements'].items():
            self.assertEqual(ent['application-identifier'],identity)
            self.assertEqual(ent['com.apple.security.application-groups'],[prep.GROUP])
            self.assertFalse(any('icloud' in key or 'ubiquity' in key for key in ent))
        info=plistlib.loads((self.ios/'Info.plist').read_bytes())
        schemes=[s for item in info['CFBundleURLTypes'] for s in item['CFBundleURLSchemes']]
        self.assertIn('miniscodex',schemes);self.assertNotIn('minis',schemes)
        stored=json.loads((self.repo/'CODEX-IPA-BUILD-PREPARED.json').read_text())
        self.assertTrue(stored['unprovisioned_icloud_sync_disabled_in_source'])
        with self.assertRaises(Refused):self.prepare_fixture()

    def test_mismatched_sync_source_refuses_before_any_write(self):
        rel=next(iter(gate.PINNED));p=self.ios/rel
        p.write_text(p.read_text()+'\n// deliberate fixture mismatch\n')
        before=self.files()
        self.assertNotEqual(blob_hash(p.read_bytes()),gate.PINNED[rel])
        with self.assertRaisesRegex(Refused,'differs from fixed upstream'):
            self.prepare_fixture()
        self.assertEqual(before,self.files())
        self.assertFalse((self.repo/'CODEX-IPA-BUILD-PREPARED.json').exists())

if __name__=='__main__':unittest.main(verbosity=2)
