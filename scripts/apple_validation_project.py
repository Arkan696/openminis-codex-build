"""Generate a local mock-only Xcode host/test project from REAL overlay sources.
No CodexRuntime, entitlements, provider settings, credentials or network actions.
"""
from pathlib import Path
import hashlib
import json
import os
import shutil
import tempfile
from xml.etree import ElementTree as ET
from overlay_lib import ROOT, SOURCE, FILES, COMPILED, Refused

MODULE = 'CodexOverlayValidation'
TEST_TARGET = 'CodexOverlayTests'
FIXTURE = ROOT/'tests/swift-fixture/CodexSessionEngineTests.swift'
HOST = r'''// Generated mock-only XCTest host. Never connects to Codex.
#import <UIKit/UIKit.h>
@interface OMValidationDelegate : UIResponder <UIApplicationDelegate>
@property(nonatomic,strong) UIWindow *window;
@end
@implementation OMValidationDelegate
- (BOOL)application:(UIApplication *)application didFinishLaunchingWithOptions:(NSDictionary *)options {
    self.window=[[UIWindow alloc] initWithFrame:UIScreen.mainScreen.bounds];
    UIViewController *controller=[UIViewController new];
    controller.view.backgroundColor=UIColor.systemBackgroundColor;
    UILabel *label=[UILabel new];label.translatesAutoresizingMaskIntoConstraints=NO;
    label.numberOfLines=0;label.textAlignment=NSTextAlignmentCenter;
    label.text=@"Codex overlay: mock tests only\nNo runtime or account loaded";
    [controller.view addSubview:label];
    [NSLayoutConstraint activateConstraints:@[
        [label.centerXAnchor constraintEqualToAnchor:controller.view.centerXAnchor],
        [label.centerYAnchor constraintEqualToAnchor:controller.view.centerYAnchor],
        [label.widthAnchor constraintLessThanOrEqualToAnchor:controller.view.widthAnchor constant:-40]
    ]];
    self.window.rootViewController=controller;[self.window makeKeyAndVisible];return YES;
}
@end
int main(int argc,char **argv){@autoreleasepool{return UIApplicationMain(argc,argv,nil,NSStringFromClass(OMValidationDelegate.class));}}
'''


def uid(label):
    return hashlib.sha256(('CodexOverlayValidation/v1/'+label).encode()).hexdigest()[:24].upper()


def encode(value, level=0):
    """OpenStep text serializer for generated values; no mutation of upstream PBX."""
    tab='\t'*level
    if isinstance(value,dict):
        lines=[tab+'\t'+json.dumps(str(k))+ ' = '+encode(v,level+1)+';' for k,v in value.items()]
        return '{\n'+'\n'.join(lines)+'\n'+tab+'}'
    if isinstance(value,(list,tuple)):
        return '(\n'+''.join(tab+'\t'+encode(v,level+1)+',\n' for v in value)+tab+')'
    return json.dumps(str(value),ensure_ascii=False)


