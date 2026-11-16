#!/usr/bin/env python3
"""Stage a real unsigned iPhone build, WITHOUT user Codex, preserving modes in tar."""
from pathlib import Path
import argparse, hashlib, json, shutil, subprocess, tarfile, tempfile, os
from overlay_lib import ROOT, EXPECTED, Refused, FILES, SOURCE, digest
from ipa_bundle import inventory, regular_tree

def contains_overlay_host(path, marker=b'OMCodexProcessHost'):
    # Scan in bounded chunks; do not load a large compiled Swift binary into RAM.
    tail=b''
    with Path(path).open('rb') as f:
        while chunk:=f.read(1024*1024):
            data=tail+chunk
            if marker in data:return True
            tail=data[-len(marker):]
    return False


def stage(app,prepared,out):
    app=Path(app);out=Path(out)
    if out.exists() or out.is_symlink():raise Refused('Artifact output already exists')
    manifest=json.loads(Path(prepared).read_text())
    if manifest.get('upstream_commit')!=EXPECTED or manifest.get('codex_core_included') is not False:raise Refused('Wrong build preparation manifest')
    info,exe,machos=inventory(app)
    if (app/'CodexRuntime/codex').exists() or (app/'CodexRuntime/codex-code-mode-host').exists():raise Refused('User runtime may not be included in a public artifact')
    # Confirm that the linked application contains the overlay's classes/selectors,
    # in addition to the Xcode archive log; this is not a runtime test.
    if not contains_overlay_host(exe):raise Refused('Compiled application lacks the native Codex host')
    if manifest.get('provider_system_paste_controls_enabled'):
        if info.get('CFBundleVersion')!='2':raise Refused('Expected native paste repair build2')
        if not contains_overlay_host(exe,b'OMProviderPaste'):raise Refused('Compiled application lacks the native provider paste repair')
    for p in regular_tree(app):
        if p.name=='auth.json' or p.suffix in ('.key','.p12','.minisbak'):raise Refused('Unexpected sensitive artifact')
    report={str(p.relative_to(app)):{'bytes':p.stat().st_size,'sha256':digest(p)} for p in regular_tree(app)}
    out.parent.mkdir(parents=True,exist_ok=True)
    tmp=Path(tempfile.mkdtemp(prefix='.public-artifact-',dir=out.parent))
    try:
        with tarfile.open(tmp/'OpenMinisCodex-unsigned-app.tar.gz','w:gz') as t:t.add(app,arcname='Minis.app',recursive=True)
        manifest.update(xcode_built=True,codex_core_included=False,ipa_built=False,device_tested=False,
                        app_files=report,app_name='Minis.app',main_executable=info['CFBundleExecutable'],
                        overlay_sources_sha256={n:hashlib.sha256((SOURCE/n).read_bytes()).hexdigest() for n in FILES},
                        overlay_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip())
        manifest['archive_sha256']=hashlib.sha256((tmp/'OpenMinisCodex-unsigned-app.tar.gz').read_bytes()).hexdigest()
        (tmp/'BUILD-MANIFEST.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
        os.rename(tmp,out)
    finally:
        if tmp.exists():shutil.rmtree(tmp)
    return manifest
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--app',type=Path,required=True);p.add_argument('--prepared',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    try:d=stage(a.app,a.prepared,a.out)
    except (Refused,OSError,ValueError,subprocess.SubprocessError) as e:p.exit(1,'REFUSED: '+str(e)+'\n')
    print('STAGED_REAL_UNSIGNED_APP',len(d['app_files']),'files; Codex runtime absent; IPA not built')
