"""Local packaging tests ONLY. Smoke binaries are NOT OpenMinis and NEVER run."""
from pathlib import Path
import contextlib, hashlib, io, json, os, plistlib, shutil, struct, subprocess, sys, tarfile, tempfile, unittest, zipfile
from unittest import mock
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import ipa_bundle as bundle
import finalize_ipa as final
import public_build_artifact as artifact
import prepare_ipa_build as prep
from overlay_lib import Refused, EXPECTED, FILES, SOURCE, PROFILE, digest
CA=Path(os.environ.get('CODEX_OVERLAY_TEST_CA','/var/minis/workspace/codex-pocket/resources/mozilla-ca.pem'))
LDID=Path('/opt/ios-toolchain/bin/ldid-ish')
SMOKE=Path('/opt/ios-toolchain/testtmp/SDKSmoke')


def fixture(platform=2,minimum=0x100500,kind=2,encrypted=False):
    commands=struct.pack('<6I',0x32,24,platform,minimum,0x100500,0)
    if encrypted:commands+=struct.pack('<6I',0x2c,24,0,32,1,0)
    return struct.pack('<8I',0xfeedfacf,0x100000c,0,kind,2 if encrypted else 1,len(commands),0,0)+commands


def signing_manifest(runtime_probe=True):
    ents={i:{'application-identifier':i,'com.apple.security.application-groups':[bundle.GROUP]} for i in final.BUNDLE_IDS}
    ents[bundle.APP_ID].update(final.CORE_ENT)
    return {'upstream_commit':EXPECTED,'bundle_id':bundle.APP_ID,'codex_core_included':False,
            'runtime_probe':runtime_probe,'no_codex_probe':not runtime_probe,
            'signing_entitlements':ents,'test_fixture_only':True}


