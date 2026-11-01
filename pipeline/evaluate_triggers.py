#!/usr/bin/env python3
"""Evaluate journal A∩B triggers: did price go up after the signal?

Treats scanner hits (especially A and B together) as synthetic buy candidates
when TradingAgents is disabled.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

PIPELINE = Path(__file__).resolve().parent
sys.path.insert(0, str(PIPELINE))

from journal import DEFAULT_JOURNAL, read_journal  # noqa: E402


def _parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _later_price(ticker: str, after: datetime, horizon: timedelta) -> float | None:
    import yfinance as yf

    target = after + horizon
    start = after.date()
    end = (target + timedelta(days=3)).date()
    raw = yf.download(
        ticker,
        start=start.isoformat(),
        end=end.isoformat(),
        interval="15m",
        auto_adjust=True,
        progress=False,
        threads=False,
    )
    if raw is None or raw.empty:
        return None
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = [c[0] for c in raw.columns]
    idx = raw.index
    after_cmp = after.replace(tzinfo=None) if idx.tz is None else after
    target_cmp = target.replace(tzinfo=None) if idx.tz is None else target
    if idx.tz is not None:
        if after_cmp.tzinfo is None:
            after_cmp = after_cmp.replace(tzinfo=timezone.utc)
        if target_cmp.tzinfo is None:
            target_cmp = target_cmp.replace(tzinfo=timezone.utc)
    # Prefer bar nearest to target time at/after horizon
    future = raw[idx >= target_cmp]
    if future.empty:
        future = raw[idx >= after_cmp]
    if future.empty or "Close" not in future.columns:
        return None
    return float(future["Close"].iloc[0])


def main() -> int:
    p = argparse.ArgumentParser(description="Evaluate trigger → later price")
    p.add_argument("--journal", type=Path, default=DEFAULT_JOURNAL)
    p.add_argument("--horizon-minutes", type=float, default=60.0)
    p.add_argument("--require-ab", action="store_true", default=True,
                   help="Only A∩B cases (default)")
    p.add_argument("--any-trigger", action="store_true",
                   help="Include A-only or B-only too")
    p.add_argument("--min-age-minutes", type=float, default=30.0,
                   help="Only evaluate signals at least this old")
    p.add_argument("--out", type=Path, default=None)
    args = p.parse_args()

    rows = read_journal(args.journal)
    if not rows:
        print(f"No journal entries at {args.journal}")
        return 0

    horizon = timedelta(minutes=args.horizon_minutes)
    min_age = timedelta(minutes=args.min_age_minutes)
    now = datetime.now(timezone.utc)

    candidates = []
    for r in rows:
        sources = set(r.get("trigger_sources") or [])
        if args.any_trigger:
            ok = bool(sources)
        else:
            ok = sources >= {"A", "B"} or (
                r.get("triggered_by_a") and r.get("triggered_by_b")
            )
        if not ok:
            continue
        ts = _parse_ts(r["ts"])
        if now - ts < min_age:
            continue
        if r.get("price_at_signal") is None:
            continue
        candidates.append(r)

    # First signal per ticker (earliest) — avoid counting spam loops
    by_ticker: dict[str, dict] = {}
    for r in sorted(candidates, key=lambda x: x["ts"]):
        t = r["ticker"]
        if t not in by_ticker:
            by_ticker[t] = r
    candidates = list(by_ticker.values())

    print(
        f"Evaluating {len(candidates)} unique tickers | horizon={args.horizon_minutes}m "
        f"| min_age={args.min_age_minutes}m",
        flush=True,
    )

    results = []
    ups = downs = flat = skipped = 0
    for r in candidates:
        ts = _parse_ts(r["ts"])
        px0 = float(r["price_at_signal"])
        later = _later_price(r["ticker"], ts, horizon)
        if later is None:
            skipped += 1
            continue
        ret = (later - px0) / px0 * 100.0
        good = ret > 0
        if ret > 0.15:
            ups += 1
        elif ret < -0.15:
            downs += 1
        else:
            flat += 1
        row = {
            "id": r["id"],
            "ticker": r["ticker"],
            "ts": r["ts"],
            "triggers": r.get("trigger_sources"),
            "a_patterns": r.get("a_patterns"),
            "b_rel_volume": r.get("b_rel_volume"),
            "price_at_signal": px0,
            "price_later": round(later, 4),
            "return_pct": round(ret, 4),
            "went_up": good,
            "ta_action": r.get("ta_action"),
        }
        results.append(row)
        flag = "UP" if good else "DOWN"
        print(
            f"  {flag} {r['ticker']}: {px0:.2f} → {later:.2f} ({ret:+.2f}%) "
            f"| A={r.get('a_patterns')} rvol={r.get('b_rel_volume')}",
            flush=True,
        )

    n = len(results)
    summary = {
        "evaluated": n,
        "up": ups,
        "down": downs,
        "flat": flat,
        "no_price": skipped,
        "win_rate_up_pct": round(100.0 * ups / n, 1) if n else None,
        "avg_return_pct": round(sum(r["return_pct"] for r in results) / n, 3) if n else None,
        "horizon_minutes": args.horizon_minutes,
        "results": results,
    }
    print("\n===== SUMMARY =====", flush=True)
    print(json.dumps({k: v for k, v in summary.items() if k != "results"}, indent=2), flush=True)

    out = args.out or (PIPELINE / "out" / "trigger_eval_latest.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"Wrote {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
