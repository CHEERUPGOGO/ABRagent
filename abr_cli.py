#!/usr/bin/env python3
"""ABR CLI alias shortcut."""

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from auto_battery_research.cli import main

if __name__ == "__main__":
    main()
