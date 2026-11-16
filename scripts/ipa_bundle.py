"""Inspect an already compiled iPhone bundle. Does not execute app or core."""
from pathlib import Path
import hashlib, plistlib, stat, struct
from overlay_lib import Refused

APP_ID='local.openminis.codex'
GROUP='group.'+APP_ID
MACH_MAGIC=b'\xcf\xfa\xed\xfe'
FAT_MAGICS={b'\xca\xfe\xba\xbe',b'\xbe\xba\xfe\xca',b'\xca\xfe\xba\xbf',b'\xbf\xba\xfe\xca'}

def inspect_macho(path, require_signature=False):
    p=Path(path);st=p.lstat()
    if not stat.S_ISREG(st.st_mode) or st.st_size<32:raise Refused('Not a regular Mach-O: '+str(p))
    with p.open('rb') as f:
        head=f.read(32)
        magic,cpu,sub,kind,n,size,flags,reserved=struct.unpack('<8I',head)
        if magic!=0xfeedfacf or cpu!=0x100000c or kind not in (2,6,8):
            raise Refused('Expected thin arm64 device executable/dylib/bundle: '+str(p))
        if not n or n>8192 or size>4*1024*1024 or size>st.st_size-32:raise Refused('Invalid command bounds')
        commands=f.read(size)
        pos=0;platforms=[];signature=None;encrypted=False
        for _ in range(n):
            if pos+8>size:raise Refused('Truncated command')
            cmd,length=struct.unpack_from('<II',commands,pos)
            if length<8 or length%8 or pos+length>size:raise Refused('Invalid command length')
            if cmd==0x32:
                if length<24:raise Refused('Truncated build-version command')
                platform,minimum,sdk=struct.unpack_from('<III',commands,pos+8)
                platforms.append((platform,minimum))
            elif cmd==0x25:
                if length<16:raise Refused('Truncated min-version command')
                platforms.append((2,struct.unpack_from('<I',commands,pos+8)[0]))
            elif cmd==0x1d:
                if signature is not None or length<16:raise Refused('Invalid signature command')
                off,count=struct.unpack_from('<II',commands,pos+8)
                if not count or off+count>st.st_size:raise Refused('Signature outside file')
                signature=(off,count)
            elif cmd in (0x21,0x2c):
                if length<20:raise Refused('Truncated encryption info')
                encrypted=encrypted or struct.unpack_from('<I',commands,pos+16)[0]!=0
            pos+=length
        if pos!=size or not platforms or any(p!=2 or m>0x100500 for p,m in platforms):
            raise Refused('Not device iOS compatible with 16.5: '+str(path))
        if encrypted:raise Refused('Encrypted App Store executable cannot be repackaged')
        if require_signature and signature is None:raise Refused('No embedded code signature: '+str(path))
        ent={}
        if signature:
            f.seek(signature[0]);blob=f.read(signature[1])
            if len(blob)<12:raise Refused('Truncated signature blob')
            magic,total,count=struct.unpack_from('>III',blob)
            if magic!=0xfade0cc0 or total>len(blob) or count>128 or 12+8*count>total:
                raise Refused('Malformed code signature superblob')
            for i in range(count):
                typ,off=struct.unpack_from('>II',blob,12+8*i)
                if off+8>total:raise Refused('Invalid code signature slot')
                slotmagic,length=struct.unpack_from('>II',blob,off)
                if length<8 or off+length>total:raise Refused('Invalid signature slot length')
                if typ==5:
                    if slotmagic!=0xfade7171 or ent:raise Refused('Invalid entitlements slot')
                    ent=plistlib.loads(blob[off+8:off+length])
                    if not isinstance(ent,dict):raise Refused('Invalid entitlements value')
    return {'kind':kind,'minimum_os_packed':max(m for _,m in platforms),'platform':'iOS-device',
            'entitlements':ent,'signature_present':signature is not None,'signature_authenticated':False}

def regular_tree(bundle):
    root=Path(bundle)
    for p in sorted(root.rglob('*')):
        mode=p.lstat().st_mode
        if stat.S_ISLNK(mode) or not(stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
            raise Refused('Linked/special bundle entry is not accepted: '+str(p))
        if stat.S_ISREG(mode):yield p

def executable_path(bundle):
    d=plistlib.loads((bundle/'Info.plist').read_bytes());name=d.get('CFBundleExecutable')
    if not isinstance(name,str) or not name or name in ('.','..') or Path(name).name!=name:
        raise Refused('Unsafe bundle executable name')
    exe=bundle/name
    if not exe.is_file():raise Refused('Bundle executable missing')
    return d,exe

def inventory(bundle, require_signature=False):
    bundle=Path(bundle);info,exe=executable_path(bundle)
    if info.get('CFBundleIdentifier')!=APP_ID:raise Refused('Expected independent OpenMinis Codex bundle ID')
    if not set(info.get('CFBundleSupportedPlatforms',[]))=={'iPhoneOS'}:raise Refused('App was not built for iPhoneOS')
    try:
        version=tuple(int(x) for x in info.get('MinimumOSVersion','999').split('.'))
    except (ValueError,AttributeError):raise Refused('Invalid MinimumOSVersion')
    if not 1<=len(version)<=3:raise Refused('Invalid MinimumOSVersion')
    version=version+(0,)*(3-len(version))
    if version>(16,5,0):raise Refused('App minimum OS is newer than iOS16.5')
    machos={}
    for p in regular_tree(bundle):
        with p.open('rb') as f:magic=f.read(4)
        if magic in FAT_MAGICS:raise Refused('Universal Mach-O must be thinned to device arm64 before finalization: '+str(p))
        if magic==MACH_MAGIC:machos[p]=inspect_macho(p,require_signature)
    if exe not in machos or machos[exe]['kind']!=2:raise Refused('Main executable is not a real arm64 Mach-O')
    for ext in bundle.glob('PlugIns/*.appex'):
        d,x=executable_path(ext)
        if not d.get('CFBundleIdentifier','').startswith(APP_ID+'.'):raise Refused('Extension identity is not isolated')
        if x not in machos:raise Refused('Extension executable not in Mach-O inventory')
    return info,exe,machos
