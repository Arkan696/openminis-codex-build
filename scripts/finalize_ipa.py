#!/usr/bin/env python3
"""Local-only finalization of a real Mac-built OpenMinis artifact for TrollStore.
No network. Never executes the app or Codex. Requires explicit authorized local use.
"""
from pathlib import Path, PurePosixPath
import argparse, hashlib, json, os, plistlib, shutil, stat, subprocess, tarfile, tempfile, zipfile
from overlay_lib import EXPECTED, SOURCE, FILES, Refused, digest, validate_runtime
from ipa_bundle import APP_ID, GROUP, inventory, regular_tree, executable_path, inspect_macho

CORE_ENT={'platform-application':True,'com.apple.private.security.no-sandbox':True}
BUNDLE_IDS={APP_ID,APP_ID+'.ShareExtension',APP_ID+'.FileProvider',APP_ID+'.AgentWidget'}
ALLOWED_ENT={'application-identifier','com.apple.security.application-groups',
 'com.apple.developer.healthkit','com.apple.developer.healthkit.access',
 'com.apple.developer.homekit','com.apple.developer.nfc.readersession.formats',*CORE_ENT}

def signing_policy(manifest):
    if manifest.get('upstream_commit')!=EXPECTED or manifest.get('bundle_id')!=APP_ID:
        raise Refused('Build identity/commit differs from reviewed plan')
    sign=manifest.get('signing_entitlements',{})
    if set(sign)!=BUNDLE_IDS:raise Refused('Unexpected app/extension signing list')
    for identifier,ent in sign.items():
        if not isinstance(ent,dict) or set(ent)-ALLOWED_ENT:raise Refused('Unreviewed entitlement requested')
        if ent.get('application-identifier')!=identifier or ent.get('com.apple.security.application-groups')!=[GROUP]:
            raise Refused('Signing identity or app group is not isolated')
        if identifier==APP_ID:
            if any(ent.get(k) is not v for k,v in CORE_ENT.items()):raise Refused('Main app spawn policy missing')
        elif any(k in ent for k in CORE_ENT):raise Refused('Extensions must not receive core private entitlements')
    return sign

def extract_artifact(artifact, dest):
    artifact=Path(artifact);manifest=json.loads((artifact/'BUILD-MANIFEST.json').read_text())
    if manifest.get('xcode_built') is not True or manifest.get('codex_core_included') is not False:
        raise Refused('Requires a successfully compiled, runtime-free Mac artifact')
    signing_policy(manifest)
    current={n:digest(SOURCE/n) for n in FILES}
    if manifest.get('overlay_sources_sha256')!=current:raise Refused('Mac artifact compiled a different overlay revision')
    archive=artifact/'OpenMinisCodex-unsigned-app.tar.gz'
    if archive.is_symlink() or digest(archive)!=manifest.get('archive_sha256'):raise Refused('Public artifact archive digest mismatch')
    count=0;total=0;seen=set()
    with tarfile.open(archive,'r|gz') as t:
        for member in t:
            p=PurePosixPath(member.name)
            if p.is_absolute() or '..' in p.parts or not p.parts or p.parts[0]!='Minis.app':raise Refused('Unsafe app archive path')
            if member.name in seen:raise Refused('Duplicate archive path')
            seen.add(member.name);count+=1;total+=member.size
            if count>100000 or total>4*1024**3 or member.size>800*1024**2:raise Refused('App archive exceeds extraction budget')
            out=dest.joinpath(*p.parts)
            if member.isdir():out.mkdir(parents=True,exist_ok=True);continue
            if not member.isfile():raise Refused('Links and special entries not permitted')
            out.parent.mkdir(parents=True,exist_ok=True)
            f=t.extractfile(member)
            if f is None:raise Refused('Truncated archive entry')
            with out.open('xb') as target:shutil.copyfileobj(f,target,1024*1024)
            out.chmod(0o755 if member.mode&0o111 else 0o644)
    app=dest/'Minis.app'
    actual={str(p.relative_to(app)):{'bytes':p.stat().st_size,'sha256':digest(p)} for p in regular_tree(app)}
    if actual!=manifest.get('app_files'):raise Refused('Extracted app does not match build file manifest')
    inventory(app)
    return app,manifest

