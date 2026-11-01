#!/usr/bin/env python3
"""Mine common traits of winning vs losing journaled quality buys."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

PIPELINE = Path(__file__).resolve().parent
OUT = PIPELINE / "out"
sys.path.insert(0, str(PIPELINE))

from journal import DEFAULT_JOURNAL, read_journal  # noqa: E402

CACHE: dict[str, pd.DataFrame] = {}


def _bars(ticker: str) -> pd.DataFrame | None:
    if ticker in CACHE:
        df = CACHE[ticker]
        return None if df.empty else df
    import yfinance as yf

    end = (datetime.now(timezone.utc) + timedelta(days=1)).date()
    start = end - timedelta(days=14)
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
        CACHE[ticker] = pd.DataFrame()
        return None
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = [c[0] for c in raw.columns]
    CACHE[ticker] = raw
    return raw


def _later_close(ticker: str, after: datetime, horizon_m: float) -> float | None:
    raw = _bars(ticker)
    if raw is None or "Close" not in raw.columns:
        return None
    target = after + timedelta(minutes=horizon_m)
    idx = raw.index
    after_cmp = after.replace(tzinfo=None) if idx.tz is None else after
    target_cmp = target.replace(tzinfo=None) if idx.tz is None else target
    future = raw[idx >= target_cmp]
    if future.empty:
        future = raw[idx >= after_cmp]
    if future.empty:
        return None
    return float(future["Close"].iloc[0])


def _gap_pct(row: dict[str, Any]) -> float | None:
    for h in row.get("a_details") or []:
        if h.get("pattern") == "gap_up" and h.get("gap_pct") is not None:
            try:
                return float(h["gap_pct"])
            except (TypeError, ValueError):
                return None
    return None


def load_cases(
    *,
    horizon_minutes: float = 60.0,
    min_age_minutes: float = 45.0,
) -> list[dict[str, Any]]:
    rows = read_journal(DEFAULT_JOURNAL)
    by_key: dict[tuple[str, str], dict] = {}
    for r in sorted(rows, key=lambda x: x.get("ts") or ""):
        t = str(r.get("ticker") or "").upper()
        ts = r.get("ts") or ""
        if not t or not ts or r.get("price_at_signal") is None:
            continue
        pats = set(r.get("a_patterns") or [])
        if not {"gap_up", "break_prev_day_high"}.issubset(pats):
            continue
        key = (t, ts[:10])
        if key not in by_key:
            by_key[key] = r

    now = datetime.now(timezone.utc)
    cases: list[dict[str, Any]] = []
    for r in by_key.values():
        ts = datetime.fromisoformat(str(r["ts"]).replace("Z", "+00:00"))
        if now - ts < timedelta(minutes=min_age_minutes):
            continue
        px0 = float(r["price_at_signal"])
        later = _later_close(r["ticker"], ts, horizon_minutes)
        if later is None or px0 <= 0:
            continue
        ret = (later - px0) / px0 * 100.0
        try:
            rvol = float(r.get("b_rel_volume") or 0)
        except (TypeError, ValueError):
            rvol = 0.0
        pe = r.get("b_pe_ttm")
        mcap = r.get("b_market_cap")
        try:
            pe_f = float(pe) if pe is not None else None
        except (TypeError, ValueError):
            pe_f = None
        try:
            mcap_f = float(mcap) if mcap is not None else None
        except (TypeError, ValueError):
            mcap_f = None
        pats = set(r.get("a_patterns") or [])
        cases.append(
            {
                "ticker": r["ticker"],
                "ts": r["ts"],
                "day": str(r["ts"])[:10],
                "ret": ret,
                "win": ret > 0,
                "rvol": rvol,
                "price": px0,
                "pe": pe_f,
                "mcap": mcap_f,
                "gap_pct": _gap_pct(r),
                "patterns": sorted(pats),
                "has_range": "range_expansion" in pats,
                "has_rvol_spike": "rvol_spike" in pats,
            }
        )
    return cases


def _avg(xs: list[float]) -> float | None:
    return round(sum(xs) / len(xs), 3) if xs else None


def _rate(xs: list[bool]) -> float | None:
    return round(sum(1 for x in xs if x) / len(xs), 3) if xs else None


def build_profile(cases: list[dict[str, Any]]) -> dict[str, Any]:
    wins = [c for c in cases if c["win"]]
    losses = [c for c in cases if not c["win"]]

    def pattern_lift() -> dict[str, Any]:
        out = {}
        all_pats = sorted({p for c in cases for p in c["patterns"]})
        for p in all_pats:
            w_has = [c for c in wins if p in c["patterns"]]
            l_has = [c for c in losses if p in c["patterns"]]
            base_win = len(wins) / len(cases) if cases else 0
            cond = cases and len([c for c in cases if p in c["patterns"]])
            if not cond:
                continue
            cond_n = [c for c in cases if p in c["patterns"]]
            cond_wr = sum(1 for c in cond_n if c["win"]) / len(cond_n)
            out[p] = {
                "n": len(cond_n),
                "win_rate": round(cond_wr, 3),
                "lift_vs_base": round(cond_wr - base_win, 3),
                "win_count": len(w_has),
                "loss_count": len(l_has),
            }
        return dict(sorted(out.items(), key=lambda kv: kv[1]["lift_vs_base"], reverse=True))

    def band_stats(key: str, edges: list[tuple[float, float, str]]) -> dict[str, Any]:
        out = {}
        for lo, hi, name in edges:
            sub = [c for c in cases if c.get(key) is not None and lo <= float(c[key]) < hi]
            if not sub:
                out[name] = {"n": 0}
                continue
            out[name] = {
                "n": len(sub),
                "win_rate": _rate([c["win"] for c in sub]),
                "avg_ret": _avg([c["ret"] for c in sub]),
            }
        return out

    rvol_bands = band_stats(
        "rvol",
        [
            (0, 2.0, "<2"),
            (2.0, 2.5, "2-2.5"),
            (2.5, 3.5, "2.5-3.5"),
            (3.5, 4.0, "3.5-4"),
            (4.0, 5.0, "4-5"),
            (5.0, 8.0, "5-8"),
            (8.0, 99, "8+"),
        ],
    )
    price_bands = band_stats(
        "price",
        [
            (0, 10, "<10"),
            (10, 25, "10-25"),
            (25, 50, "25-50"),
            (50, 100, "50-100"),
            (100, 1000, "100+"),
        ],
    )
    gap_bands = band_stats(
        "gap_pct",
        [
            (0, 2, "gap<2"),
            (2, 4, "gap2-4"),
            (4, 8, "gap4-8"),
            (8, 100, "gap8+"),
        ],
    )

    # Suggested experiment knobs from winner/loser contrast
    suggestions: list[str] = []
    best_rvol = None
    best_score = -1e9
    for name, st in rvol_bands.items():
        if st.get("n", 0) < 5 or st.get("avg_ret") is None:
            continue
        score = float(st["avg_ret"]) + (float(st["win_rate"]) - 0.5) * 2
        if score > best_score:
            best_score = score
            best_rvol = name
    if best_rvol:
        suggestions.append(f"favor RVOL band {best_rvol}")

    pat_lift = pattern_lift()
    for p, st in list(pat_lift.items())[:3]:
        if st["n"] >= 8 and st["lift_vs_base"] >= 0.05:
            suggestions.append(f"prefer pattern {p} (lift {st['lift_vs_base']:+.2f})")

    # price sweet spot
    best_px = None
    best_px_score = -1e9
    for name, st in price_bands.items():
        if st.get("n", 0) < 5 or st.get("avg_ret") is None:
            continue
        score = float(st["avg_ret"])
        if score > best_px_score:
            best_px_score = score
            best_px = name
    if best_px:
        suggestions.append(f"favor price band {best_px}")

    win_rvols = [c["rvol"] for c in wins if c["rvol"]]
    loss_rvols = [c["rvol"] for c in losses if c["rvol"]]
    win_prices = [c["price"] for c in wins]
    loss_prices = [c["price"] for c in losses]

    profile = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "n_cases": len(cases),
        "n_wins": len(wins),
        "n_losses": len(losses),
        "base_win_rate": _rate([c["win"] for c in cases]),
        "base_avg_ret": _avg([c["ret"] for c in cases]),
        "winners": {
            "avg_rvol": _avg(win_rvols),
            "avg_price": _avg(win_prices),
            "avg_gap_pct": _avg([c["gap_pct"] for c in wins if c["gap_pct"] is not None]),
            "avg_ret": _avg([c["ret"] for c in wins]),
            "pct_with_range": _rate([c["has_range"] for c in wins]),
            "pct_with_rvol_spike": _rate([c["has_rvol_spike"] for c in wins]),
            "top_patterns": Counter(
                p for c in wins for p in c["patterns"]
            ).most_common(8),
        },
        "losers": {
            "avg_rvol": _avg(loss_rvols),
            "avg_price": _avg(loss_prices),
            "avg_gap_pct": _avg([c["gap_pct"] for c in losses if c["gap_pct"] is not None]),
            "avg_ret": _avg([c["ret"] for c in losses]),
            "pct_with_range": _rate([c["has_range"] for c in losses]),
            "pct_with_rvol_spike": _rate([c["has_rvol_spike"] for c in losses]),
        },
        "pattern_lift": pat_lift,
        "rvol_bands": rvol_bands,
        "price_bands": price_bands,
        "gap_bands": gap_bands,
        "suggestions": suggestions,
        "auto_variant_hints": {
            "min_rvol": 2.5 if best_rvol in {"2.5-3.5", "3.5-4"} else 2.5,
            "max_rvol": 3.5 if best_rvol == "2.5-3.5" else (4.0 if best_rvol == "3.5-4" else 5.0),
            "require_range_expansion": bool(
                wins
                and (_rate([c["has_range"] for c in wins]) or 0)
                > (_rate([c["has_range"] for c in losses]) or 0) + 0.1
            ),
            "prefer_price_band": best_px,
        },
    }
    return profile


def main() -> int:
    p = argparse.ArgumentParser(description="Winner/loser trait miner")
    p.add_argument("--horizon-minutes", type=float, default=60.0)
    p.add_argument("--out", type=Path, default=OUT / "winner_profile.json")
    args = p.parse_args()
    cases = load_cases(horizon_minutes=args.horizon_minutes)
    profile = build_profile(cases)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(profile, indent=2), encoding="utf-8")
    print(json.dumps({k: profile[k] for k in profile if k not in ("pattern_lift",)}, indent=2))
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
