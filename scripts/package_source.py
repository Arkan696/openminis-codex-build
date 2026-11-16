#!/usr/bin/env python3
"""Package ONLY tracked source, patches and reports. Never an IPA/runtime bundle."""
from pathlib import Path
import hashlib
import json
import subprocess
import zipfile
from overlay_lib import ROOT, FILES


def main():
    status=subprocess.run(['git','status','--porcelain','--untracked-files=all'],cwd=ROOT,text=True,capture_output=True,check=True).stdout
    if status.strip():
        raise SystemExit('REFUSED: commit source/report changes before producing an archive')
    revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    report=json.loads((ROOT/'VERIFICATION.json').read_text())
    for name, expected in report['checked_source_sha256'].items():
        if hashlib.sha256((ROOT/name).read_bytes()).hexdigest()!=expected:
            raise SystemExit('REFUSED: source differs from verified digest: '+name)
    if any(report[k] for k in ('runtime_executed','swift_typechecked','xctest_executed','app_or_tipa_built','ios_device_tested')):
        raise SystemExit('REFUSED: source-only packaging contract changed; review the report')
    files=subprocess.check_output(['git','ls-files','-z'],cwd=ROOT).decode().split('\0')
    selected=[]
    for name in files:
        if not name:continue
        p=ROOT/name
        if p.is_symlink() or not p.is_file():
            raise SystemExit('REFUSED: non-regular tracked source: '+name)
        if p.suffix.lower() not in ('.py','.swift','.c','.h','.m','.md','.toml','.patch','.json','.txt') and name not in ('LICENSE','.gitignore'):
            raise SystemExit('REFUSED: unexpected tracked artifact: '+name)
        if p.stat().st_size>2*1024*1024 or 'CodexRuntime' in p.parts:
            raise SystemExit('REFUSED: runtime/large artifact cannot enter source archive')
        selected.append(name)
    manifest={'source_only':True,'app_or_tipa':False,'overlay_commit':revision,'files':{}}
    for name in sorted(selected):
        p=ROOT/name
        manifest['files'][name]={'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
    out=ROOT/'dist/OpenMinis-Codex-overlay-source-v0.1.zip'
    out.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
        for name in sorted(selected):
            item=zipfile.ZipInfo('openminis-codex-port/'+name,date_time=(2026,11,16,0,0,0))
            item.create_system=3;item.external_attr=(0o100644<<16);item.compress_type=zipfile.ZIP_DEFLATED
            archive.writestr(item,(ROOT/name).read_bytes())
        archive.writestr('openminis-codex-port/SOURCE-MANIFEST.json',json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    with zipfile.ZipFile(out) as archive:
        if archive.testzip() is not None:
            raise SystemExit('Archive integrity failure')
        assert len(archive.namelist())==len(selected)+1
    digest=hashlib.sha256(out.read_bytes()).hexdigest()
    (out.with_suffix('.zip.sha256')).write_text(digest+'  '+out.name+'\n')
    print(json.dumps({'artifact':str(out),'bytes':out.stat().st_size,'sha256':digest,'source_files':len(selected),'overlay_commit':revision,'source_only':True},indent=2))

if __name__=='__main__':
    main()
