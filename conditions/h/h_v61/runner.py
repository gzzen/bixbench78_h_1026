#!/usr/bin/env python3
# Path: conditions/h/h_v61/runner.py
# Purpose: Entry point for h_v61; see conditions/h/README.md for the H variants.
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from conditions.h.core.rescue_main import main  # noqa: E402
from conditions.h.h_v61.pipeline import BalancedV61  # noqa: E402

if __name__ == "__main__":
    main(BalancedV61())