def sign_bundle(app, manifest, ldid, work):
    sign=signing_policy(manifest)
    _,main,machos=inventory(app)
    bundles=[app,*app.glob('PlugIns/*.appex'),*app.rglob('*.framework')]
    bundle_exes={}
    for b in bundles:
        info,exe=executable_path(b)
        if b==app or b.suffix=='.appex':
            identifier=info.get('CFBundleIdentifier')
            if identifier not in sign:raise Refused('Unknown signed app/extension identity')
            ent=sign[identifier]
        else:ent={}
        bundle_exes[exe]=(b,ent)
    policies={}
    def sign_path(path,ent,shallow=False):
        entfile=work/('ent-'+hashlib.sha256(str(path).encode()).hexdigest()[:16]+'.plist')
        entfile.write_bytes(plistlib.dumps(ent))
        command=[str(ldid),'-S'+str(entfile),'-Cadhoc']
        if shallow:command.append('-w')
        command.append(str(path))
        subprocess.run(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True)
    # Sign every Mach-O with its own policy; never merge existing private entitlements.
    for exe,data in machos.items():
        rel=exe.relative_to(app)
        ent=bundle_exes[exe][1] if exe in bundle_exes else (CORE_ENT if rel.parts[0]=='CodexRuntime' else {})
        exe.chmod(0o755);sign_path(exe,ent);policies[str(rel)]=ent
    # Shallow seals preserve the different policies already applied to nested binaries.
    for b in sorted(bundles,key=lambda p:len(p.parts),reverse=True):
        _,exe=executable_path(b);sign_path(b,bundle_exes[exe][1],shallow=True)
        if not (b/'_CodeSignature/CodeResources').is_file():raise Refused('Signing did not generate bundle resource seal')
    _,_,signed=inventory(app,require_signature=True)
    for p,data in signed.items():
        if data['entitlements']!=policies[str(p.relative_to(app))]:raise Refused('Signed entitlement policy mismatch')
    return {str(p.relative_to(app)):{'sha256':digest(p),'entitlements':v['entitlements']} for p,v in signed.items()}


