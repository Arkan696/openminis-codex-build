#!/usr/bin/env python3
"""Regenerate the pinned patch from exact audited files; never edits upstream."""
from pathlib import Path
import argparse
from overlay_lib import ROOT, EXPECTED, AFFECTED, Refused, baseline, integration_patch

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--baseline', type=Path, required=True, help='OpenMinis root or read-only research snapshot')
    ap.add_argument('--output', type=Path, default=ROOT/'patches/openminis-codex-integration.patch')
    args=ap.parse_args()
    try:
        patch=integration_patch(baseline(args.baseline))
    except (Refused,OSError) as e:
        ap.exit(1,'REFUSED: '+str(e)+'\n')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(patch)
    print('Generated:',args.output,'bytes:',len(patch.encode()),'upstream:',EXPECTED)

if __name__=='__main__':
    main()
