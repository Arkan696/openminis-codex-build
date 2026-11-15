#!/usr/bin/env python3
"""Opt-in source overlay for a full OpenMinis checkout.

Default is a dry run. It copies CodexEngine source/resources only and writes a
small integration note; PBX source/resource membership and toolbar edits are
left for a Mac/Xcode review because this research tree is not a git checkout.
"""
from pathlib import Path
import argparse, subprocess, sys
ROOT=Path(__file__).resolve().parents[1]
EXPECTED='1a71df0f381cefde80bf912b723de6f9831bc177'
ap=argparse.ArgumentParser();ap.add_argument('--repo',type=Path,required=True);ap.add_argument('--apply',action='store_true');a=ap.parse_args();repo=a.repo.resolve()
if not (repo/'src/ios/Minis.xcodeproj/project.pbxproj').is_file():raise SystemExit('REFUSED: not OpenMinis')
rev=subprocess.run(['git','rev-parse','HEAD'],cwd=repo,text=True,capture_output=True)
if rev.returncode or rev.stdout.strip()!=EXPECTED:raise SystemExit('REFUSED: wrong OpenMinis commit')
if not a.apply:
 print('DRY RUN: would add overlay/src/ios/CodexEngine to Minis target and add CodexSessionView sheet/toolbar entry.')
 print('Manual Xcode steps: add .h/.m/.c/.swift files, bridging header import, CodexRuntime resources, and link only Minis app target.')
 raise SystemExit(0)
raise SystemExit('REFUSED: automatic PBX/UI mutation is intentionally disabled; apply after Xcode review.')
