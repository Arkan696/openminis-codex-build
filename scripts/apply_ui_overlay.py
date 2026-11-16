#!/usr/bin/env python3
"""Compatibility alias. Uses the SAME source/project/UI transaction as apply_overlay."""
from apply_overlay import main
if __name__=='__main__':
    raise SystemExit(main())
