"""Actual source/copy/fixture tests, not live iOS clipboard or Swift execution."""
from pathlib import Path
import os, shutil, plistlib, subprocess, sys, tempfile, unittest
from unittest import mock
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import repair_provider_paste as paste
import disable_unprovisioned_sync as sync
import prepare_ipa_build as prep
from overlay_lib import SOURCE, FILES, EXPECTED, Refused
UPSTREAM=Path(os.environ.get('CODEX_IPA_TEST_UPSTREAM',str(ROOT/'build/upstream-full'/('OpenMinis-'+EXPECTED))))
IOS=UPSTREAM/'src/ios'
EXTRA=('Info.plist','Minis.entitlements','ShareExtension/ShareExtension.entitlements',
       'FileProvider/FileProvider.entitlements','AgentWidget/AgentWidget.entitlements',
       'Configs/ProviderCustomization.xcconfig.example','Shared/DeepLinkRouter.swift',
       'ShareExtension/ShareViewController.swift','Minis.xcodeproj/project.pbxproj')
SOURCES=tuple(dict.fromkeys((*paste.PINNED,*sync.PINNED,*EXTRA)))

@unittest.skipUnless(all((IOS/n).is_file() for n in SOURCES),'Needs fixed public source fixture')
class ProviderPastePatchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='provider-paste-patch-')
        self.repo=Path(self.temp.name);self.ios=self.repo/'src/ios'
        self.original={n:(IOS/n).read_bytes() for n in SOURCES}
        for n,b in self.original.items():
            p=self.ios/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b)
        engine=self.ios/'CodexEngine';engine.mkdir()
        for n in FILES:shutil.copyfile(SOURCE/n,engine/n)
    def tearDown(self):
        for n,b in self.original.items():self.assertEqual((IOS/n).read_bytes(),b,n)
        self.temp.cleanup()
    def files(self):
        return {p.relative_to(self.repo).as_posix():p.read_bytes() for p in self.repo.rglob('*') if p.is_file()}
    def prepare(self):
        with mock.patch.object(prep.subprocess,'run',return_value=subprocess.CompletedProcess([],0,EXPECTED+'\n','')):
            return prep.prepare(self.repo)

    def test_only_four_rows_added_without_changing_mask_store_or_protocol(self):
        before=self.files();proposed=paste.proposed_changes(self.ios)
        self.assertEqual(before,self.files())
        self.assertEqual(set(proposed),set(paste.PINNED))
        for n,text in proposed.items():
            original=self.original[n].decode()
            clean=[line for line in text.splitlines() if line.strip() and 'OMProviderPasteRow(' not in line]
            expected=[line for line in original.splitlines() if line.strip()]
            self.assertEqual(clean,expected,n)
            self.assertEqual(text.count('OMProviderPasteRow('),2)
        add=proposed['Views/Providers/AddProviderView.swift']
        self.assertIn('text: $apiKeyInput, fieldID: pendingInstanceId + ":api-key", secret: true',add)
        self.assertIn('text: $customBaseURLInput, fieldID: pendingInstanceId + ":base-url", secret: false',add)
        edit=proposed['Views/Providers/ProviderInstanceDetailView.swift']
        self.assertIn('text: $keyInputText, fieldID: instance.id + ":api-key", secret: true',edit)
        self.assertIn('text: $editingCustomBaseURL, fieldID: instance.id + ":base-url", secret: false',edit)

    def test_prepare_includes_rows_build2_and_preserves_sync_and_entitlements(self):
        result=self.prepare()
        self.assertTrue(result['provider_system_paste_controls_enabled'])
        self.assertTrue(result['unprovisioned_icloud_sync_disabled_in_source'])
        self.assertEqual(result['ipa_build_number'],'2')
        self.assertFalse(result['codex_core_included'])
        info=plistlib.loads((self.ios/'Info.plist').read_bytes())
        self.assertEqual(info['CFBundleVersion'],'2')
        for n in paste.PINNED:self.assertEqual((self.ios/n).read_text().count('OMProviderPasteRow('),2)
        for n in sync.PINNED:self.assertIn('local.openminis.codex',(self.ios/n).read_text())
        for identity,ent in result['signing_entitlements'].items():
            self.assertEqual(ent['application-identifier'],identity)
            self.assertEqual(ent['com.apple.security.application-groups'],[prep.GROUP])
            self.assertFalse(any('icloud' in k or 'ubiquity' in k for k in ent))
        with self.assertRaises(Refused):self.prepare()

    def test_changed_provider_source_is_rejected_before_any_write(self):
        p=self.ios/next(iter(paste.PINNED));p.write_text(p.read_text()+'\n// deliberate fixture mismatch\n')
        before=self.files()
        with self.assertRaisesRegex(Refused,'Fixed provider source blob differs'):self.prepare()
        self.assertEqual(before,self.files())
        self.assertFalse((self.repo/'CODEX-IPA-BUILD-PREPARED.json').exists())

    def test_missing_provider_source_or_duplicate_application_refuses(self):
        n=next(iter(paste.PINNED));p=self.ios/n;p.unlink()
        before=self.files()
        with self.assertRaises(Refused):paste.proposed_changes(self.ios)
        self.assertEqual(before,self.files())
        p.write_bytes(self.original[n]);patched=paste.proposed_changes(self.ios)
        for rel,text in patched.items():(self.ios/rel).write_text(text)
        before=self.files()
        with self.assertRaises(Refused):paste.proposed_changes(self.ios)
        self.assertEqual(before,self.files())

    def test_native_receiver_is_registered_and_sensitive_data_is_never_logged(self):
        src=(SOURCE/'OMProviderPaste.swift').read_text()
        self.assertIn('OMProviderPaste.swift',FILES)
        for token in ('UIPasteControl(configuration:', 'control.target = context.coordinator',
                      'override func canPaste(_ itemProviders:', 'override func paste(itemProviders:', 'loadObject(ofClass: NSString.self)',
                      'destination.wrappedValue = text', 'pending.generation == request.generation',
                      'destination.wrappedValue == request.initialValue', 'DispatchQueue.main.asyncAfter',
                      'state.suspend(fieldID: fieldID)', 'coordinator.state.detach'):
            self.assertIn(token,src)
        for token in ('UIPasteboard.general','ProviderKeychainHelper.', 'SecItem', 'NSLog(', 'print(', 'AppLogger('):
            self.assertNotIn(token,src)
        feedback=src.split('var message: String {',1)[1].split('var isError:',1)[0]
        self.assertNotIn('\\(',feedback)
        self.assertIn('CURRENT_PROJECT_VERSION=2',(ROOT/'ci/build_openminis.sh').read_text())

if __name__=='__main__':unittest.main(verbosity=2)
