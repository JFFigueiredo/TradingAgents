#!/usr/bin/env python3
"""Settle journal cases with later prices to score TradingAgents decisions.

Example:
  python pipeline/settle_journal.py --horizon-hours 24
  python pipeline/settle_journal.py --horizon-days 1 --summary
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

PIPELINE = Path(__file__).resolve().parent
sys.path.insert(0, str(PIPELINE))

from journal import (  # noqa: E402
    DEFAULT_JOURNAL,
    read_journal,
    rewrite_journal,
    score_outcome,
)


def _parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _price_after(ticker: str, after: datetime, prefer_intraday: bool = True) -> float | None:
    """Best-effort later price from yfinance."""
    import yfinance as yf

    start = after.date()
    end = (after + timedelta(days=10)).date()
    if prefer_intraday:
        raw = yf.download(
            ticker,
            start=start.isoformat(),
            end=end.isoformat(),
            interval="15m",
            auto_adjust=True,
            progress=False,
            threads=False,
        )
        if raw is not None and not raw.empty:
            if isinstance(raw.columns, pd.MultiIndex):
                raw.columns = [c[0] for c in raw.columns]
            # first bar at/after 'after'
            idx = raw.index
            if idx.tz is None:
                # treat as UTC-naive comparable
                after_cmp = after.replace(tzinfo=None)
            else:
                after_cmp = after if after.tzinfo else after.replace(tzinfo=timezone.utc)
                if idx.tz is not None and after_cmp.tzinfo is None:
                    after_cmp = after_cmp.replace(tzinfo=timezone.utc)
            future = raw[idx >= after_cmp]
            if not future.empty and "Close" in future.columns:
                return float(future["Close"].iloc[0])

    raw = yf.download(
        ticker,
        start=start.isoformat(),
        end=(end + timedelta(days=5)).isoformat(),
        interval="1d",
        auto_adjust=True,
        progress=False,
        threads=False,
    )
    if raw is None or raw.empty:
        return None
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = [c[0] for c in raw.columns]
    # next daily close after signal day
    for ts, row in raw.iterrows():
        d = ts.to_pydatetime()
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        if d.date() > after.date() and "Close" in raw.columns:
            return float(row["Close"])
    if "Close" in raw.columns:
        return float(raw["Close"].iloc[-1])
    return None


def main() -> int:
    p = argparse.ArgumentParser(description="Settle decision journal outcomes")
    p.add_argument("--journal", type=Path, default=DEFAULT_JOURNAL)
    p.add_argument("--horizon-hours", type=float, default=24.0)
    p.add_argument("--horizon-days", type=float, default=0.0, help="Overrides hours if > 0")
    p.add_argument("--summary", action="store_true")
    p.add_argument("--limit", type=int, default=0)
    args = p.parse_args()

    horizon = (
        timedelta(days=args.horizon_days)
        if args.horizon_days and args.horizon_days > 0
        else timedelta(hours=args.horizon_hours)
    )

    rows = read_journal(args.journal)
    if not rows:
        print(f"No journal at {args.journal}")
        return 0

    pending = [r for r in rows if r.get("outcome") is None]
    if args.limit:
        pending = pending[: args.limit]
    print(f"Settling {len(pending)} / {len(rows)} open cases (horizon={horizon})…", flush=True)

    by_id = {r["id"]: r for r in rows}
    settled = 0
    for case in pending:
        ts = _parse_ts(case["ts"])
        due = ts + horizon
        if datetime.now(timezone.utc) < due:
            continue
        ticker = case["ticker"]
        px0 = case.get("price_at_signal")
        if px0 is None:
            case["outcome"] = {
                "settled_at": datetime.now(timezone.utc).isoformat(),
                "horizon": str(horizon),
                "note": "no_signal_price",
                "decision_correct": None,
            }
            by_id[case["id"]] = case
            settled += 1
            continue
        later = _price_after(ticker, due)
        if later is None:
            continue
        scored = score_outcome(
            ta_action=case.get("ta_action"),
            price_at_signal=float(px0),
            price_later=float(later),
        )
        case["outcome"] = {
            "settled_at": datetime.now(timezone.utc).isoformat(),
            "horizon": str(horizon),
            "price_at_signal": float(px0),
            **scored,
        }
        by_id[case["id"]] = case
        settled += 1
        print(
            f"  {ticker}: action={case.get('ta_action')} ret={scored.get('return_pct')}% "
            f"correct={scored.get('decision_correct')}",
            flush=True,
        )

    rewrite_journal(list(by_id.values()), args.journal)
    print(f"Settled {settled}. Journal → {args.journal}", flush=True)

    if args.summary:
        all_rows = read_journal(args.journal)
        done = [r for r in all_rows if r.get("outcome") and r["outcome"].get("decision_correct") is not None]
        if not done:
            print("No scored outcomes yet.")
            return 0
        correct = sum(1 for r in done if r["outcome"]["decision_correct"])
        print(f"Scored cases: {len(done)} | correct: {correct} ({100*correct/len(done):.1f}%)")
        skips = [r for r in done if (r.get("ta_action") or "").upper().find("BUY") < 0 or "DO NOT" in (r.get("ta_action") or "").upper()]
        buys = [r for r in done if "BUY" in (r.get("ta_action") or "").upper() and "DO NOT" not in (r.get("ta_action") or "").upper()]
        if skips:
            sc = sum(1 for r in skips if r["outcome"]["decision_correct"])
            print(f"  Skips/avoids: {sc}/{len(skips)} correct")
        if buys:
            bc = sum(1 for r in buys if r["outcome"]["decision_correct"])
            print(f"  Buys: {bc}/{len(buys)} correct")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