def project_text():
    objects={}
    def add(label,isa,**attributes):
        key=uid(label)
        if key in objects:raise Refused('Generated validation-project ID collision')
        objects[key]={'isa':isa,**attributes}
        return key
    sources=[];refs=[]
    for name,kind in FILES.items():
        ref=add('ref/'+name,'PBXFileReference',lastKnownFileType=kind,path=name,sourceTree='<group>')
        refs.append(ref)
        if name in COMPILED:
            sources.append(add('source/'+name,'PBXBuildFile',fileRef=ref))
    host=add('ref/ValidationMain.m','PBXFileReference',lastKnownFileType='sourcecode.c.objc',path='ValidationMain.m',sourceTree='<group>')
    refs.append(host);sources.append(add('source/ValidationMain.m','PBXBuildFile',fileRef=host))
    source_group=add('group/sources','PBXGroup',children=refs,path='Sources',sourceTree='<group>')
    test_ref=add('ref/tests','PBXFileReference',lastKnownFileType='sourcecode.swift',path='CodexSessionEngineTests.swift',sourceTree='<group>')
    test_group=add('group/tests','PBXGroup',children=[test_ref],path='Tests',sourceTree='<group>')
    test_build=add('source/tests','PBXBuildFile',fileRef=test_ref)
    framework_refs=[];framework_builds=[]
    for name in ('UIKit','Foundation','Security','SafariServices'):
        ref=add('framework/'+name,'PBXFileReference',lastKnownFileType='wrapper.framework',name=name+'.framework',path='System/Library/Frameworks/'+name+'.framework',sourceTree='SDKROOT')
        framework_refs.append(ref);framework_builds.append(add('link/'+name,'PBXBuildFile',fileRef=ref))
    frameworks=add('group/frameworks','PBXGroup',children=framework_refs,name='Frameworks',sourceTree='<group>')
    app_product=add('product/app','PBXFileReference',explicitFileType='wrapper.application',path=MODULE+'.app',includeInIndex='0',sourceTree='BUILT_PRODUCTS_DIR')
    test_product=add('product/tests','PBXFileReference',explicitFileType='wrapper.cfbundle',path=TEST_TARGET+'.xctest',includeInIndex='0',sourceTree='BUILT_PRODUCTS_DIR')
    products=add('group/products','PBXGroup',children=[app_product,test_product],name='Products',sourceTree='<group>')
    main_group=add('group/main','PBXGroup',children=[source_group,test_group,frameworks,products],sourceTree='<group>')
    app_sources=add('phase/app-sources','PBXSourcesBuildPhase',buildActionMask='2147483647',files=sources,runOnlyForDeploymentPostprocessing='0')
    app_frameworks=add('phase/app-frameworks','PBXFrameworksBuildPhase',buildActionMask='2147483647',files=framework_builds,runOnlyForDeploymentPostprocessing='0')
    app_resources=add('phase/app-resources','PBXResourcesBuildPhase',buildActionMask='2147483647',files=[],runOnlyForDeploymentPostprocessing='0')
    test_sources=add('phase/test-sources','PBXSourcesBuildPhase',buildActionMask='2147483647',files=[test_build],runOnlyForDeploymentPostprocessing='0')
    test_frameworks=add('phase/test-frameworks','PBXFrameworksBuildPhase',buildActionMask='2147483647',files=[],runOnlyForDeploymentPostprocessing='0')
    test_resources=add('phase/test-resources','PBXResourcesBuildPhase',buildActionMask='2147483647',files=[],runOnlyForDeploymentPostprocessing='0')
    def configurations(label,settings):
        configurations=[]
        for name in ('Debug','Release'):
            current=dict(settings)
            current['SWIFT_OPTIMIZATION_LEVEL']='-Onone' if name=='Debug' else '-O'
            configurations.append(add('config/'+label+'/'+name,'XCBuildConfiguration',name=name,buildSettings=current))
        return add('configs/'+label,'XCConfigurationList',buildConfigurations=configurations,defaultConfigurationIsVisible='0',defaultConfigurationName='Debug')
    common={'SDKROOT':'iphoneos','IPHONEOS_DEPLOYMENT_TARGET':'16.5','SUPPORTED_PLATFORMS':'iphoneos iphonesimulator','SWIFT_VERSION':'5.0','SWIFT_STRICT_CONCURRENCY':'targeted','SWIFT_DEFAULT_ACTOR_ISOLATION':'nonisolated','CLANG_ENABLE_MODULES':'YES','CLANG_ENABLE_OBJC_ARC':'YES','GCC_C_LANGUAGE_STANDARD':'gnu17','ENABLE_TESTABILITY':'YES','CODE_SIGNING_ALLOWED':'NO','CODE_SIGNING_REQUIRED':'NO','ENABLE_DEBUG_DYLIB':'NO','TARGETED_DEVICE_FAMILY':'1,2','PRODUCT_NAME':'$(TARGET_NAME)','GENERATE_INFOPLIST_FILE':'YES','LD_RUNPATH_SEARCH_PATHS':['$(inherited)','@executable_path/Frameworks','@loader_path/Frameworks']}
    app_settings={**common,'PRODUCT_BUNDLE_IDENTIFIER':'local.openminis.codex.validation-host','PRODUCT_MODULE_NAME':MODULE,'SWIFT_OBJC_BRIDGING_HEADER':'$(SRCROOT)/Sources/OMCodexProcessHost.h','INFOPLIST_KEY_UILaunchScreen_Generation':'YES','INFOPLIST_KEY_UIApplicationSceneManifest_Generation':'NO','INFOPLIST_KEY_CFBundleDisplayName':'Codex Mock Validation'}
    test_settings={**common,'PRODUCT_BUNDLE_IDENTIFIER':'local.openminis.codex.validation-host.tests','PRODUCT_MODULE_NAME':TEST_TARGET,'TEST_HOST':'$(BUILT_PRODUCTS_DIR)/'+MODULE+'.app/'+MODULE,'BUNDLE_LOADER':'$(TEST_HOST)','TEST_TARGET_NAME':MODULE,'FRAMEWORK_SEARCH_PATHS':['$(inherited)','$(PLATFORM_DIR)/Developer/Library/Frameworks'],'SWIFT_INCLUDE_PATHS':['$(inherited)','$(PLATFORM_DIR)/Developer/usr/lib']}
    app_configs=configurations('app',app_settings);test_configs=configurations('tests',test_settings)
    project_configs=configurations('project',{'SDKROOT':'iphoneos','IPHONEOS_DEPLOYMENT_TARGET':'16.5','SWIFT_VERSION':'5.0','CLANG_ENABLE_MODULES':'YES','CLANG_ENABLE_OBJC_ARC':'YES'})
    app_target=add('target/app','PBXNativeTarget',buildConfigurationList=app_configs,buildPhases=[app_sources,app_frameworks,app_resources],buildRules=[],dependencies=[],name=MODULE,productName=MODULE,productReference=app_product,productType='com.apple.product-type.application')
    proxy=add('proxy/app','PBXContainerItemProxy',containerPortal=uid('project'),proxyType='1',remoteGlobalIDString=app_target,remoteInfo=MODULE)
    dependency=add('dependency/app','PBXTargetDependency',target=app_target,targetProxy=proxy)
    test_target=add('target/tests','PBXNativeTarget',buildConfigurationList=test_configs,buildPhases=[test_sources,test_frameworks,test_resources],buildRules=[],dependencies=[dependency],name=TEST_TARGET,productName=TEST_TARGET,productReference=test_product,productType='com.apple.product-type.bundle.unit-test')
    project=add('project','PBXProject',attributes={'LastUpgradeCheck':'1600','TargetAttributes':{app_target:{'CreatedOnToolsVersion':'16.0'},test_target:{'CreatedOnToolsVersion':'16.0','TestTargetID':app_target}}},buildConfigurationList=project_configs,compatibilityVersion='Xcode 14.0',developmentRegion='en',hasScannedForEncodings='0',knownRegions=['en','Base'],mainGroup=main_group,productRefGroup=products,projectDirPath='',projectRoot='',targets=[app_target,test_target])
    return '// !$*UTF8*$!\n'+encode({'archiveVersion':'1','classes':{},'objectVersion':'56','objects':objects,'rootObject':project})+'\n'


