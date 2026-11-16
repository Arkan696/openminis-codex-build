#!/usr/bin/env python3
"""Stage a locally supplied .deb WITHOUT executing it, signing or networking.

The user already supplies the runtime for local use. --authorized is a local-use
acknowledgement only, not a claim about the right to redistribute the binary.
"""
from pathlib import Path
from contextlib import contextmanager
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
from overlay_lib import PROFILE, CA_SHA, Refused, digest, macho_info, validate_runtime

MEMBERS = {
    'usr/libexec/codex/codex': 'codex',
    'usr/libexec/codex/codex-code-mode-host': 'codex-code-mode-host',
}
MEMBER_LIMIT = 400 * 1024 * 1024
ARCHIVE_LIMIT = 800 * 1024 * 1024

class BoundedReader:
    def __init__(self, raw, limit):
        self.raw, self.limit, self.count = raw, limit, 0
    def read(self, n=-1):
        # tarfile stream mode always requests bounded chunks; reject huge reads.
        n = min(n if n >= 0 else 10240, 65536)
        data = self.raw.read(n)
        self.count += len(data)
        if self.count > self.limit:
            raise Refused('Decompressed archive exceeds the local staging limit')
        return data

@contextmanager
def package_stream(deb):
    """Two bounded pipes, stderr discarded, all child processes reaped by owner."""
    ar = decoder = None
    try:
        ar = subprocess.Popen(['ar','p',str(deb),'data.tar.zst'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        decoder = subprocess.Popen(['zstd','-q','-d','-c'], stdin=ar.stdout, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        ar.stdout.close()
        reader = BoundedReader(decoder.stdout, ARCHIVE_LIMIT)
        yield reader
        # Standard tar records may end in zero padding. Count it toward the same
        # decompression budget and never accumulate the complete archive in RAM.
        while padding := reader.read(65536):
            if any(padding):
                raise Refused('Unexpected non-padding data after archive')
        for process in (decoder, ar):
            if process.wait(timeout=15) != 0:
                raise Refused('ar or zstd rejected the package')
    finally:
        if decoder and decoder.stdout:
            decoder.stdout.close()
        for process in (decoder, ar):
            if process is None:
                continue
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

def canonical_member(raw):
    while raw.startswith('./'):
        raw = raw[2:]
    # Never lstrip('./'): that would turn traversal paths into approved members.
    if raw.startswith('/') or any(p == '..' for p in raw.split('/')):
        raise Refused('Archive traversal path rejected')
    return raw

def extract_runtime(stream, stage):
    found = set()
    members = 0
    with tarfile.open(fileobj=stream, mode='r|') as archive:
        for member in archive:
            members += 1
            if members > 10000 or member.size > ARCHIVE_LIMIT or member.size < 0:
                raise Refused('Archive member bounds exceeded')
            name = canonical_member(member.name)
            if name not in MEMBERS:
                continue
            output_name = MEMBERS[name]
            if output_name in found or not member.isfile() or member.size > MEMBER_LIMIT:
                raise Refused('Duplicate, linked or oversized runtime member')
            source = archive.extractfile(member)
            if source is None:
                raise Refused('Missing runtime member bytes')
            remaining = member.size
            path = stage/output_name
            with source, path.open('xb') as dest:
                while remaining:
                    block = source.read(min(1024*1024, remaining))
                    if not block:
                        raise Refused('Truncated runtime member')
                    dest.write(block)
                    remaining -= len(block)
            path.chmod(0o755)
            macho_info(path)
            found.add(output_name)
    if found != set(MEMBERS.values()):
        raise Refused('Required codex runtime members are missing')

def prepare(deb, ca, output):
    if output.exists() or output.is_symlink():
        raise Refused('Output exists; choose a new path')
    if deb.is_symlink() or not deb.is_file() or deb.stat().st_size > 256*1024*1024:
        raise Refused('Expected a regular local .deb of at most 256 MiB')
    if ca.is_symlink() or not ca.is_file() or digest(ca) != CA_SHA:
        raise Refused('Fixed public Mozilla CA digest mismatch')
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.codex-runtime-', dir=output.parent))
    try:
        with package_stream(deb) as stream:
            extract_runtime(stream, stage)
        shutil.copyfile(ca, stage/'ca-certificates.crt')
        shutil.copyfile(PROFILE, stage/'text-only.toml')
        for name in ('ca-certificates.crt', 'text-only.toml'):
            (stage/name).chmod(0o644)
        report = validate_runtime(stage)
        (stage/'runtime-manifest.json').write_text(json.dumps(report, indent=2)+'\n')
        os.replace(stage, output)
        return report
    finally:
        if stage.exists():
            shutil.rmtree(stage)

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--deb', type=Path, required=True)
    ap.add_argument('--ca-bundle', type=Path, required=True)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--authorized', action='store_true', help='acknowledge local use is authorized; no redistribution claim')
    args = ap.parse_args(argv)
    if not args.authorized:
        ap.exit(1, 'REFUSED: local use acknowledgement missing; no files created\n')
    try:
        report = prepare(args.deb, args.ca_bundle, args.out.absolute())
    except (Refused, OSError, tarfile.TarError, subprocess.SubprocessError) as error:
        ap.exit(1, 'REFUSED: '+str(error)+'\n')
    for name, item in report.items():
        print(name, 'bytes=', item['bytes'], 'sha256=', item['sha256'])
    print('STAGED ONLY: device iOS metadata checked; signature NOT authenticated. Nothing executed or uploaded.')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
