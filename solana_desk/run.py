#!/usr/bin/env python3
"""Entry point: python run.py [--config control] [--headless] [--reset]"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from desk.main import run

if __name__ == "__main__":
    raise SystemExit(run())
