#!/usr/bin/env python3
"""Claude Code / Cursor stdio launcher. Puts the plugin checkout on sys.path."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hermes_telegram_ads.mcp import main

if __name__ == "__main__":
    main()
