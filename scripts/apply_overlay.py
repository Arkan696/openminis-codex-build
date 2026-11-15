#!/usr/bin/env python3
"""Apply the reviewed Codex overlay to a full OpenMinis checkout.

This script is intentionally opt-in and defaults to --dry-run. It expects a
macOS/Xcode checkout at --repo, a user-authorized CodexRuntime directory, and
will refuse if the OpenMinis commit or main target does not match the audit.
It does not upload files, read Keychain, or copy user credentials.
"""
from pathlib import Path
import argparse, hashlib, plistlib, shutil, subprocess, sys
EXPECTED='1a71df0f381cefde80bf912b723de6f9831bc177'
ROOT=Path(__file__).resolve().parents[1]
def run(cmd,cwd):return subprocess.run(cmd,cwd=cwd,text=True,capture_output=True)
def die(msg):raise SystemExit('REFUSED: '+msg)
ap=argparse.ArgumentParser();ap.add_argument('--repo',type=Path,required=True);ap.add_argument('--runtime',type=Path,required=True);ap.add_argument('--apply',action='store_true');a=ap.parse_args();repo=a.repo.resolve();runtime=a.runtime.resolve()
if not (repo/'src/ios/Minis.xcodeproj/project.pbxproj').is_file():die('not an OpenMinis checkout')
git=run(['git','rev-parse','HEAD'],repo)
if git.returncode or git.stdout.strip()!=EXPECTED:die(f'expected fixed commit {EXPECTED}, got {git.stdout.strip()!r}')
pbx=(repo/'src/ios/Minis.xcodeproj/project.pbxproj').read_text()
if 'PRODUCT_BUNDLE_IDENTIFIER = com.openminis.app;' not in pbx or pbx.count('IPHONEOS_DEPLOYMENT_TARGET = 16.0;')<2:die('main target audit markers missing')
for name in ['codex','ca-certificates.crt','text-only.toml']:
 p=runtime/name
 if not p.is_file():die(f'missing runtime resource {p}')
if not (runtime/'codex').stat().st_mode & 0o111:die('runtime codex is not executable')
print('target commit:',EXPECTED,'main target baseline: iOS16/Swift5')
print('overlay files:',len(list((ROOT/'overlay/src/ios/CodexEngine').rglob('*'))))
print('runtime resources:',runtime)
if not a.apply:
 print('DRY RUN: no files changed. Pass --apply only from a reviewed macOS checkout.')
 raise SystemExit(0)
# Copy source overlay; PBX edits remain a deliberate review step because Xcode may
# be using synchronized groups in a different local checkout.
dst=repo/'src/ios/CodexEngine';dst.mkdir(parents=True,exist_ok=True)
for p in (ROOT/'overlay/src/ios/CodexEngine').iterdir():
 if p.name=='CodexEngineResources':continue
 if p.is_file():shutil.copy2(p,dst/p.name)
res=dst/'CodexEngineResources';res.mkdir(exist_ok=True)
for p in (runtime/'codex',runtime/'ca-certificates.crt',runtime/'text-only.toml'):
 shutil.copy2(p,res/p.name)
print('Applied source/resource overlay. Add files to the Minis target in Xcode, then build the native dependencies and run tests.')
