#!/usr/bin/env python3
"""Generate reviewed Xcode integration files for the pinned OpenMinis checkout.

Default is dry-run. --apply only copies a new isolated CodexEngine directory,
adds one bridging-header import and a separately named CodexOverlay.xcconfig.
It deliberately does NOT edit project.pbxproj or ContentView: the user must
add the engine sources/resources and review the UI action in Xcode.
"""
from pathlib import Path
import argparse,subprocess
EXPECTED='1a71df0f381cefde80bf912b723de6f9831bc177'
ROOT=Path(__file__).resolve().parents[1]
ap=argparse.ArgumentParser();ap.add_argument('--repo',type=Path,required=True);ap.add_argument('--apply',action='store_true');a=ap.parse_args();repo=a.repo.resolve()
pbx=repo/'src/ios/Minis.xcodeproj/project.pbxproj'
if not pbx.is_file():raise SystemExit('REFUSED: not an OpenMinis checkout')
git=subprocess.run(['git','rev-parse','HEAD'],cwd=repo,text=True,capture_output=True)
if git.returncode or git.stdout.strip()!=EXPECTED:raise SystemExit('REFUSED: wrong OpenMinis commit')
bridging=repo/'src/ios/MinisApp-Bridging-Header.h'
if not bridging.is_file():raise SystemExit('REFUSED: bridging header missing')
imp='#import "CodexEngine/OMCodexProcessHost.h"'
if imp in bridging.read_text():raise SystemExit('REFUSED: Codex host already imported; inspect manually')
print('Pinned source:',EXPECTED)
print('Target baseline: Minis app iOS16.0 / Swift5; no project.pbxproj edits will be made.')
print('Files to copy:',len([p for p in (ROOT/'overlay/src/ios/CodexEngine').rglob('*') if p.is_file()]))
print('New xcconfig: src/ios/Configs/CodexOverlay.xcconfig (does not overwrite existing provider config)')
if not a.apply:
 print('DRY RUN: use --apply only in a clean macOS checkout after review.');raise SystemExit(0)
dst=repo/'src/ios/CodexEngine';dst.mkdir(parents=True,exist_ok=False)
import shutil
shutil.copytree(ROOT/'overlay/src/ios/CodexEngine',dst,dirs_exist_ok=True)
# User-supplied authorized resources must be added in Xcode; never source from this script.
with (repo/'src/ios/MinisApp-Bridging-Header.h').open('a') as f:f.write('\n// Codex independent session engine (GPLv3 overlay)\n'+imp+'\n')
config=repo/'src/ios/Configs/CodexOverlay.xcconfig'
config.write_text('''// Optional Codex overlay build settings. Merge this file with the Minis target's existing base configuration in Xcode; do not replace ProviderCustomization.xcconfig.\nCODEX_ENGINE_ENABLED = NO\nCODEX_ENGINE_RESOURCE_SUBPATH = CodexRuntime\n''')
print('Applied isolated Swift/ObjC/C source overlay and new xcconfig. No secrets or binaries copied.')
print('Manual Xcode follow-up remains: add source files/resources to Minis target and explicitly enable CODEX_ENGINE_ENABLED after building/tests.')
