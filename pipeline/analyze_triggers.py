#!/usr/bin/env python3
"""Analyze buy_price_check.json to find weak triggers and simulate filters."""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

OUT = Path(__file__).resolve().parent / "out"
BEAR = {"gap_down", "break_prev_day_low"}


def main() -> int:
    rows = json.loads((OUT / "buy_price_check.json").read_text(encoding="utf-8"))["rows"]
    by_pat: dict = defaultdict(lambda: {"n": 0, "upn": 0, "s2": 0.0, "s10": 0.0, "sn": 0.0})
    by_combo: dict = defaultdict(lambda: {"n": 0, "upn": 0, "sn": 0.0, "tickers": []})
    buckets = {
        "pure_bull": {"n": 0, "s2": 0.0, "s10": 0.0, "sn": 0.0, "upn": 0},
        "mixed_bear": {"n": 0, "s2": 0.0, "s10": 0.0, "sn": 0.0, "upn": 0},
        "rvol_only": {"n": 0, "s2": 0.0, "s10": 0.0, "sn": 0.0, "upn": 0},
    }

    def add(bucket, r):
        bucket["n"] += 1
        for k, rk in [("s2", "ret_2m"), ("s10", "ret_10m"), ("sn", "ret_now")]:
            v = r.get(rk)
            if v is not None:
                bucket[k] += v
        if r.get("ret_now") is not None and r["ret_now"] > 0:
            bucket["upn"] += 1

    for r in rows:
        pats = set(r.get("patterns") or [])
        combo = tuple(sorted(pats))
        for p in pats:
            b = by_pat[p]
            b["n"] += 1
            for rk, sk in [("ret_2m", "s2"), ("ret_10m", "s10"), ("ret_now", "sn")]:
                v = r.get(rk)
                if v is not None:
                    b[sk] += v
            if r.get("ret_now") is not None and r["ret_now"] > 0:
                b["upn"] += 1
        c = by_combo[combo]
        c["n"] += 1
        c["tickers"].append(r["ticker"])
        if r.get("ret_now") is not None:
            c["sn"] += r["ret_now"]
            if r["ret_now"] > 0:
                c["upn"] += 1
        if pats == {"rvol_spike"}:
            add(buckets["rvol_only"], r)
        elif pats & BEAR:
            add(buckets["mixed_bear"], r)
        else:
            add(buckets["pure_bull"], r)

    print("=== BY SINGLE PATTERN ===")
    for p, b in sorted(by_pat.items(), key=lambda x: x[1]["sn"] / max(1, x[1]["n"])):
        n = b["n"]
        print(
            f"  {p:22} n={n:2}  avg2={b['s2']/n:+6.2f}%  avg10={b['s10']/n:+6.2f}%  "
            f"avgNow={b['sn']/n:+6.2f}%  winNow={b['upn']}/{n}"
        )

    print("\n=== BY PATTERN COMBO ===")
    for combo, b in sorted(by_combo.items(), key=lambda x: x[1]["sn"] / max(1, x[1]["n"])):
        n = b["n"]
        name = "+".join(combo) or "(none)"
        print(
            f"  {name:50} n={n:2}  avgNow={b['sn']/n:+6.2f}%  "
            f"win={b['upn']}/{n}  {b['tickers'][:5]}"
        )

    print("\n=== BUCKETS ===")
    for name, b in buckets.items():
        n = b["n"] or 1
        print(
            f"  {name:12} n={b['n']:2}  avg2={b['s2']/n:+6.2f}% avg10={b['s10']/n:+6.2f}% "
            f"avgNow={b['sn']/n:+6.2f}% winNow={b['upn']}/{b['n']}"
        )

    print("\n=== WORST 10 now% ===")
    for r in sorted(rows, key=lambda x: (x.get("ret_now") is None, x.get("ret_now") or 0))[:10]:
        print(f"  {r['ticker']:6} {r.get('ret_now'):+7.2f}%  pats={r.get('patterns')}  rvol={r.get('rvol')}")

    print("\n=== BEST 10 now% ===")
    scored = [x for x in rows if x.get("ret_now") is not None]
    for r in sorted(scored, key=lambda x: -x["ret_now"])[:10]:
        print(f"  {r['ticker']:6} {r.get('ret_now'):+7.2f}%  pats={r.get('patterns')}  rvol={r.get('rvol')}")

    def avg(xs):
        return sum(xs) / len(xs) if xs else 0.0

    def sim(pred, label):
        sel = [r for r in rows if pred(r)]
        if not sel:
            print(f"  {label}: empty")
            return
        sn = [r["ret_now"] for r in sel if r.get("ret_now") is not None]
        s10 = [r["ret_10m"] for r in sel if r.get("ret_10m") is not None]
        win = sum(1 for x in sn if x > 0)
        print(
            f"  {label:42} n={len(sel):2}  avg10={avg(s10):+6.2f}%  "
            f"avgNow={avg(sn):+6.2f}%  winNow={win}/{len(sn)}"
        )

    print("\n=== FILTER SIMULATIONS ===")
    sim(lambda r: True, "baseline all")
    sim(lambda r: not (set(r.get("patterns") or []) & BEAR), "drop gap_down/break_low")
    sim(
        lambda r: "gap_up" in (r.get("patterns") or [])
        and "gap_down" not in (r.get("patterns") or []),
        "require gap_up, no gap_down",
    )
    sim(
        lambda r: "break_prev_day_high" in (r.get("patterns") or [])
        and not (set(r.get("patterns") or []) & BEAR),
        "break_high clean",
    )
    sim(
        lambda r: "gap_up" in (r.get("patterns") or [])
        and "break_prev_day_high" in (r.get("patterns") or []),
        "gap_up AND break_high",
    )
    sim(lambda r: (r.get("rvol") or 0) >= 1.8, "rvol>=1.8")
    sim(lambda r: (r.get("rvol") or 0) >= 2.0, "rvol>=2.0")
    sim(lambda r: (r.get("rvol") or 0) >= 2.5, "rvol>=2.5")
    sim(
        lambda r: "gap_up" in (r.get("patterns") or [])
        and "break_prev_day_high" in (r.get("patterns") or [])
        and (r.get("rvol") or 0) >= 1.5,
        "gap_up+break_high+rvol>=1.5",
    )
    sim(
        lambda r: "gap_up" in (r.get("patterns") or [])
        and "break_prev_day_high" in (r.get("patterns") or [])
        and (r.get("rvol") or 0) >= 1.8,
        "gap_up+break_high+rvol>=1.8",
    )
    sim(
        lambda r: bool(set(r.get("patterns") or []) & {"gap_up", "break_prev_day_high"})
        and not (set(r.get("patterns") or []) & BEAR)
        and (r.get("rvol") or 0) >= 1.5,
        "bull-only + rvol>=1.5",
    )
    sim(
        lambda r: not (set(r.get("patterns") or []) & BEAR)
        and (r.get("rvol") or 0) >= 1.8
        and bool(set(r.get("patterns") or []) & {"gap_up", "break_prev_day_high"}),
        "clean bull + rvol>=1.8",
    )
    sim(
        lambda r: "gap_up" in (r.get("patterns") or [])
        and "break_prev_day_high" in (r.get("patterns") or [])
        and not (set(r.get("patterns") or []) & BEAR)
        and (r.get("rvol") or 0) >= 1.5,
        "BEST candidate: gap_up+break_high clean rvol>=1.5",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
