"""Runs local Python/Git/parser/fixture tests, NEVER Codex or UIKit.
Usage: PYTHONPATH=/path/to/openstep_parser.whl python3 tests/test_overlay_tools.py
"""
from pathlib import Path
import contextlib
import io
import json
import os
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
import overlay_lib as lib
import apply_overlay as application
import prepare_codex_runtime as preparation
from openstep_parser import OpenStepDecoder

BASELINE = Path(os.environ.get('CODEX_OVERLAY_BASELINE', '/var/minis/workspace/openminis-research'))
CA = Path(os.environ.get('CODEX_OVERLAY_TEST_CA', '/var/minis/workspace/codex-pocket/resources/mozilla-ca.pem'))


def parse(text):
    return OpenStepDecoder.ParseFromFile(io.StringIO(text))


def fixture_macho(platform=2, minimum=0x100000):
    command = struct.pack('<6I', 0x32, 24, platform, minimum, 0x100400, 0)
    return struct.pack('<8I', 0xfeedfacf, 0x0100000c, 0, 2, 1, len(command), 0, 0) + command


def snapshot_repo(root):
    for name, text in lib.baseline(BASELINE).items():
        p = root/name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    # Unchanged local configuration is included to catch accidental overwrites.
    p = root/'src/ios/Configs/ProviderCustomization.xcconfig'
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('// test-only provider configuration\n')
    for args in (['init','-q'], ['add','.'], ['-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','fixture']):
        subprocess.run(['git',*args],cwd=root,check=True,stdout=subprocess.DEVNULL)


class OverlayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='codex-overlay-test-')
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_pinned_blobs_and_deterministic_patch(self):
        before = {n:(BASELINE/n).read_bytes() for n in lib.AFFECTED}
        base = lib.baseline(BASELINE)
        one = lib.integration_patch(base)
        self.assertEqual(one, lib.integration_patch(base))
        self.assertEqual(one, (ROOT/'patches/openminis-codex-integration.patch').read_text())
        self.assertEqual(before, {n:(BASELINE/n).read_bytes() for n in lib.AFFECTED})

    def test_pbx_parses_and_only_declared_sources_are_compiled(self):
        base = lib.baseline(BASELINE)
        original = parse(base[lib.AFFECTED[0]])
        edited = parse(lib.transform(base)[lib.AFFECTED[0]])
        old, obj = original['objects'], edited['objects']
        added = set(obj)-set(old)
        self.assertEqual(len(added), len(lib.FILES)+len(lib.COMPILED)+3)
        self.assertIn('OMProviderPaste.swift',lib.COMPILED)
        self.assertTrue(all(len(i)==24 and all(c in '0123456789ABCDEF' for c in i) for i in added))
        new_sources = set(obj['E51000041']['files'])-set(old['E51000041']['files'])
        names = {obj[obj[i]['fileRef']]['path'] for i in new_sources}
        self.assertEqual(names, set(lib.COMPILED))
        self.assertFalse(any(n.endswith('.h') for n in names))
        new_resources = set(obj['E51000042']['files'])-set(old['E51000042']['files'])
        self.assertEqual(len(new_resources), 1)
        runtime_ref = obj[obj[new_resources.pop()]['fileRef']]
        self.assertEqual(runtime_ref['path'], 'CodexRuntime')
        self.assertEqual(runtime_ref['lastKnownFileType'], 'folder')
        for key, value in old.items():
            if key not in ('E51000031','E51000041','E51000042'):
                self.assertEqual(value,obj[key],key)
        self.assertEqual(obj['E51000040'],old['E51000040'])
        self.assertEqual(obj['E51000072'],old['E51000072'])
        self.assertEqual(obj['E51000073'],old['E51000073'])

    def test_git_apply_dry_run_no_changes_and_source_application(self):
        repo=self.root/'repo';repo.mkdir();snapshot_repo(repo)
        config=repo/'src/ios/Configs/ProviderCustomization.xcconfig'
        original_config=config.read_bytes()
        before={n:(repo/n).read_bytes() for n in lib.AFFECTED}
        inputs,patch,resources=application.plan(repo)
        self.assertIsNone(resources)
        self.assertEqual(before,{n:(repo/n).read_bytes() for n in lib.AFFECTED})
        self.assertFalse((repo/'src/ios/CodexEngine').exists())
        application.apply_transaction(repo,inputs,patch)
        self.assertEqual(config.read_bytes(),original_config)
        expected=lib.transform(inputs)
        for n in lib.AFFECTED:self.assertEqual((repo/n).read_text(),expected[n])
        target=repo/'src/ios/CodexEngine'
        self.assertEqual({p.name for p in target.iterdir()},set(lib.FILES)|{'CodexRuntime'})
        self.assertFalse((target/'CodexRuntime/codex').exists())
        self.assertEqual((target/'CodexRuntime/text-only.toml').read_bytes(),lib.PROFILE.read_bytes())
        subprocess.run(['git','diff','--check'],cwd=repo,check=True)
        with self.assertRaises(lib.Refused):application.plan(repo)

    def test_application_failure_rolls_back_own_changes(self):
        repo=self.root/'repo';repo.mkdir();snapshot_repo(repo)
        inputs,patch,_=application.plan(repo)
        real_git=application.git
        def broken_git(root,*args,**kwargs):
            if args[:1]==('apply',) and '--check' not in args:
                result=real_git(root,*args,**kwargs)
                self.assertEqual(result.returncode,0)
                return subprocess.CompletedProcess(args,1,b'',b'fixture failure after apply')
            return real_git(root,*args,**kwargs)
        with mock.patch.object(application,'git',side_effect=broken_git):
            with self.assertRaises(lib.Refused):application.apply_transaction(repo,inputs,patch)
        for n in lib.AFFECTED:self.assertEqual((repo/n).read_text(),inputs[n])
        self.assertFalse((repo/'src/ios/CodexEngine').exists())
        self.assertFalse(list((repo/'src/ios').glob('.codex-stage-*')))

    def test_wrong_commit_and_changed_baseline_are_refused(self):
        repo=self.root/'repo';repo.mkdir();snapshot_repo(repo)
        with self.assertRaises(lib.Refused):application.verify_checkout(repo)
        p=repo/lib.AFFECTED[2];p.write_text(p.read_text()+'\n// unrelated local change\n')
        with self.assertRaises(lib.Refused):application.plan(repo)
        self.assertFalse((repo/'src/ios/CodexEngine').exists())

    def test_device_platform_checked_not_just_arm64_magic(self):
        p=self.root/'fixture';p.write_bytes(fixture_macho())
        self.assertEqual(lib.macho_info(p)['platform'],'iOS-device')
        self.assertFalse(lib.macho_info(p)['signature_authenticated'])
        for raw in (fixture_macho(1),fixture_macho(7),fixture_macho(2,0x110000),b'not Mach-O'):
            p.write_bytes(raw)
            with self.assertRaises(lib.Refused):lib.macho_info(p)

    def test_runtime_profile_ca_and_symlink_validation(self):
        runtime=self.root/'runtime';runtime.mkdir()
        p=runtime/'codex';p.write_bytes(fixture_macho());p.chmod(0o755)
        shutil.copyfile(CA,runtime/'ca-certificates.crt')
        shutil.copyfile(lib.PROFILE,runtime/'text-only.toml')
        report=lib.validate_runtime(runtime)
        self.assertFalse(report['codex']['signature_authenticated'])
        (runtime/'text-only.toml').write_text('cli_auth_credentials_store="file"\n')
        with self.assertRaises(lib.Refused):lib.validate_runtime(runtime)
        shutil.copyfile(lib.PROFILE,runtime/'text-only.toml')
        (runtime/'ca-certificates.crt').write_bytes(b'wrong CA')
        with self.assertRaises(lib.Refused):lib.validate_runtime(runtime)
        shutil.copyfile(CA,runtime/'ca-certificates.crt')
        p.unlink();p.symlink_to(CA)
        with self.assertRaises(lib.Refused):lib.validate_runtime(runtime)

    def test_authorization_acknowledgement_does_not_create_files(self):
        out=self.root/'not-created'
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                preparation.main(['--deb','missing.deb','--ca-bundle','missing.crt','--out',str(out)])
        self.assertFalse(out.exists())

    def test_bounded_reader_and_traversal(self):
        reader=preparation.BoundedReader(io.BytesIO(b'123456'),5)
        with self.assertRaises(lib.Refused):reader.read(64)
        for name in ('../../usr/libexec/codex/codex','/usr/libexec/codex/codex','./../usr/libexec/codex/codex'):
            with self.assertRaises(lib.Refused):preparation.canonical_member(name)
        self.assertEqual(preparation.canonical_member('./usr/libexec/codex/codex'),'usr/libexec/codex/codex')

    def fixture_tar(self, duplicate=False, symlink=False):
        raw=io.BytesIO()
        with tarfile.open(fileobj=raw,mode='w') as archive:
            for name in preparation.MEMBERS:
                info=tarfile.TarInfo('./'+name)
                if symlink and name.endswith('/codex'):
                    info.type=tarfile.SYMTYPE;info.linkname='/outside';archive.addfile(info)
                else:
                    payload=fixture_macho();info.size=len(payload)
                    archive.addfile(info,io.BytesIO(payload))
                    if duplicate:archive.addfile(info,io.BytesIO(payload))
        return raw.getvalue()

    def test_extracts_only_named_regular_runtime_members(self):
        output=self.root/'files';output.mkdir()
        preparation.extract_runtime(io.BytesIO(self.fixture_tar()),output)
        self.assertEqual({p.name for p in output.iterdir()},set(preparation.MEMBERS.values()))
        for name in preparation.MEMBERS.values():self.assertEqual((output/name).read_bytes(),fixture_macho())
        for kwargs in ({'duplicate':True},{'symlink':True}):
            separate=self.root/str(len(list(self.root.iterdir())));separate.mkdir()
            with self.assertRaises(lib.Refused):preparation.extract_runtime(io.BytesIO(self.fixture_tar(**kwargs)),separate)

    def test_complete_synthetic_deb_pipeline_without_execution(self):
        # Both Mach-O entries are 56-byte fake headers; none is executable code.
        source=self.root/'package';source.mkdir()
        (source/'debian-binary').write_bytes(b'2.0\n')
        raw=self.fixture_tar()
        compressed=subprocess.run(['zstd','-q','-c'],input=raw,capture_output=True,check=True).stdout
        (source/'data.tar.zst').write_bytes(compressed)
        deb=source/'fixture.deb'
        subprocess.run(['ar','rc',str(deb),'debian-binary','data.tar.zst'],cwd=source,check=True)
        staged=self.root/'staged'
        report=preparation.prepare(deb,CA,staged)
        self.assertEqual((staged/'codex').read_bytes(),fixture_macho())
        self.assertFalse(report['codex']['signature_authenticated'])
        self.assertEqual((staged/'text-only.toml').read_bytes(),lib.PROFILE.read_bytes())
        self.assertTrue((staged/'runtime-manifest.json').is_file())
        for p in ('auth.json','config.toml'):
            self.assertFalse((staged/p).exists())
        with self.assertRaises(lib.Refused):preparation.prepare(deb,CA,staged)
        # Malformed package does not leave an apparently valid output directory.
        (source/'data.tar.zst').write_bytes(b'not zstd')
        subprocess.run(['ar','r',str(deb),'data.tar.zst'],cwd=source,check=True)
        bad=self.root/'bad'
        with self.assertRaises((lib.Refused,tarfile.TarError)):
            preparation.prepare(deb,CA,bad)
        self.assertFalse(bad.exists())
        self.assertFalse(list(self.root.glob('.codex-runtime-*')))

    def test_profile_digest_matches_native_guard(self):
        native=(lib.SOURCE/'OMCodexProcessHost.m').read_text()
        self.assertIn(lib.digest(lib.PROFILE),native)
        self.assertIn('drainDeadline=OMNow()+1.0',native)
        self.assertNotIn('return YES;\n return YES;',native)

    def test_application_stages_runtime_under_correct_bundle_folder(self):
        repo=self.root/'repo';repo.mkdir();snapshot_repo(repo)
        runtime=self.root/'runtime';runtime.mkdir()
        p=runtime/'codex';p.write_bytes(fixture_macho());p.chmod(0o755)
        shutil.copyfile(CA,runtime/'ca-certificates.crt')
        shutil.copyfile(lib.PROFILE,runtime/'text-only.toml')
        inputs,patch,resources=application.plan(repo,runtime)
        self.assertEqual(resources['codex']['platform'],'iOS-device')
        application.apply_transaction(repo,inputs,patch,runtime)
        target=repo/'src/ios/CodexEngine/CodexRuntime'
        self.assertEqual((target/'codex').read_bytes(),fixture_macho())
        self.assertEqual((target/'text-only.toml').read_bytes(),lib.PROFILE.read_bytes())
        self.assertTrue((target/'codex').stat().st_mode & 0o111)
        self.assertFalse((repo/'src/ios/CodexEngine/codex').exists())

if __name__=='__main__':
    unittest.main(verbosity=2)
