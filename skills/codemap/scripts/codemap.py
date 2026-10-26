#!/usr/bin/env python3
"""codemap: layered, human-sized maps of code and of the changes an AI agent made.

Usage:  python codemap.py analyze | review | render | video | mark | demo | doctor   (--help for options)
Standard library only; Python 3.9+.
"""
import sys
from pathlib import Path

if sys.version_info < (3, 9):
    sys.exit("codemap needs Python 3.9 or newer (found %d.%d)" % sys.version_info[:2])

sys.path.insert(0, str(Path(__file__).resolve().parent))

from codemap_lib.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
