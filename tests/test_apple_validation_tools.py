"""Local generator/runner tests ONLY. Does not run Swift, Xcode, XCTest or Codex."""
from pathlib import Path
import contextlib
import hashlib
import io
import json
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from xml.etree import ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from openstep_parser import OpenStepDecoder
import apple_validation_project as project
import verify_apple as runner
from overlay_lib import FILES, COMPILED, Refused


def decoded(text):
    return OpenStepDecoder.ParseFromFile(io.StringIO(text))


class AppleToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='apple-generator-fixture-')
        self.root=Path(self.tmp.name)
    def tearDown(self):
        self.tmp.cleanup()

    def test_generated_pbx_object_graph_and_target_dependencies(self):
        original=project.project_text();self.assertEqual(original,project.project_text())
        data=decoded(original);objects=data['objects'];root=objects[data['rootObject']]
        self.assertEqual(len(root['targets']),2)
        self.assertTrue(all(re.fullmatch('[A-F0-9]{24}',key) for key in objects))
        app=objects[project.uid('target/app')];tests=objects[project.uid('target/tests')]
        self.assertEqual(app['productType'],'com.apple.product-type.application')
        self.assertEqual(tests['productType'],'com.apple.product-type.bundle.unit-test')
        dependency=objects[tests['dependencies'][0]]
        self.assertEqual(dependency['target'],project.uid('target/app'))
        for target in (app,tests):
            for phase in target['buildPhases']:self.assertIn(phase,objects)
            configurations=objects[target['buildConfigurationList']]['buildConfigurations']
            for configuration in configurations:
                settings=objects[configuration]['buildSettings']
                self.assertEqual(settings['SWIFT_VERSION'],'5.0')
                self.assertEqual(settings['IPHONEOS_DEPLOYMENT_TARGET'],'16.5')
                self.assertEqual(settings['CODE_SIGNING_ALLOWED'],'NO')
                self.assertNotIn('CODE_SIGN_ENTITLEMENTS',settings)
        phase=objects[project.uid('phase/app-sources')]
        paths={objects[objects[i]['fileRef']]['path'] for i in phase['files']}
        self.assertEqual(paths,set(COMPILED)|{'ValidationMain.m'})
        self.assertFalse(any(name.endswith('.h') for name in paths))
        test_settings=objects[objects[tests['buildConfigurationList']]['buildConfigurations'][0]]['buildSettings']
        self.assertIn(project.MODULE+'.app/'+project.MODULE,test_settings['TEST_HOST'])
        framework_paths={o.get('path') for o in objects.values() if o.get('lastKnownFileType')=='wrapper.framework'}
        for name in ('UIKit','Security','SafariServices','Foundation'):
            self.assertIn('System/Library/Frameworks/'+name+'.framework',framework_paths)
        for resources in ('phase/app-resources','phase/test-resources'):
            self.assertEqual(objects[project.uid(resources)]['files'],[])

    def test_generated_scheme_tests_one_fixture_target(self):
        root=ET.fromstring(project.scheme_xml())
        tests=root.findall('./TestAction/Testables/TestableReference')
        self.assertEqual(len(tests),1)
        self.assertEqual(tests[0].get('skipped'),'NO')
        ref=tests[0].find('BuildableReference')
        self.assertEqual(ref.get('BlueprintIdentifier'),project.uid('target/tests'))
        self.assertEqual(ref.get('ReferencedContainer'),'container:'+project.MODULE+'.xcodeproj')
        self.assertFalse(root.findall('.//EnvironmentVariables'))

    def test_generator_copies_actual_sources_not_stubs_or_runtime(self):
        before={name:(project.SOURCE/name).read_bytes() for name in FILES}
        out=self.root/'generated';project.generate(out)
        for name,data in before.items():
            self.assertEqual((out/'Sources'/name).read_bytes(),data)
            self.assertEqual((project.SOURCE/name).read_bytes(),data)
        self.assertEqual((out/'Tests/CodexSessionEngineTests.swift').read_text(),
                         '@testable import '+project.MODULE+'\n'+project.FIXTURE.read_text())
        manifest=json.loads((out/'VALIDATION-PROJECT.json').read_text())
        self.assertFalse(manifest['apple_build_performed'])
        self.assertFalse(manifest['xctest_performed'])
        self.assertFalse(manifest['core_runtime_included'])
        for name,data in before.items():
            self.assertEqual(manifest['production_sources_sha256'][name],hashlib.sha256(data).hexdigest())
        self.assertFalse(list(out.rglob('CodexRuntime')))
        self.assertFalse(list(out.rglob('*.entitlements')))
        host=(out/'Sources/ValidationMain.m').read_text()
        self.assertNotIn('startInBundle:',host)
        self.assertNotIn('CodexSessionView()',host)
        with self.assertRaises(Refused):project.generate(out)

    def test_default_dry_run_does_not_call_apple_or_create_output(self):
        out=self.root/'plan'
        with mock.patch.object(runner.subprocess,'run',side_effect=AssertionError('Apple command must not run')):
            with contextlib.redirect_stdout(io.StringIO()) as log:
                self.assertEqual(runner.main(['--out',str(out)]),0)
        response=json.loads(log.getvalue())
        self.assertTrue(response['plan_only']);self.assertFalse(response['xcode_invoked'])
        self.assertFalse(out.exists())
        command=response['commands'][0]
        self.assertIn('build-for-testing',command)
        self.assertIn('generic/platform=iOS Simulator',command)
        self.assertIn('CODE_SIGNING_ALLOWED=NO',command)

    def test_execute_on_linux_refuses_without_creating_output(self):
        out=self.root/'refused'
        with mock.patch.object(runner.sys,'platform','linux'):
            with mock.patch.object(runner.subprocess,'run',side_effect=AssertionError('No commands expected')):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(runner.main(['--out',str(out),'--execute']),1)
        self.assertFalse(out.exists())

    def test_xctest_requires_explicit_booted_ios_simulator(self):
        with self.assertRaises(Refused):runner.commands(self.root/'out','test')
        payload={'devices':{'com.apple.CoreSimulator.SimRuntime.iOS-18-0':[
            {'udid':'FIXTURE-UDID','state':'Booted','isAvailable':True,'name':'Local fixture'}]}}
        def read_only(cmd,**kwargs):
            self.assertEqual(cmd,['xcrun','simctl','list','devices','booted','--json'])
            return subprocess.CompletedProcess(cmd,0,json.dumps(payload),'')
        value=runner.simulator_preflight('FIXTURE-UDID',read_only)
        self.assertEqual(value['state'],'Booted')
        with self.assertRaises(Refused):runner.simulator_preflight('DIFFERENT',read_only)
        payload['devices']={'com.apple.CoreSimulator.SimRuntime.tvOS-18-0':[
            {'udid':'FIXTURE-UDID','state':'Booted','isAvailable':True}]}
        with self.assertRaises(Refused):runner.simulator_preflight('FIXTURE-UDID',read_only)

    def test_build_failure_never_records_success_or_loses_error_log(self):
        out=self.root/'failure'
        plan=runner.commands(out,'build')
        def fake_failure(cmd,**kwargs):
            kwargs['stdout'].write(b'SYNTHETIC script-test failure, not a Swift compiler invocation\n')
            return subprocess.CompletedProcess(cmd,65)
        with mock.patch.object(runner.subprocess,'run',side_effect=fake_failure):
            self.assertEqual(runner.execute(out,'build',None,plan,{'script_test_fixture':True}),1)
        result=json.loads((out/'APPLE-VERIFICATION.json').read_text())
        self.assertEqual(result['status'],'failed');self.assertEqual(result['last_returncode'],65)
        for key in ('swift_sources_compiled','swiftui_sources_compiled','native_host_compiled',
                    'xctest_executed','xctest_passed','codex_executed','full_openminis_built','tipa_built'):
            self.assertFalse(result[key],key)
        self.assertTrue((out/result['failure_log']).is_file())

    def test_zero_or_partial_xctest_result_cannot_be_passed(self):
        summary={'result':'Passed','totalTestCount':12,'passedTests':12,'failedTests':0}
        self.assertEqual(runner.verify_test_summary(summary,12)['passed'],12)
        for altered in ({'totalTestCount':0,'passedTests':0}, {'passedTests':11},
                        {'failedTests':1}, {'result':'Failed'}, {'totalTestCount':'12'}):
            with self.assertRaises(Refused):runner.verify_test_summary({**summary,**altered},12)

    def test_xcode_success_without_xcresult_is_not_xctest_success(self):
        out=self.root/'missing-result';plan=runner.commands(out,'test','FIXTURE-UDID')
        with mock.patch.object(runner.subprocess,'run',return_value=subprocess.CompletedProcess([],0)):
            with self.assertRaises(Refused):
                runner.execute(out,'test','FIXTURE-UDID',plan,{'script_test_fixture':True})
        result=json.loads((out/'APPLE-VERIFICATION.json').read_text())
        self.assertEqual(result['status'],'failed');self.assertFalse(result['xctest_passed'])
        self.assertFalse(result['xctest_executed'])

if __name__=='__main__':
    unittest.main(verbosity=2)
