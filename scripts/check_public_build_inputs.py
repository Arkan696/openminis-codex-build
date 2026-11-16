#!/usr/bin/env python3
"""Verify the public-only upload allowlist; never reads environment secret values."""
from pathlib import Path
import argparse, hashlib, json, subprocess
from overlay_lib import ROOT, Refused
MANIFEST='PUBLIC-BUILD-INPUTS.json'
ALLOWED={'.py','.sh','.yml','.yaml','.swift','.m','.c','.h','.md','.json','.toml','.txt','.patch'}

def check(root=ROOT):
    root=Path(root);m=json.loads((root/MANIFEST).read_text())
    files=subprocess.check_output(['git','ls-files','-z'],cwd=root).decode().split('\0')
    files=set(filter(None,files))
    if files!=set(m['files'])|{MANIFEST}:raise Refused('Tracked files differ from reviewed public upload list')
    for name,h in m['files'].items():
        p=root/name
        if not p.resolve().is_relative_to(root.resolve()) or p.is_symlink() or not p.is_file():raise Refused('Nonregular public input: '+name)
        if p.suffix not in ALLOWED and name not in ('LICENSE','.gitignore'):raise Refused('Not a public source file: '+name)
        if 'CodexRuntime' in p.parts or p.stat().st_size>2*1024*1024:raise Refused('Runtime or large artifact cannot enter public build')
        data=p.read_bytes()
        if b'\x00' in data or hashlib.sha256(data).hexdigest()!=h:raise Refused('Public source digest mismatch: '+name)
    return len(m['files'])
if __name__=='__main__':
    try:print('PUBLIC_SOURCE_INPUTS_VERIFIED',check())
    except (OSError,ValueError,subprocess.SubprocessError) as e:raise SystemExit('REFUSED: '+str(e))
