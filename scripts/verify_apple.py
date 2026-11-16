#!/usr/bin/env python3
"""Mock-only Apple validation. Default prints plan; --execute requires macOS/Xcode.

No remote service, runtime, signing key, device install or paid request. Tests
require an ALREADY BOOTED iOS Simulator UDID explicitly supplied by the caller.
The tool never creates/boots/shuts down a simulator or selects a physical phone.
"""
from pathlib import Path
import argparse
import datetime
import hashlib
import json
import re
import shutil
import subprocess
import sys
from apple_validation_project import generate, MODULE, SOURCE, FILES, FIXTURE
from overlay_lib import ROOT, Refused


def commands(work, mode, simulator_id=None):
    work=Path(work).absolute()
    base=['xcodebuild','-project',str(work/(MODULE+'.xcodeproj')),
          '-scheme',MODULE,'-configuration','Debug','-derivedDataPath',str(work/'DerivedData'),
          'CODE_SIGNING_ALLOWED=NO','CODE_SIGNING_REQUIRED=NO',
          'IPHONEOS_DEPLOYMENT_TARGET=16.5','SWIFT_VERSION=5.0']
    if mode=='build':
        return [base+['-destination','generic/platform=iOS Simulator','build-for-testing']]
    if not simulator_id:
        raise Refused('XCTest requires --simulator-id for an already booted iOS simulator')
    return [base+['-destination','platform=iOS Simulator,id='+simulator_id,
                 '-parallel-testing-enabled','NO','-resultBundlePath',str(work/'MockTests.xcresult'),'test']]


def simulator_preflight(udid, run):
    result=run(['xcrun','simctl','list','devices','booted','--json'],capture_output=True,text=True,check=True)
    devices=json.loads(result.stdout).get('devices',{})
    matches=[(runtime,d) for runtime,items in devices.items() for d in items if d.get('udid')==udid]
    if len(matches)!=1:
        raise Refused('Simulator must already be booted; tool does not boot/create it')
    runtime,device=matches[0]
    if '.iOS-' not in runtime or device.get('state')!='Booted' or device.get('isAvailable') is not True:
        raise Refused('Expected an available, booted iOS Simulator, not a device/tvOS/watchOS')
    return {'runtime':runtime,'udid':udid,'name':device.get('name'),'state':device.get('state')}


def preflight(mode, udid):
    if sys.platform!='darwin':
        raise Refused('Real Apple validation requires macOS with full Xcode; no check performed')
    for name in ('xcodebuild','xcrun'):
        if not shutil.which(name):raise Refused('Missing Apple command: '+name)
    versions={}
    for label,cmd in [('xcode',['xcodebuild','-version']),('swift',['xcrun','swiftc','--version'])]:
        result=subprocess.run(cmd,capture_output=True,text=True,check=True)
        versions[label]=result.stdout.strip()
    if mode=='test':versions['simulator']=simulator_preflight(udid,subprocess.run)
    return versions


def source_digests():
    result={str((SOURCE/name).relative_to(ROOT)):hashlib.sha256((SOURCE/name).read_bytes()).hexdigest() for name in FILES}
    result[str(FIXTURE.relative_to(ROOT))]=hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    return result


def verify_test_summary(summary, expected_count):
    if not isinstance(summary,dict):raise Refused('Invalid XCTest result summary')
    total=summary.get('totalTestCount');failed=summary.get('failedTests');passed=summary.get('passedTests')
    if not all(type(v) is int for v in (total,failed,passed)):
        raise Refused('XCTest result is missing numeric test counts')
    if total!=expected_count or passed!=expected_count or failed!=0 or summary.get('result')!='Passed':
        raise Refused('Not all expected mock XCTest cases ran and passed')
    return {'total':total,'passed':passed,'failed':failed,'expected':expected_count}


