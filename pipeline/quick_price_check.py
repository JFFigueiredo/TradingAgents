#!/usr/bin/env python3
"""Quick price check for current BUY list: signal, +2m, +10m, now + exit rules."""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

OUT = Path(__file__).resolve().parent / "out"
sys.path.insert(0, str(Path(__file__).resolve().parent))
from trade_rules import ExitPlan, simulate_exit  # noqa: E402


def parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def bar_at(df: pd.DataFrame, target: datetime):
    if df is None or df.empty or "Close" not in df.columns:
        return None
    idx = df.index
    tgt = target
    if idx.tz is None and tgt.tzinfo is not None:
        tgt = tgt.replace(tzinfo=None)
    elif idx.tz is not None and tgt.tzinfo is None:
        tgt = tgt.replace(tzinfo=timezone.utc)
    future = df[idx >= tgt]
    if not future.empty:
        return float(future["Close"].iloc[0]), str(future.index[0])
    past = df[idx <= tgt]
    if not past.empty:
        return float(past["Close"].iloc[-1]), str(past.index[-1])
    return None


def main() -> int:
    data = json.loads((OUT / "buys_latest.json").read_text(encoding="utf-8"))
    buys = data.get("buys") or []
    tickers = [b["ticker"] for b in buys]
    buy_meta = {b["ticker"]: b for b in buys}
    print(f"Buys: {len(tickers)} @ {data.get('created_at')}", flush=True)

    signals: dict[str, dict] = {}
    journal = OUT / "decision_journal.jsonl"
    if journal.exists():
        for line in journal.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = r.get("ticker")
            if t not in tickers or r.get("price_at_signal") is None:
                continue
            if t not in signals or r["ts"] < signals[t]["ts"]:
                signals[t] = r

    now = datetime.now(timezone.utc)
    rows = []
    for t in tickers:
        sig = signals.get(t)
        if sig:
            px0 = float(sig["price_at_signal"])
            ts0 = parse_ts(sig["ts"])
        else:
            px0 = buy_meta[t].get("price")
            px0 = float(px0) if px0 is not None else None
            ts0 = parse_ts(data["created_at"]) if data.get("created_at") else now - timedelta(minutes=30)

        start = (ts0 - timedelta(hours=1)).date().isoformat()
        end = (now + timedelta(days=1)).date().isoformat()
        raw = yf.download(
            t, start=start, end=end, interval="1m", auto_adjust=True, progress=False, threads=False
        )
        if raw is not None and not raw.empty and isinstance(raw.columns, pd.MultiIndex):
            raw.columns = [c[0] for c in raw.columns]

        p2 = p10 = pnow = None
        exit_res = None
        if raw is not None and not raw.empty:
            r2 = bar_at(raw, ts0 + timedelta(minutes=2))
            r10 = bar_at(raw, ts0 + timedelta(minutes=10))
            rn = bar_at(raw, now)
            if r2:
                p2 = r2[0]
            if r10:
                p10 = r10[0]
            if rn:
                pnow = rn[0]
            if px0 is None:
                r0 = bar_at(raw, ts0)
                if r0:
                    px0 = r0[0]

            # Build 1m path for exit-rule simulation (up to ~60m)
            if px0 is not None:
                plan = ExitPlan()
                ep = buy_meta[t].get("exit_plan") or {}
                if ep.get("take_profit") and px0:
                    # recover pct from absolute targets if present
                    plan = ExitPlan(
                        take_profit_pct=float(ep.get("take_profit_pct") or plan.take_profit_pct),
                        stop_loss_pct=float(ep.get("stop_loss_pct") or plan.stop_loss_pct),
                        time_stop_minutes=float(
                            ep.get("time_stop_minutes") or plan.time_stop_minutes
                        ),
                        confirm_minutes=float(ep.get("confirm_minutes") or plan.confirm_minutes),
                    )
                path = []
                idx = raw.index
                after = ts0
                if idx.tz is None and after.tzinfo is not None:
                    after_cmp = after.replace(tzinfo=None)
                else:
                    after_cmp = after
                future = raw[idx >= after_cmp]
                for i, (ts, bar) in enumerate(future.iterrows()):
                    if i > 90:
                        break
                    mins = (ts.to_pydatetime().replace(tzinfo=after.tzinfo) - after).total_seconds() / 60.0
                    if mins < 0:
                        continue
                    path.append((mins, float(bar["Close"])))
                if path:
                    exit_res = simulate_exit(float(px0), path, plan)

        def ret(p):
            if px0 and p is not None:
                return (p - px0) / px0 * 100.0
            return None

        rows.append(
            {
                "ticker": t,
                "signal_ts": ts0.isoformat(),
                "px0": px0,
                "px_2m": p2,
                "ret_2m": ret(p2),
                "px_10m": p10,
                "ret_10m": ret(p10),
                "px_now": pnow,
                "ret_now": ret(pnow),
                "patterns": buy_meta[t].get("patterns"),
                "rvol": buy_meta[t].get("rel_volume"),
                "size_mult": buy_meta[t].get("size_mult"),
                "rank": buy_meta[t].get("rank"),
                "exit_sim": exit_res,
            }
        )
        time.sleep(0.12)

    print(
        f"\n{'TKR':6} {'SIG':>8} {'+2m':>8} {'+10m':>8} {'NOW':>8}  "
        f"{'r2%':>7} {'r10%':>7} {'rNow%':>7} {'exit%':>7} {'why':12} sz",
        flush=True,
    )
    print("-" * 110, flush=True)

    ups2 = ups10 = upsn = 0
    n2 = n10 = nn = 0
    exit_rets = []
    for r in rows:
        def fmt(p):
            return f"{p:8.2f}" if p is not None else f"{'n/a':>8}"

        def fmtr(x):
            return f"{x:+7.2f}" if x is not None else f"{'n/a':>7}"

        ex = r.get("exit_sim") or {}
        er = ex.get("return_pct")
        if er is not None:
            exit_rets.append(er)
        print(
            f"{r['ticker']:6} {fmt(r['px0'])} {fmt(r['px_2m'])} {fmt(r['px_10m'])} {fmt(r['px_now'])}  "
            f"{fmtr(r['ret_2m'])} {fmtr(r['ret_10m'])} {fmtr(r['ret_now'])} "
            f"{fmtr(er)} {(ex.get('reason') or 'n/a'):12} "
            f"×{r.get('size_mult') if r.get('size_mult') is not None else '-'}",
            flush=True,
        )
        if r["ret_2m"] is not None:
            n2 += 1
            ups2 += r["ret_2m"] > 0
        if r["ret_10m"] is not None:
            n10 += 1
            ups10 += r["ret_10m"] > 0
        if r["ret_now"] is not None:
            nn += 1
            upsn += r["ret_now"] > 0

    def wr(u, n):
        return f"{u}/{n} ({100 * u / n:.0f}%)" if n else "n/a"

    def avg(key):
        vals = [r[key] for r in rows if r[key] is not None]
        return sum(vals) / len(vals) if vals else None

    print("\n===== SUMMARY (price UP vs signal) =====", flush=True)
    print(f"  +2m:  {wr(ups2, n2)}", flush=True)
    print(f"  +10m: {wr(ups10, n10)}", flush=True)
    print(f"  now:  {wr(upsn, nn)}", flush=True)
    for label, key in [("avg +2m%", "ret_2m"), ("avg +10m%", "ret_10m"), ("avg now%", "ret_now")]:
        a = avg(key)
        print(f"  {label}: {a:+.3f}%" if a is not None else f"  {label}: n/a", flush=True)
    if exit_rets:
        ew = sum(1 for x in exit_rets if x > 0)
        print(
            f"  exit-rules: {ew}/{len(exit_rets)} wins | avg {sum(exit_rets)/len(exit_rets):+.3f}%",
            flush=True,
        )

    out = OUT / "buy_price_check.json"
    out.write_text(
        json.dumps(
            {"checked_at": now.isoformat(), "buys_created_at": data.get("created_at"), "rows": rows},
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    print(f"\nWrote {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
