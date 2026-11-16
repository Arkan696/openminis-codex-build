"""Local-only overlay helpers. No core execution, network, credentials or signing."""
from pathlib import Path
import difflib
import hashlib
import json
import os
import re
import stat
import struct
import subprocess

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = '1a71df0f381cefde80bf912b723de6f9831bc177'
CA_SHA = '427c064216761a1e72a8b30f0d954d08cb6e4435a82091fb865afd20c8497285'
FILES = {
    'CodexProtocol.swift': 'sourcecode.swift',
    'CodexSessionEngine.swift': 'sourcecode.swift',
    'CodexSessionView.swift': 'sourcecode.swift',
    'OMProviderPaste.swift': 'sourcecode.swift',
    'OMCodexProcessHost.m': 'sourcecode.c.objc',
    'OMCodexProcessHost.h': 'sourcecode.c.h',
    'OMCodexFramer.c': 'sourcecode.c.c',
    'OMCodexFramer.h': 'sourcecode.c.h',
}
COMPILED = tuple(n for n in FILES if not n.endswith('.h'))
AFFECTED = (
    'src/ios/Minis.xcodeproj/project.pbxproj',
    'src/ios/MinisApp-Bridging-Header.h',
    'src/ios/Views/ContentView/ContentView.swift',
    'src/ios/Views/ContentView/ContentView+SidebarToolbar.swift',
)
SOURCE = ROOT / 'overlay/src/ios/CodexEngine'
PROFILE = SOURCE / 'CodexEngineResources/text-only.toml'

class Refused(ValueError):
    pass

