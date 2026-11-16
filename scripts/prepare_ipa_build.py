#!/usr/bin/env python3
"""Rebrand ONLY a disposable, overlay-applied OpenMinis build checkout. No network."""
from pathlib import Path
import argparse, hashlib, json, plistlib, subprocess
from overlay_lib import EXPECTED, Refused, FILES
from disable_unprovisioned_sync import proposed_changes as proposed_sync_changes

APP_ID='local.openminis.codex'
GROUP='group.'+APP_ID
SCHEME='miniscodex'
TEXT_SUFFIXES={'.swift','.m','.mm','.h','.plist','.entitlements','.pbxproj','.xcconfig','.strings','.json','.js','.html','.css','.md','.txt'}

def prepare(repo):
    repo=Path(repo).resolve();ios=repo/'src/ios'
    rev=subprocess.run(['git','rev-parse','HEAD'],cwd=repo,capture_output=True,text=True,check=True).stdout.strip()
    if rev!=EXPECTED:raise Refused('Wrong upstream commit')
    marker=repo/'CODEX-IPA-BUILD-PREPARED.json'
    if marker.exists():raise Refused('Build checkout was already prepared')
    for name in FILES:
        if not (ios/'CodexEngine'/name).is_file():raise Refused('Apply the reviewed overlay first: '+name)
    for name in ('codex','codex-code-mode-host','auth.json'):
        p=ios/'CodexEngine/CodexRuntime'/name
        if p.exists() or p.is_symlink():raise Refused('Public build checkout must not contain user runtime or credentials')
    required=('Info.plist','Minis.entitlements','ShareExtension/ShareExtension.entitlements',
              'FileProvider/FileProvider.entitlements','AgentWidget/AgentWidget.entitlements',
              'Configs/ProviderCustomization.xcconfig.example')
    for name in required:
        p=ios/name
        if p.is_symlink() or not p.is_file():raise Refused('Build source missing or linked: '+name)
    # Parse entitlements before writing any source, so a malformed input fails early.
    for name in required:
        if name.endswith(('.entitlements','.plist')):plistlib.loads((ios/name).read_bytes())
    # Validate all four fixed sync sources and anchors before any source writes.
    sync_changes=proposed_sync_changes(ios)
    changes=[]
    # Rebrand application IDs and keychain service literals, never copy a live app's data.
    for p in sorted(ios.rglob('*')):
        if not p.is_file() or p.is_symlink() or p.suffix not in TEXT_SUFFIXES:continue
        try:s=p.read_text()
        except UnicodeDecodeError:continue
        rel=p.relative_to(ios).as_posix()
        t=sync_changes.get(rel,s).replace('com.openminis.app',APP_ID)
        # External launch schemes must not be registered by both installed apps.
        # minis:// remains a resource protocol inside WebViews; only external
        # registration and share-extension launch are redirected to the test app.
        if p==ios/'Shared/DeepLinkRouter.swift':
            t=t.replace('guard url.scheme == "minis", let host',
                        'guard (url.scheme == "minis" || url.scheme == "miniscodex"), let host')
        if p==ios/'ShareExtension/ShareViewController.swift':
            t=t.replace('URL(string: "minis://share")','URL(string: "miniscodex://share")')
        if t!=s:
            p.write_text(t);changes.append(str(p.relative_to(repo)))
    info=ios/'Info.plist';d=plistlib.loads(info.read_bytes())
    for url in d.get('CFBundleURLTypes',[]):
        url['CFBundleURLSchemes']=[SCHEME if x=='minis' else 'miniscodex-mcp' if x=='minis-mcp' else x for x in url.get('CFBundleURLSchemes',[])]
    d['CFBundleDisplayName']='OpenMinis Codex'
    info.write_bytes(plistlib.dumps(d))
    sign={}
    for rel,bundle in [('Minis.entitlements',APP_ID),('ShareExtension/ShareExtension.entitlements',APP_ID+'.ShareExtension'),('FileProvider/FileProvider.entitlements',APP_ID+'.FileProvider'),('AgentWidget/AgentWidget.entitlements',APP_ID+'.AgentWidget')]:
        ent=plistlib.loads((ios/rel).read_bytes())
        ent['application-identifier']=bundle
        ent.pop('get-task-allow',None)
        # Push/iCloud/WeatherKit use an Apple provisioning identity, not TrollStore's
        # ad-hoc test identity. They are deliberately not granted here.
        for key in list(ent):
            if key=='aps-environment' or 'icloud' in key or 'ubiquity' in key or key=='com.apple.developer.weatherkit':del ent[key]
        if bundle==APP_ID:
            ent.update({'platform-application':True,'com.apple.private.security.no-sandbox':True})
        if ent.get('com.apple.security.application-groups')!=[GROUP]:raise Refused('Unexpected app group')
        (ios/rel).write_bytes(plistlib.dumps(ent))
        sign[bundle]=ent
    config=ios/'Configs/ProviderCustomization.xcconfig'
    if not config.exists():config.write_bytes((config.with_suffix('.xcconfig.example')).read_bytes())
    manifest={'schema':1,'upstream_commit':EXPECTED,'bundle_id':APP_ID,'app_group':GROUP,
              'external_scheme':SCHEME,'codex_core_included':False,'xcode_built':False,
              'unprovisioned_icloud_sync_disabled_in_source':True,
              'signing_entitlements':sign,'modified_paths':changes,
              'limitations':['iCloud/push/WeatherKit are not provisioned for this independent TrollStore identity','Source-level namespace replacement still requires full build and device validation']}
    marker.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    return manifest

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--repo',required=True,type=Path)
    ap.add_argument('--disposable-copy',action='store_true',help='Acknowledge that this is a NEW, disposable build checkout')
    a=ap.parse_args()
    if not a.disposable_copy:ap.exit(1,'REFUSED: use --disposable-copy only on a separate build checkout\n')
    try:d=prepare(a.repo)
    except (Refused,OSError,subprocess.SubprocessError) as e:ap.exit(1,'REFUSED: '+str(e)+'\n')
    print('Prepared disposable source checkout:',d['bundle_id'],'group:',d['app_group'])
    print('No Xcode build, signing, installation or core execution was performed.')
if __name__=='__main__':main()