def compiled_artifacts(work):
    products=work/'DerivedData/Build/Products/Debug-iphonesimulator'
    app=products/(MODULE+'.app')/MODULE
    tests=products/'CodexOverlayTests.xctest/CodexOverlayTests'
    if not tests.is_file():tests=app.parent/'PlugIns/CodexOverlayTests.xctest/CodexOverlayTests'
    module=products/(MODULE+'.swiftmodule')
    if not app.is_file() or not tests.is_file() or app.stat().st_size==0 or tests.stat().st_size==0:
        raise Refused('Xcode reported success without both compiled app and XCTest products')
    modules=list(module.glob('*.swiftmodule')) if module.is_dir() else []
    if not modules or any(not p.is_file() or p.stat().st_size==0 for p in modules):
        raise Refused('Xcode reported success without a compiled production Swift module')
    return {str(p.relative_to(work)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [app,tests,*modules]}


def execute(work, mode, simulator_id, run_commands, versions):
    """Write false flags first. Any failure preserves a factual failure report."""
    generate(work)
    digests=source_digests()
    report={'generated_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'mode':mode,'mock_only':True,'production_sources_sha256':digests,
            'tool_versions':versions,'commands':run_commands,
            'status':'running','apple_target':'iOS-Simulator','physical_device_tested':False,
            'swift_sources_compiled':False,'native_host_compiled':False,'swiftui_sources_compiled':False,
            'xctest_compiled':False,'xctest_executed':False,'xctest_passed':False,
            'codex_executed':False,'real_oauth_tested':False,'full_openminis_built':False,
            'tipa_built':False}
    destination=work/'APPLE-VERIFICATION.json'
    def save():destination.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    save()
    try:
        for i,command in enumerate(run_commands):
            log=work/('xcode-'+str(i+1)+'.log')
            with log.open('wb') as output:
                result=subprocess.run(command,cwd=work,stdout=output,stderr=subprocess.STDOUT)
            report['last_returncode']=result.returncode
            if result.returncode:
                report['status']='failed';report['failure_log']=log.name;save()
                return 1
        if source_digests()!=digests:
            raise Refused('Overlay source changed during validation; results cannot cover current files')
        # Product presence plus xcodebuild result establishes source compilation,
        # not UI/core behavior.
        report['compiled_products']=compiled_artifacts(work)
        report.update(status='passed',swift_sources_compiled=True,native_host_compiled=True,
                      swiftui_sources_compiled=True,xctest_compiled=True)
        if mode=='test':
            result=work/'MockTests.xcresult'
            if not result.is_dir():raise Refused('Xcode returned success without the requested test result bundle')
            summary_result=subprocess.run(['xcrun','xcresulttool','get','test-results','summary','--path',str(result)],capture_output=True,text=True,check=True)
            summary=json.loads(summary_result.stdout)
            expected=len(re.findall(r'^\s*func test\w+\s*\(',FIXTURE.read_text(),re.M))
            if not expected:raise Refused('Mock fixture defines no test methods')
            report['xctest_counts']=verify_test_summary(summary,expected)
            report.update(xctest_executed=True,xctest_passed=True,result_bundle='MockTests.xcresult')
        save();return 0
    except BaseException as error:
        report['status']='failed';report['failure_type']=type(error).__name__;save()
        raise


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True,help='NEW isolated work directory; existing output is refused')
    parser.add_argument('--mode',choices=['build','test'],default='build')
    parser.add_argument('--simulator-id',help='Required for test; existing BOOTED simulator only')
    parser.add_argument('--execute',action='store_true',help='Run local Xcode; otherwise print plan without creating output')
    args=parser.parse_args(argv)
    work=args.out.absolute()
    try:
        plan=commands(work,args.mode,args.simulator_id)
        if work.exists() or work.is_symlink():raise Refused('Output exists; previous test evidence will not be overwritten')
        if not args.execute:
            print(json.dumps({'plan_only':True,'files_created':False,'xcode_invoked':False,
                              'runtime_included':False,'commands':plan},ensure_ascii=False,indent=2))
            return 0
        versions=preflight(args.mode,args.simulator_id)
        code=execute(work,args.mode,args.simulator_id,plan,versions)
        print('Apple validation:', 'PASSED' if code==0 else 'FAILED','report:',work/'APPLE-VERIFICATION.json')
        print('Mock/compiler checks only. Full OpenMinis, OAuth, device signing and Codex runtime remain untested.')
        return code
    except (Refused,OSError,ValueError,subprocess.SubprocessError) as error:
        print('REFUSED:',str(error));return 1

if __name__=='__main__':
    raise SystemExit(main())