def digest(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()

def blob_hash(data):
    return hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()

def baseline(repo):
    """Validate exact upstream blobs, not just target-version text markers."""
    pinned = json.loads((ROOT / 'upstream-files.json').read_text())['files']
    result = {}
    repo = Path(repo).resolve()
    for name in AFFECTED:
        path = repo / name
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(repo):
            raise Refused('Missing or linked baseline file: ' + name)
        raw = path.read_bytes()
        if blob_hash(raw) != pinned[name]:
            raise Refused('Baseline differs from pinned upstream: ' + name)
        result[name] = raw.decode('utf-8')
    return result

def macho_info(path):
    """Bounded load-command inspection; NOT signature authentication."""
    path = Path(path)
    st = path.lstat()
    if not stat.S_ISREG(st.st_mode) or st.st_size < 32:
        raise Refused('Runtime must be a regular Mach-O file')
    with path.open('rb') as f:
        head = f.read(32)
        magic, cpu, subtype, kind, n, size, flags, reserved = struct.unpack('<8I', head)
        if (magic, cpu, kind) != (0xfeedfacf, 0x0100000c, 2):
            raise Refused('Expected a thin arm64 Mach-O executable')
        if not n or n > 8192 or size > 4*1024*1024 or size > st.st_size-32:
            raise Refused('Invalid Mach-O command bounds')
        commands = f.read(size)
    pos = 0
    platforms = []
    signature = False
    for _ in range(n):
        if pos+8 > size:
            raise Refused('Truncated Mach-O load command')
        cmd, length = struct.unpack_from('<II', commands, pos)
        if length < 8 or length % 8 or pos+length > size:
            raise Refused('Invalid Mach-O command size')
        if cmd == 0x32:
            if length < 24:
                raise Refused('Truncated LC_BUILD_VERSION')
            platform, minimum, sdk = struct.unpack_from('<III', commands, pos+8)
            platforms.append((platform, minimum, sdk))
        elif cmd == 0x25:
            if length < 16:
                raise Refused('Truncated LC_VERSION_MIN_IPHONEOS')
            minimum, sdk = struct.unpack_from('<II', commands, pos+8)
            platforms.append((2, minimum, sdk))
        elif cmd == 0x1d:
            if length < 16:
                raise Refused('Truncated code signature command')
            offset, count = struct.unpack_from('<II', commands, pos+8)
            if not count or offset+count > st.st_size:
                raise Refused('Code signature blob bounds invalid')
            signature = True
        pos += length
    if pos != size or not platforms or any(p != 2 or m > 0x100500 for p,m,_ in platforms):
        raise Refused('Expected device iOS platform with minimum OS <=16.5')
    return {'cpu': 'arm64', 'platform': 'iOS-device',
            'minimum_os_packed': max(m for _,m,_ in platforms),
            'code_signature_blob_present': signature,
            'signature_authenticated': False, 'bytes': st.st_size}

def validate_runtime(runtime):
    runtime = Path(runtime)
    if runtime.is_symlink() or not runtime.is_dir():
        raise Refused('Runtime directory missing or linked')
    expected_profile = PROFILE.read_bytes()
    report = {}
    for name in ('codex', 'ca-certificates.crt', 'text-only.toml'):
        p = runtime / name
        if p.is_symlink() or not p.is_file():
            raise Refused('Missing/linked runtime member: ' + name)
        report[name] = {'sha256': digest(p), 'bytes': p.stat().st_size}
    if report['ca-certificates.crt']['sha256'] != CA_SHA:
        raise Refused('Mozilla CA SHA-256 mismatch')
    if (runtime/'text-only.toml').read_bytes() != expected_profile:
        raise Refused('Runtime profile differs from reviewed ephemeral profile')
    report['codex'].update(macho_info(runtime/'codex'))
    if not (runtime/'codex').stat().st_mode & 0o111:
        raise Refused('Codex executable permission missing')
    helper = runtime/'codex-code-mode-host'
    if helper.exists() or helper.is_symlink():
        helper_info = macho_info(helper)
        report[helper.name] = {'sha256': digest(helper), **helper_info}
    return report

def _replace(text, old, new):
    if text.count(old) != 1:
        raise Refused('Pinned source anchor changed: ' + old[:65])
    return text.replace(old, new, 1)

def object_id(label):
    return hashlib.sha256(('OpenMinisCodex/v1/' + label).encode()).hexdigest()[:24].upper()

def transform(inputs):
    """Only audited target changes. Existing entitlement/identity values preserved."""
    output = dict(inputs)
    pbx = inputs[AFFECTED[0]]
    ids = {}
    def uid(label):
        value = object_id(label)
        if value in ids.values() or re.search(r'\b' + value + r'\b', pbx):
            raise Refused('PBX ID collision: ' + label)
        ids[label] = value
        return value
    group = uid('engine-group')
    runtime_ref, runtime_build = uid('runtime-ref'), uid('runtime-build')
    build_lines, ref_lines, children, sources = [], [], [], []
    for name, filetype in FILES.items():
        ref = uid('ref/' + name)
        ref_lines.append(f'\t\t{ref} /* {name} */ = {{isa = PBXFileReference; lastKnownFileType = {filetype}; path = {name}; sourceTree = "<group>"; }};')
        children.append(f'\t\t\t\t{ref} /* {name} */,')
        if name in COMPILED:
            bid = uid('source/' + name)
            build_lines.append(f'\t\t{bid} /* {name} in Sources */ = {{isa = PBXBuildFile; fileRef = {ref} /* {name} */; }};')
            sources.append(f'\t\t\t\t{bid} /* {name} in Sources */,')
    ref_lines.append(f'\t\t{runtime_ref} /* CodexRuntime */ = {{isa = PBXFileReference; lastKnownFileType = folder; path = CodexRuntime; sourceTree = "<group>"; }};')
    children.append(f'\t\t\t\t{runtime_ref} /* CodexRuntime */,')
    build_lines.append(f'\t\t{runtime_build} /* CodexRuntime in Resources */ = {{isa = PBXBuildFile; fileRef = {runtime_ref} /* CodexRuntime */; }};')
    def section(text, kind, lines):
        marker = f'/* End {kind} section */'
        return _replace(text, marker, '\n'.join(lines) + '\n' + marker)
    edited = section(pbx, 'PBXBuildFile', build_lines)
    edited = section(edited, 'PBXFileReference', ref_lines)
    declaration = f'\t\t{group} /* CodexEngine */ = {{\n\t\t\tisa = PBXGroup;\n\t\t\tchildren = (\n' + '\n'.join(children) + '\n\t\t\t);\n\t\t\tpath = CodexEngine;\n\t\t\tsourceTree = "<group>";\n\t\t};'
    edited = section(edited, 'PBXGroup', [declaration])
    group_anchor = '\t\tE51000031 /* Minis */ = {\n\t\t\tisa = PBXGroup;\n\t\t\tchildren = (\n'
    edited = _replace(edited, group_anchor, group_anchor + f'\t\t\t\t{group} /* CodexEngine */,\n')
    for phase, kind, lines in (
        ('E51000041', 'Sources', sources),
        ('E51000042', 'Resources', [f'\t\t\t\t{runtime_build} /* CodexRuntime in Resources */,']),
    ):
        marker = f'\t\t{phase} /* {kind} */ = {{\n\t\t\tisa = PBX{kind}BuildPhase;\n\t\t\tbuildActionMask = 2147483647;\n\t\t\tfiles = (\n'
        edited = _replace(edited, marker, marker + '\n'.join(lines) + '\n')
    output[AFFECTED[0]] = edited
    output[AFFECTED[1]] = _replace(inputs[AFFECTED[1]], '#import "ISHShellExecutor.h"', '#import "ISHShellExecutor.h"\n#import "CodexEngine/OMCodexProcessHost.h"')
    view = _replace(inputs[AFFECTED[2]], '    case browserManagement\n    case syncMigrationDetail', '    case browserManagement\n    case codexSession\n    case syncMigrationDetail')
    view = _replace(view, '            case .browser:\n                BrowserSheetView(pool: browserPool)', '            case .codexSession:\n                CodexSessionView()\n            case .browser:\n                BrowserSheetView(pool: browserPool)')
    output[AFFECTED[2]] = view
    anchor = '                    Button {\n                        showTerminal = true\n                    } label: {\n                        Label("Shell Terminal", systemImage: "terminal")\n                    }'
    menu = '                    Button {\n                        activeToolSheet = .codexSession\n                    } label: {\n                        Label("Codex 独立会话", systemImage: "cpu")\n                    }\n'
    output[AFFECTED[3]] = _replace(inputs[AFFECTED[3]], anchor, menu + anchor)
    return output

def integration_patch(inputs):
    output = transform(inputs)
    pieces = []
    for rel in AFFECTED:
        pieces.extend(difflib.unified_diff(inputs[rel].splitlines(True), output[rel].splitlines(True), fromfile='a/'+rel, tofile='b/'+rel))
    return ''.join(pieces)
