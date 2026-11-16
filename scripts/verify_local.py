#!/usr/bin/env python3
"""Run local checks, not Swift/Xcode/UI tests. NEVER executes a Codex runtime."""
from pathlib import Path
import argparse
import ctypes
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from overlay_lib import ROOT, EXPECTED, PROFILE, FILES, baseline


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--baseline',type=Path,required=True)
    ap.add_argument('--ca-bundle',type=Path,required=True,help='Public pinned Mozilla file, never a credential')
    ap.add_argument('--sdk',type=Path,default=Path('/opt/ios-toolchain/sdks/iPhoneOS16.5.sdk'))
    ap.add_argument('--cc',default='clang-19')
    args=ap.parse_args()
    build=ROOT/'build';build.mkdir(exist_ok=True)
    logs=build/'verification.log'
    # A failed rerun must not leave an older success report or mixed test log.
    logs.write_text('')
    (ROOT/'VERIFICATION.json').unlink(missing_ok=True)
    completed=[]
    def run(label,command,env=None):
        with logs.open('ab') as out:
            out.write(('\nCHECK: '+label+'\n').encode())
            out.flush()
            subprocess.run([str(p) for p in command],cwd=ROOT,stdout=out,stderr=out,env=env,check=True)
        completed.append(label)
    baseline(args.baseline)
    native=ROOT/'overlay/src/ios/CodexEngine'
    common=[args.cc,'--target=arm64-apple-ios16.5','-isysroot',args.sdk,'-Wall','-Wextra','-Werror']
    run('objc_ios16_5_strict_syntax',common+['-fobjc-arc','-fblocks','-fsyntax-only',native/'OMCodexProcessHost.m'])
    run('c_ios16_5_object_compile',common+['-c',native/'OMCodexFramer.c','-o',build/'OMCodexFramer.o'])
    run('c_native_fixture_build',[args.cc,'-Wall','-Wextra','-Werror','-shared','-fPIC',native/'OMCodexFramer.c',ROOT/'overlay/tests/test_framer.c','-o',build/'framer_fixture.so'])
    assert ctypes.CDLL(str(build/'framer_fixture.so')).main()==0
    completed.append('c_framer_runtime_via_ctypes')
    profile=tomllib.loads(PROFILE.read_text())
    assert profile['cli_auth_credentials_store']=='ephemeral'
    assert profile['skills']['bundled']['enabled'] is False
    assert all(value is False for value in profile['features'].values())
    completed.append('profile_toml_ephemeral_and_disabled_features')
    env=os.environ.copy()
    env['CODEX_OVERLAY_BASELINE']=str(args.baseline.resolve())
    env['CODEX_OVERLAY_TEST_CA']=str(args.ca_bundle.resolve())
    run('python_git_pbx_runtime_fixture_tests',[sys.executable,ROOT/'tests/test_overlay_tools.py'],env)
    run('apple_validation_generator_and_failure_report_tests',[sys.executable,ROOT/'tests/test_apple_validation_tools.py'],env)
    revision=subprocess.run(['git','rev-parse','HEAD'],cwd=ROOT,text=True,capture_output=True).stdout.strip()
    source_files={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in native.iterdir() if p.name in FILES}
    fixture=ROOT/'tests/swift-fixture/CodexSessionEngineTests.swift'
    source_files[str(fixture.relative_to(ROOT))]=hashlib.sha256(fixture.read_bytes()).hexdigest()
    log_text=logs.read_text()
    counts=re.findall(r'^Ran (\d+) tests? in [^\n]+\n\nOK$',log_text,re.M)
    if len(counts)!=2:
        raise SystemExit('Expected two successful unittest summaries in the actual test log')
    report={
        'upstream_commit':EXPECTED,'overlay_commit_before_verification':revision,
        'local_checks_passed':completed,'local_test_log':'build/verification.log',
        'checked_source_sha256':source_files,
        'runtime_executed':False,'swift_typechecked':False,'xctest_executed':False,
        'xcode_project_opened_or_built':False,'ios_device_tested':False,
        'app_or_tipa_built':False,'runtime_signature_authenticated':False,
        'apple_validation_project_generator_tested':True,
        'apple_validation_project_built':False,
        'swift_fixture_cases_defined_not_executed':len(re.findall(r'^\s*func test\w+\s*\(', (ROOT/'tests/swift-fixture/CodexSessionEngineTests.swift').read_text(), re.M)),
        'python_tool_test_cases_passed':sum(int(count) for count in counts),
    }
    (ROOT/'VERIFICATION.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':
    main()
