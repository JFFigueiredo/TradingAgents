#!/usr/bin/env python3
"""Daytrade scan (A) + tvscreener (B) → shortlist → parallel lean TradingAgents.

Default A is short-horizon daytrade triggers (≤7d intraday bars).
Optional --swing uses classic stock-pattern (needs longer history).
Default merge mode is AND.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIPELINE = Path(__file__).resolve().parent
OUT_DIR = PIPELINE / "out"
sys.path.insert(0, str(PIPELINE))

from journal import attach_ta_results, build_cases, log_cases  # noqa: E402
from scanners.daytrade_scan import DaytradeConfig, scan_daytrade  # noqa: E402
from scanners.shortlist import merge_shortlist  # noqa: E402
from scanners.stock_pattern_scan import PatternScanConfig, scan_patterns  # noqa: E402
from scanners.tvscreener_scan import TvFilters, list_index_tickers, screen_to_records  # noqa: E402
from scanners.universe import (  # noqa: E402
    DaytradeUniverseFilters,
    default_universe_path,
    ensure_daytrade_universe,
)


def _run_validate_docker(shortlist_path: Path, out_path: Path, workers: int) -> int:
    env = os.environ.copy()
    env["TRADINGAGENTS_DATA_DIR"] = str(ROOT / "data")
    cmd = [
        "docker",
        "compose",
        "run",
        "--rm",
        "-v",
        f"{PIPELINE / 'validate_batch.py'}:/tmp/validate_batch.py:ro",
        "-v",
        f"{shortlist_path}:/tmp/shortlist.json:ro",
        "-v",
        f"{OUT_DIR}:/tmp/out",
        "--entrypoint",
        "python",
        "tradingagents",
        "/tmp/validate_batch.py",
        "--shortlist",
        "/tmp/shortlist.json",
        "--workers",
        str(workers),
        "--out",
        f"/tmp/out/{out_path.name}",
    ]
    print("Running:", " ".join(cmd), flush=True)
    return subprocess.call(cmd, cwd=str(ROOT), env=env)


def main() -> int:
    p = argparse.ArgumentParser(description="Daytrade + tvscreener → TradingAgents")
    p.add_argument("--mode", choices=["and", "or"], default="and")
    p.add_argument(
        "--universe",
        choices=["daytrade", "sp500", "nasdaq100", "file"],
        default="daytrade",
        help="daytrade=liquid movers (~1000+); sp500/nasdaq100=index; file=--universe-file",
    )
    p.add_argument("--index", default="SP500", help="Used when --universe sp500|nasdaq100")
    p.add_argument("--refresh-universe", action="store_true", help="Rebuild daytrade universe")
    p.add_argument("--rvol", type=float, default=1.3, help="Min relative volume (tvscreener B)")
    p.add_argument("--min-mcap", type=float, default=300e6, help="B filter market cap")
    p.add_argument("--min-volume", type=float, default=500_000)
    p.add_argument("--pe-min", type=float, default=0.0, help="0 disables PE lower bound")
    p.add_argument("--pe-max", type=float, default=0.0, help="0 disables PE upper bound")
    p.add_argument("--max-rows", type=int, default=500)
    p.add_argument(
        "--scanner-a",
        choices=["daytrade", "swing"],
        default="daytrade",
        help="daytrade=≤7d intraday triggers; swing=classic stock-pattern",
    )
    p.add_argument("--interval", default="15m", help="daytrade bar size: 5m|15m|30m|60m")
    p.add_argument("--min-daytrade-rvol", type=float, default=1.8)
    p.add_argument(
        "--pattern-set",
        choices=["bull", "bear", "structure", "all"],
        default="structure",
        help="Only used with --scanner-a swing",
    )
    p.add_argument("--pattern-workers", type=int, default=8)
    p.add_argument("--ta-workers", type=int, default=2)
    p.add_argument(
        "--skip-validate",
        "--no-ta",
        action="store_true",
        dest="skip_validate",
        help="Run only scanners A+B (+ journal). Do not call TradingAgents.",
    )
    p.add_argument("--universe-file", type=Path, default=None)
    p.add_argument("--limit-validate", type=int, default=0)
    p.add_argument("--fallback-b", action="store_true")
    p.add_argument("--force-tickers", default="")
    args = p.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    # --- Resolve scan universe (daytrade default: liquid movers, not only SP500) ---
    if args.universe == "file" or args.universe_file:
        path = args.universe_file or default_universe_path()
        if not path.exists():
            print(f"Universe file missing: {path}", flush=True)
            return 1
        universe = [
            ln.strip().upper()
            for ln in path.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.startswith("#")
        ]
        print(f"=== Universe from file: {len(universe)} ({path}) ===", flush=True)
    elif args.universe == "daytrade":
        print("=== Universe: daytrade liquid movers (tvscreener) ===", flush=True)
        universe = ensure_daytrade_universe(
            default_universe_path(),
            DaytradeUniverseFilters(),
            refresh=args.refresh_universe,
        )
        print(f"Daytrade universe size: {len(universe)}", flush=True)
    elif args.universe == "nasdaq100":
        universe = list_index_tickers("NASDAQ100", max_rows=args.max_rows)
        print(f"=== Universe NASDAQ100: {len(universe)} ===", flush=True)
    else:
        universe = list_index_tickers("SP500", max_rows=args.max_rows)
        print(f"=== Universe SP500: {len(universe)} ===", flush=True)

    print("=== Project B: tvscreener filters on that universe snapshot ===", flush=True)
    # B is a live volume/valuation pass. For daytrade universe we screen America
    # with looser PE (many movers have odd/negative PE) and require RVOL.
    pe_min = args.pe_min if args.pe_min > 0 else None
    pe_max = args.pe_max if args.pe_max > 0 else None
    b_index = "NONE" if args.universe == "daytrade" else (
        "NASDAQ100" if args.universe == "nasdaq100" else args.index
    )
    tv_filters = TvFilters(
        index=b_index,
        min_rel_volume=args.rvol,
        min_market_cap=args.min_mcap,
        min_volume=args.min_volume,
        pe_min=pe_min,
        pe_max=pe_max,
        max_rows=max(args.max_rows, 500),
    )
    tv_rows = screen_to_records(tv_filters)
    # Keep B hits that are in our universe (daytrade list may be wider than one TV page)
    uni_set = set(universe)
    if uni_set:
        tv_rows = [r for r in tv_rows if r["ticker"] in uni_set]
    print(f"tvscreener B hits in universe: {len(tv_rows)}", flush=True)
    for row in tv_rows[:15]:
        print(
            f"  B {row['ticker']}: rvol={row.get('rel_volume')} pe={row.get('pe_ttm')}",
            flush=True,
        )

    # A scan set: AND mode → only B hits (cheap); OR → full universe
    if args.mode == "and":
        scan_universe = [r["ticker"] for r in tv_rows] or universe[:80]
        if not tv_rows:
            print(
                "B empty — scanning first 80 universe names for A anyway.",
                flush=True,
            )
    else:
        scan_universe = universe
    universe = scan_universe

    pattern_hits: list[dict] = []
    if args.scanner_a == "daytrade":
        print(
            f"=== Project A: daytrade (fresh {args.interval}, last ~7d) "
            f"on {len(universe)} tickers ===",
            flush=True,
        )
        if universe:
            pattern_hits = scan_daytrade(
                universe,
                DaytradeConfig(
                    interval=args.interval,
                    min_rvol=args.min_daytrade_rvol,
                    max_workers=args.pattern_workers,
                ),
            )
        print(f"daytrade hits: {len(pattern_hits)}", flush=True)
    else:
        print(
            f"=== Project A: swing stock-pattern on {len(universe)} "
            f"({args.pattern_set}) ===",
            flush=True,
        )
        if universe:
            pattern_hits = scan_patterns(
                universe,
                PatternScanConfig(
                    pattern_set=args.pattern_set,
                    max_workers=args.pattern_workers,
                ),
            )
        print(f"stock-pattern hits: {len(pattern_hits)}", flush=True)

    for hit in pattern_hits[:20]:
        print(
            f"  A {hit.get('ticker') or hit.get('sym')}: {hit.get('pattern')}",
            flush=True,
        )

    shortlist = merge_shortlist(pattern_hits, tv_rows, mode=args.mode)
    if args.force_tickers.strip():
        forced = [t.strip().upper() for t in args.force_tickers.split(",") if t.strip()]
        have = {r["ticker"] for r in shortlist}
        for t in forced:
            if t not in have:
                shortlist.append(
                    {
                        "ticker": t,
                        "reasons": ["forced"],
                        "patterns": [],
                        "rel_volume": None,
                        "pe_ttm": None,
                        "market_cap": None,
                        "price": None,
                        "passed_pattern": False,
                        "passed_tvscreener": False,
                    }
                )

    if not shortlist and args.mode == "and" and args.fallback_b and tv_rows:
        print("AND empty — fallback to B (--fallback-b).", flush=True)
        shortlist = merge_shortlist([], tv_rows, mode="or")

    print(f"=== Shortlist ({args.mode.upper()}): {len(shortlist)} ===", flush=True)
    for row in shortlist:
        print(f"  {row['ticker']}: {', '.join(row['reasons'])}", flush=True)

    # Journal: every A and/or B positive (union), even if AND shortlist is smaller.
    cases = build_cases(
        pattern_hits,
        tv_rows,
        run_id=stamp,
        scanner_a=args.scanner_a,
    )
    print(
        f"=== Journal candidates (A∪B): {len(cases)} "
        f"(logged even if TA later says NO) ===",
        flush=True,
    )

    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mode": args.mode,
        "scanner_a": args.scanner_a,
        "interval": args.interval,
        "index": args.index,
        "tvscreener_count": len(tv_rows),
        "pattern_hit_count": len(pattern_hits),
        "shortlist": shortlist,
        "tvscreener": tv_rows,
        "pattern_hits": pattern_hits,
    }
    shortlist_path = OUT_DIR / f"shortlist_{stamp}.json"
    shortlist_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    (OUT_DIR / "shortlist_latest.json").write_text(
        shortlist_path.read_text(encoding="utf-8"), encoding="utf-8"
    )
    print(f"Wrote {shortlist_path}", flush=True)

    if args.skip_validate:
        journal_path = log_cases(cases)
        print(f"Logged {len(cases)} cases (no TA yet) → {journal_path}", flush=True)
        print("TradingAgents DISABLED (--no-ta / --skip-validate). A+B only.", flush=True)
        return 0

    to_validate = shortlist[: args.limit_validate] if args.limit_validate else shortlist
    if not to_validate:
        journal_path = log_cases(cases)
        print(f"Empty shortlist — logged scanner hits only → {journal_path}", flush=True)
        return 0

    validate_input = OUT_DIR / f"validate_input_{stamp}.json"
    validate_input.write_text(json.dumps({"shortlist": to_validate}, indent=2), encoding="utf-8")
    report_name = f"validate_{stamp}.json"
    rc = _run_validate_docker(validate_input, OUT_DIR / report_name, args.ta_workers)
    report_path = OUT_DIR / report_name
    ta_results: list[dict] = []
    if report_path.exists():
        try:
            ta_results = json.loads(report_path.read_text(encoding="utf-8")).get("results") or []
        except json.JSONDecodeError:
            ta_results = []
    cases = attach_ta_results(cases, ta_results)
    journal_path = log_cases(cases)
    print(f"Logged {len(cases)} cases (with TA where run) → {journal_path}", flush=True)
    if rc == 0:
        print(f"Validation report: {report_path}", flush=True)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