def scheme_xml():
    root=ET.Element('Scheme',{'LastUpgradeVersion':'1600','version':'1.3'})
    build=ET.SubElement(root,'BuildAction',{'parallelizeBuildables':'YES','buildImplicitDependencies':'YES'})
    entries=ET.SubElement(build,'BuildActionEntries')
    def reference(parent,label,name,path):
        return ET.SubElement(parent,'BuildableReference',{'BuildableIdentifier':'primary','BlueprintIdentifier':uid(label),'BuildableName':path,'BlueprintName':name,'ReferencedContainer':'container:'+MODULE+'.xcodeproj'})
    for label,name,path in [('target/app',MODULE,MODULE+'.app'),('target/tests',TEST_TARGET,TEST_TARGET+'.xctest')]:
        entry=ET.SubElement(entries,'BuildActionEntry',{'buildForTesting':'YES','buildForRunning':'NO','buildForProfiling':'NO','buildForArchiving':'NO','buildForAnalyzing':'YES'})
        reference(entry,label,name,path)
    action=ET.SubElement(root,'TestAction',{'buildConfiguration':'Debug','selectedDebuggerIdentifier':'Xcode.DebuggerFoundation.Debugger.LLDB','selectedLauncherIdentifier':'Xcode.IDEFoundation.Launcher.LLDB','shouldUseLaunchSchemeArgsEnv':'NO','codeCoverageEnabled':'NO'})
    tests=ET.SubElement(action,'Testables')
    test=ET.SubElement(tests,'TestableReference',{'skipped':'NO','parallelizable':'NO'})
    reference(test,'target/tests',TEST_TARGET,TEST_TARGET+'.xctest')
    macro=ET.SubElement(action,'MacroExpansion');reference(macro,'target/app',MODULE,MODULE+'.app')
    ET.indent(root)
    return ET.tostring(root,encoding='unicode',xml_declaration=True)+'\n'


def generate(output):
    output=Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise Refused('Validation output already exists; refuse to overwrite')
    output.parent.mkdir(parents=True,exist_ok=True)
    stage=Path(tempfile.mkdtemp(prefix='.codex-apple-check-',dir=output.parent))
    source_hashes={}
    try:
        source=stage/'Sources';source.mkdir()
        for name in FILES:
            data=(SOURCE/name).read_bytes()
            (source/name).write_bytes(data)
            source_hashes[name]=hashlib.sha256(data).hexdigest()
        (source/'ValidationMain.m').write_text(HOST)
        tests=stage/'Tests';tests.mkdir()
        # XCTest fixtures are compiled against the production Swift module,
        # not a translated Python model or stub implementation.
        (tests/'CodexSessionEngineTests.swift').write_text('@testable import '+MODULE+'\n'+FIXTURE.read_text())
        project=stage/(MODULE+'.xcodeproj');project.mkdir()
        (project/'project.pbxproj').write_text(project_text())
        schemes=project/'xcshareddata/xcschemes';schemes.mkdir(parents=True)
        (schemes/(MODULE+'.xcscheme')).write_text(scheme_xml())
        manifest={'mock_only':True,'core_runtime_included':False,'core_will_be_executed':False,
                  'upstream_sources_modified':False,'production_sources_sha256':source_hashes,
                  'fixture_sha256':hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
                  'apple_build_performed':False,'xctest_performed':False}
        (stage/'VALIDATION-PROJECT.json').write_text(json.dumps(manifest,indent=2)+'\n')
        os.replace(stage,output)
        return output
    finally:
        if stage.exists():shutil.rmtree(stage)


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    try:
        result=generate(args.out)
    except (Refused,OSError) as error:
        parser.exit(1,'REFUSED: '+str(error)+'\n')
    print('GENERATED ONLY:',result)
    print('No Swift compiler, Xcode build or XCTest was invoked by this generator.')