class IPATests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='codex-ipa-test-')
        self.root=Path(self.tmp.name)
    def tearDown(self):self.tmp.cleanup()
    def app(self,root=None,real=False):
        app=(root or self.root)/'Minis.app';app.mkdir(parents=True)
        info={'CFBundleIdentifier':bundle.APP_ID,'CFBundleExecutable':'Smoke',
              'CFBundleSupportedPlatforms':['iPhoneOS'],'MinimumOSVersion':'16.5'}
        (app/'Info.plist').write_bytes(plistlib.dumps(info))
        exe=app/'Smoke'
        if real:shutil.copyfile(SMOKE,exe)
        else:exe.write_bytes(fixture()+b'OMCodexProcessHost')
        exe.chmod(0o755)
        return app
    def runtime(self):
        p=self.root/'runtime';p.mkdir()
        shutil.copyfile(SMOKE,p/'codex');(p/'codex').chmod(0o755)
        shutil.copyfile(CA,p/'ca-certificates.crt');shutil.copyfile(PROFILE,p/'text-only.toml')
        return p

    def test_macho_platform_encryption_and_bounds_rejected(self):
        p=self.root/'fixture'
        for content in (fixture(platform=7),fixture(minimum=0x110000),fixture(encrypted=True),b'not a Mach-O'):
            p.write_bytes(content)
            with self.assertRaises(Refused):bundle.inspect_macho(p)
        p.write_bytes(fixture())
        self.assertEqual(bundle.inspect_macho(p)['platform'],'iOS-device')
        with self.assertRaises(Refused):bundle.inspect_macho(p,True)

    def test_inventory_rejects_shared_app_identity_links_and_bad_minimum(self):
        app=self.app();info=plistlib.loads((app/'Info.plist').read_bytes())
        for key,value in [('CFBundleIdentifier','com.openminis.app'),('MinimumOSVersion','17.0'),('MinimumOSVersion','invalid'),('CFBundleExecutable','../escape')]:
            bad={**info,key:value};(app/'Info.plist').write_bytes(plistlib.dumps(bad))
            with self.assertRaises(Refused):bundle.inventory(app)
        (app/'Info.plist').write_bytes(plistlib.dumps(info))
        (app/'link').symlink_to('/etc/passwd')
        with self.assertRaises(Refused):bundle.inventory(app)
        (app/'link').unlink()
        self.assertEqual(len(bundle.inventory(app)[2]),1)

    def test_signing_manifest_rejects_extra_privileges_and_wrong_group(self):
        manifest=signing_manifest();final.signing_policy(manifest)
        for key,value in [('com.apple.private.security.storage.AppDataContainers',True),('com.apple.security.application-groups',['group.com.openminis.app'])]:
            bad=json.loads(json.dumps(manifest));bad['signing_entitlements'][bundle.APP_ID][key]=value
            with self.assertRaises(Refused):final.signing_policy(bad)
        bad=json.loads(json.dumps(manifest));bad['signing_entitlements'][bundle.APP_ID+'.ShareExtension'].update(final.CORE_ENT)
        with self.assertRaises(Refused):final.signing_policy(bad)

    def test_public_artifact_extract_roundtrip_and_digest_tampering(self):
        app=self.app();prepared=self.root/'prepared.json';prepared.write_text(json.dumps(signing_manifest()))
        out=self.root/'artifact';artifact.stage(app,prepared,out)
        dest=self.root/'unpacked';dest.mkdir()
        restored,m=final.extract_artifact(out,dest)
        self.assertEqual((restored/'Smoke').read_bytes(),(app/'Smoke').read_bytes())
        self.assertTrue(m['test_fixture_only'])
        with (out/'OpenMinisCodex-unsigned-app.tar.gz').open('ab') as f:f.write(b'tampered')
        new=self.root/'bad';new.mkdir()
        with self.assertRaisesRegex(Refused,'digest mismatch'):final.extract_artifact(out,new)

    def test_public_artifact_refuses_user_runtime(self):
        app=self.app();prepared=self.root/'prepared.json';prepared.write_text(json.dumps(signing_manifest()))
        rt=app/'CodexRuntime';rt.mkdir();(rt/'codex').write_bytes(fixture())
        with self.assertRaisesRegex(Refused,'User runtime'):artifact.stage(app,prepared,self.root/'artifact')
        self.assertFalse((self.root/'artifact').exists())

    @unittest.skipUnless(LDID.is_file() and SMOKE.is_file() and CA.is_file(),'local signer/smoke/CA unavailable')
    def test_real_ldid_nested_signing_then_complete_local_finalizer(self):
        app=self.app(real=True)
        ext=app/'PlugIns/Share.appex';ext.mkdir(parents=True)
        (ext/'Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier':bundle.APP_ID+'.ShareExtension','CFBundleExecutable':'Smoke'}))
        shutil.copyfile(SMOKE,ext/'Smoke');(ext/'Smoke').chmod(0o755)
        manifest=signing_manifest();prepared=self.root/'prepared.json';prepared.write_text(json.dumps(manifest))
        # Only the symbol check is mocked: this program is the compiler smoke test,
        # NOT OpenMinis and not evidence of a full Swift/Xcode build.
        with mock.patch.object(artifact,'contains_overlay_host',return_value=True):
            out=self.root/'artifact';artifact.stage(app,prepared,out)
        runtime=self.runtime();ipa=self.root/'SMOKE-ONLY-NEVER-INSTALL.ipa'
        report=final.finalize(out,runtime,LDID,ipa,authorized=True,execute=True)
        self.assertTrue(report['ipa_packaged']);self.assertFalse(report['device_tested'])
        self.assertFalse(report['codex_executed']);self.assertFalse(report['xcode_built_here'])
        with zipfile.ZipFile(ipa) as z:
            self.assertIsNone(z.testzip())
            self.assertIn('Payload/Minis.app/CodexRuntime/codex',z.namelist())
            self.assertTrue((z.getinfo('Payload/Minis.app/CodexRuntime/codex').external_attr>>16)&0o111)
            for rel,expected in [('CodexRuntime/codex',final.CORE_ENT),('PlugIns/Share.appex/Smoke',manifest['signing_entitlements'][bundle.APP_ID+'.ShareExtension']),('Smoke',manifest['signing_entitlements'][bundle.APP_ID])]:
                p=self.root/('signed-'+str(len(rel)));p.write_bytes(z.read('Payload/Minis.app/'+rel))
                self.assertEqual(bundle.inspect_macho(p,True)['entitlements'],expected)
        before=ipa.read_bytes()
        with self.assertRaises(Refused):final.finalize(out,runtime,LDID,ipa,authorized=True,execute=True)
        self.assertEqual(ipa.read_bytes(),before)

    @unittest.skipUnless(LDID.is_file() and SMOKE.is_file() and CA.is_file(),'local signer/smoke/CA unavailable')
    def test_no_codex_probe_finalizes_without_any_runtime_injection(self):
        app=self.app(real=True)
        ext=app/'PlugIns/Share.appex';ext.mkdir(parents=True)
        (ext/'Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier':bundle.APP_ID+'.ShareExtension','CFBundleExecutable':'Smoke'}))
        shutil.copyfile(SMOKE,ext/'Smoke');(ext/'Smoke').chmod(0o755)
        manifest=signing_manifest(runtime_probe=False);prepared=self.root/'prepared.json';prepared.write_text(json.dumps(manifest))
        with mock.patch.object(artifact,'contains_overlay_host',return_value=True):
            out=self.root/'no-codex-artifact';artifact.stage(app,prepared,out)
        empty_runtime=self.root/'empty-runtime';empty_runtime.mkdir()
        ipa=self.root/'NO-CODEX-SMOKE-ONLY.ipa'
        report=final.finalize(out,empty_runtime,LDID,ipa,authorized=True,execute=True,runtime_probe=False)
        self.assertTrue(report['ipa_packaged']);self.assertFalse(report['runtime_injected'])
        with zipfile.ZipFile(ipa) as z:
            self.assertIsNone(z.testzip())
            self.assertNotIn('Payload/Minis.app/CodexRuntime/codex',z.namelist())
            self.assertNotIn('Payload/Minis.app/CodexRuntime/codex-code-mode-host',z.namelist())

if __name__=='__main__':unittest.main(verbosity=2)