def write_ipa(app, destination, report):
    """Create Payload ZIP with explicit Unix modes; no overwrite of an existing IPA."""
    destination=Path(destination)
    if destination.exists() or destination.is_symlink():raise Refused('IPA output already exists')
    _,_,machos=inventory(app,require_signature=True)
    files=list(regular_tree(app))
    with zipfile.ZipFile(destination,'x',zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for p in files:
            name='Payload/'+app.name+'/'+p.relative_to(app).as_posix()
            item=zipfile.ZipInfo(name);item.create_system=3
            mode=0o755 if p in machos else (0o755 if p.stat().st_mode&0o111 else 0o644)
            item.external_attr=(stat.S_IFREG|mode)<<16;item.compress_type=zipfile.ZIP_DEFLATED
            with p.open('rb') as src,archive.open(item,'w',force_zip64=True) as out:
                shutil.copyfileobj(src,out,1024*1024)
    with zipfile.ZipFile(destination) as archive:
        if archive.testzip() is not None:raise Refused('IPA ZIP integrity failed')
        expected={'Payload/'+app.name+'/'+p.relative_to(app).as_posix() for p in files}
        if set(archive.namelist())!=expected:raise Refused('IPA member inventory mismatch')
        for p in machos:
            zi=archive.getinfo('Payload/'+app.name+'/'+p.relative_to(app).as_posix())
            if not (zi.external_attr>>16)&0o111:raise Refused('IPA executable mode lost')
        for p in files:
            name='Payload/'+app.name+'/'+p.relative_to(app).as_posix()
            with archive.open(name) as stored:
                if hashlib.file_digest(stored,'sha256').hexdigest()!=digest(p):
                    raise Refused('IPA member differs from signed app')
    report.update(ipa_bytes=destination.stat().st_size,ipa_sha256=digest(destination),
                  ipa_packaged=True,zip_members=len(files),codex_executed=False,
                  device_tested=False,coretrust_installation_tested=False,
                  signature_authenticated=False)
    return report


def finalize(artifact, runtime, ldid, out, authorized=False, execute=False, runtime_probe=True):
    if not authorized:raise Refused('Confirm that supplied runtime is authorized for local use')
    out=Path(out).absolute();runtime=Path(runtime).absolute();ldid=Path(ldid).absolute()
    if out.suffix.lower()!='.ipa':raise Refused('Output must be an .ipa file')
    for p in (out,Path(str(out)+'.json'),Path(str(out)+'.sha256')):
        if p.exists() or p.is_symlink():raise Refused('Output or evidence file already exists')
    with tempfile.TemporaryDirectory(prefix='ipa-verify-') as tmp:
        _,manifest=extract_artifact(artifact,Path(tmp))
        if runtime_probe and manifest.get('runtime_probe') is False:
            raise Refused('Runtime injection cannot be mixed with a no-codex-probe artifact')
        if not runtime_probe and manifest.get('runtime_probe') is not False:
            raise Refused('Cannot omit the runtime from a runtime-probe artifact')
    if runtime_probe:validated=validate_runtime(runtime)
    if not execute:
        with tempfile.TemporaryDirectory(prefix='ipa-verify-') as tmp:
            app,manifest=extract_artifact(artifact,Path(tmp))
            return {'plan_only':True,'ipa_packaged':False,'core_included':False,
                    'app_bundle':APP_ID,'runtime_files_checked':sorted(validated) if runtime_probe else [],
                    'runtime_probe':runtime_probe,'network_used':False,'xcode_built_here':False}
    if not ldid.is_file() or not os.access(ldid,os.X_OK):raise Refused('ldid is not an executable local tool')
    out.parent.mkdir(parents=True,exist_ok=True)
    # The shared iOS Files mount may report every file as 0644. Native /tmp
    # preserves executable modes required by runtime validation and signing.
    with tempfile.TemporaryDirectory(prefix='.codex-ipa-') as tmp:
        work=Path(tmp);app,manifest=extract_artifact(artifact,work)
        rt=app/'CodexRuntime'
        rt.mkdir(exist_ok=True)
        for name in ('codex','codex-code-mode-host'):
            if (rt/name).exists():raise Refused('Public artifact already contains a core')
        if runtime_probe:
            for name in validated:
                shutil.copyfile(runtime/name,rt/name)
                (rt/name).chmod(0o755 if name in ('codex','codex-code-mode-host') else 0o644)
            validate_runtime(rt)
        # Remove old bundle resource seals/profiles only in this temporary copy.
        for p in list(app.rglob('_CodeSignature')):
            if p.is_dir():shutil.rmtree(p)
        for p in app.rglob('embedded.mobileprovision'):p.unlink()
        signed=sign_bundle(app,manifest,ldid,work)
        report={'app_bundle':APP_ID,'upstream_commit':EXPECTED,
                'overlay_commit':manifest.get('overlay_commit'),
                'runtime_input_sha256':validated if runtime_probe else {},
                'runtime_probe':runtime_probe,'runtime_injected':runtime_probe,'signed_macho':signed,
                'xcode_built_here':False,'source_build_declared_by_artifact':True}
        candidate=work/'final.ipa';write_ipa(app,candidate,report)
        fd,staged=tempfile.mkstemp(prefix='.ipa-publish-',dir=out.parent)
        staged=Path(staged)
        try:
            with os.fdopen(fd,'wb') as target,candidate.open('rb') as src:
                shutil.copyfileobj(src,target,1024*1024)
            if digest(staged)!=report['ipa_sha256']:raise Refused('IPA copy digest mismatch')
            # Publish only on the destination filesystem, never overwrite an IPA.
            os.link(staged,out)
        finally:staged.unlink(missing_ok=True)
    Path(str(out)+'.sha256').write_text(report['ipa_sha256']+'  '+out.name+'\n')
    Path(str(out)+'.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    return report


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--artifact',type=Path,required=True,help='Mac build artifact directory, not a user app export')
    ap.add_argument('--runtime',type=Path,required=True,help='Locally prepared authorized CodexRuntime')
    ap.add_argument('--ldid',type=Path,default=Path('/opt/ios-toolchain/bin/ldid-ish'))
    ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--authorized',action='store_true',help='Confirm supplied core is authorized for your local use')
    ap.add_argument('--runtime-probe',dest='runtime_probe',action='store_true',default=True,help='Inject the locally supplied runtime (default)')
    ap.add_argument('--no-runtime-probe',dest='runtime_probe',action='store_false',help='No-codex-probe diagnostic: inject no runtime')
    ap.add_argument('--execute',action='store_true',help='Actually sign and package; otherwise validate inputs only')
    a=ap.parse_args(argv)
    try:report=finalize(a.artifact,a.runtime,a.ldid,a.out,a.authorized,a.execute,a.runtime_probe)
    except (Refused,OSError,ValueError,subprocess.SubprocessError,tarfile.TarError) as e:
        ap.exit(1,'REFUSED: '+str(e)+'\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0

if __name__=='__main__':raise SystemExit(main())
