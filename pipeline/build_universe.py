#!/usr/bin/env python3
"""Build / refresh a daytrading universe (liquid US common stocks).

Unlike SP500 (mega-caps that often chop), this ranks by 30-day average volume
and weekly volatility so the rotator has names that actually move.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PIPELINE = Path(__file__).resolve().parent
sys.path.insert(0, str(PIPELINE))

from scanners.universe import (  # noqa: E402
    DaytradeUniverseFilters,
    default_universe_path,
    ensure_daytrade_universe,
    fetch_daytrade_universe_df,
)


def main() -> int:
    p = argparse.ArgumentParser(description="Build daytrade universe file")
    p.add_argument("--out", type=Path, default=default_universe_path())
    p.add_argument("--min-avg-vol", type=float, default=1_500_000)
    p.add_argument("--min-price", type=float, default=5.0)
    p.add_argument("--max-price", type=float, default=250.0)
    p.add_argument("--min-mcap", type=float, default=300e6)
    p.add_argument("--min-volatility-week", type=float, default=2.0)
    p.add_argument("--pages", type=int, default=3, help="500 names per page")
    p.add_argument("--refresh", action="store_true", help="Rebuild even if file exists")
    args = p.parse_args()

    filters = DaytradeUniverseFilters(
        min_avg_volume_30d=args.min_avg_vol,
        min_price=args.min_price,
        max_price=args.max_price,
        min_market_cap=args.min_mcap,
        min_volatility_week=args.min_volatility_week,
        max_pages=args.pages,
    )
    print("Fetching liquid US common stocks via tvscreener…", flush=True)
    tickers = ensure_daytrade_universe(args.out, filters, refresh=True)
    print(f"Wrote {len(tickers)} tickers → {args.out}", flush=True)
    print(f"CSV detail → {args.out.with_suffix('.csv')}", flush=True)
    print("Sample:", ", ".join(tickers[:20]), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
