#!/usr/bin/env python3
"""Score all strategy variants on journal outcomes → lab_leaderboard.json.

Keeps live baseline untouched. Re-run anytime to compare experiments.
Also builds a data-driven `winner_mined` variant from winner_profile hints.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PIPELINE = Path(__file__).resolve().parent
OUT = PIPELINE / "out"
sys.path.insert(0, str(PIPELINE))

from variants import Variant, all_variants  # noqa: E402
from winner_profile import build_profile, load_cases  # noqa: E402


def _clamp_exit(ret: float, v: Variant) -> float:
    if ret >= v.take_profit_pct:
        return v.take_profit_pct
    if ret <= -v.stop_loss_pct:
        return -v.stop_loss_pct
    return ret


def passes(case: dict[str, Any], v: Variant) -> bool:
    pats = set(case.get("patterns") or [])
    if v.require_gap_break and not {"gap_up", "break_prev_day_high"}.issubset(pats):
        return False
    if v.forbid_bear and pats & {"gap_down", "break_prev_day_low"}:
        return False
    if v.require_range_expansion and "range_expansion" not in pats:
        return False
    if v.require_rvol_spike and "rvol_spike" not in pats:
        return False
    rvol = float(case.get("rvol") or 0)
    if rvol < v.min_rvol or rvol > v.max_rvol:
        return False
    px = float(case.get("price") or 0)
    if px < v.min_price or px > v.max_price:
        return False
    if v.max_pe is not None and case.get("pe") is not None:
        try:
            if float(case["pe"]) > v.max_pe:
                return False
        except (TypeError, ValueError):
            pass
    if v.min_mcap is not None and case.get("mcap") is not None:
        try:
            if float(case["mcap"]) < v.min_mcap:
                return False
        except (TypeError, ValueError):
            pass
    if v.max_mcap is not None and case.get("mcap") is not None:
        try:
            if float(case["mcap"]) > v.max_mcap:
                return False
        except (TypeError, ValueError):
            pass
    return True


def _rank_key(case: dict[str, Any], v: Variant) -> float:
    rvol = float(case.get("rvol") or 0)
    score = 100.0 - abs(rvol - v.sweet_rvol) * 12.0
    pats = set(case.get("patterns") or [])
    if "range_expansion" in pats:
        score += 15.0
    if v.min_rvol <= rvol < 4.0:
        score += 8.0
    return score


def score_variant(
    cases: list[dict[str, Any]],
    v: Variant,
    *,
    start_equity: float = 50.0,
) -> dict[str, Any]:
    matched = [c for c in cases if passes(c, v)]
    if not matched:
        return {
            "id": v.id,
            "name": v.name,
            "n": 0,
            "win_rate": None,
            "avg_ret_raw": None,
            "avg_ret_exit": None,
            "expectancy_exit": None,
            "equity_50_raw": start_equity,
            "equity_50_exit": start_equity,
            "pnl_50_exit": 0.0,
            "days": 0,
            "notes": ["no_matches"],
        }

    raw_rets = [float(c["ret"]) for c in matched]
    exit_rets = [_clamp_exit(r, v) for r in raw_rets]
    wins = sum(1 for r in exit_rets if r > 0)
    n = len(exit_rets)
    avg_raw = sum(raw_rets) / n
    avg_exit = sum(exit_rets) / n
    wr = wins / n

    # Day-compounded equity: top_n per day, equal split, exit-clamped
    by_day: dict[str, list[dict]] = defaultdict(list)
    for c in matched:
        by_day[c["day"]].append(c)
    equity = start_equity
    day_pnls: list[float] = []
    for day in sorted(by_day):
        day_cases = sorted(by_day[day], key=lambda c: _rank_key(c, v), reverse=True)[
            : max(1, v.top_n)
        ]
        day_ret = sum(_clamp_exit(float(c["ret"]), v) for c in day_cases) / len(day_cases)
        equity *= 1.0 + day_ret / 100.0
        day_pnls.append(day_ret)

    return {
        "id": v.id,
        "name": v.name,
        "description": v.description,
        "tags": v.tags,
        "params": {
            "min_rvol": v.min_rvol,
            "max_rvol": v.max_rvol,
            "top_n": v.top_n,
            "min_price": v.min_price,
            "max_price": v.max_price,
            "require_range_expansion": v.require_range_expansion,
            "require_rvol_spike": v.require_rvol_spike,
            "take_profit_pct": v.take_profit_pct,
            "stop_loss_pct": v.stop_loss_pct,
            "time_stop_minutes": v.time_stop_minutes,
            "open_window_minutes": v.open_window_minutes,
            "spy_min_chg_pct": v.spy_min_chg_pct,
            "max_pe": v.max_pe,
        },
        "n": n,
        "days": len(by_day),
        "win_rate": round(wr, 3),
        "avg_ret_raw": round(avg_raw, 3),
        "avg_ret_exit": round(avg_exit, 3),
        "expectancy_exit": round(avg_exit, 3),
        "median_day_ret": round(sorted(day_pnls)[len(day_pnls) // 2], 3) if day_pnls else None,
        "equity_50_exit": round(equity, 2),
        "pnl_50_exit": round(equity - start_equity, 2),
        "pnl_50_pct": round((equity / start_equity - 1.0) * 100.0, 2),
    }


def mined_variant(profile: dict[str, Any]) -> Variant:
    hints = profile.get("auto_variant_hints") or {}
    min_rvol = float(hints.get("min_rvol") or 2.5)
    max_rvol = float(hints.get("max_rvol") or 4.0)
    require_range = bool(hints.get("require_range_expansion"))
    # Prefer range OR price band (not both) so sample doesn't collapse
    prefer_px = hints.get("prefer_price_band")
    min_price, max_price = 5.0, 200.0
    px_bands = profile.get("price_bands") or {}
    px_n = (px_bands.get(prefer_px) or {}).get("n") or 0
    if not require_range and prefer_px and px_n >= 8:
        if prefer_px == "10-25":
            min_price, max_price = 10.0, 25.0
        elif prefer_px == "25-50":
            min_price, max_price = 25.0, 50.0
        elif prefer_px == "50-100":
            min_price, max_price = 50.0, 100.0
    return Variant(
        id="winner_mined",
        name="Winner-mined auto",
        description="Auto knobs from winner vs loser trait lift",
        min_rvol=min_rvol,
        max_rvol=max_rvol,
        min_price=min_price,
        max_price=max_price,
        require_range_expansion=require_range,
        top_n=2,
        take_profit_pct=2.25,
        time_stop_minutes=35.0,
        tags=["auto", "mined"],
    )


def run_lab(
    *,
    horizon_minutes: float = 60.0,
    start_equity: float = 50.0,
    out: Path | None = None,
    profile_out: Path | None = None,
) -> dict[str, Any]:
    out = out or (OUT / "lab_leaderboard.json")
    profile_out = profile_out or (OUT / "winner_profile.json")
    cases = load_cases(horizon_minutes=horizon_minutes)
    profile = build_profile(cases)
    profile_out.parent.mkdir(parents=True, exist_ok=True)
    profile_out.write_text(json.dumps(profile, indent=2), encoding="utf-8")

    variants = list(all_variants())
    variants.append(mined_variant(profile))

    results = [score_variant(cases, v, start_equity=start_equity) for v in variants]
    # Rank: prefer expectancy then sample size, penalize tiny n
    def rank_key(r: dict[str, Any]) -> tuple:
        n = r.get("n") or 0
        if n < 5:
            return (-1e9, n, 0.0)
        exp = r.get("expectancy_exit") or -999.0
        pnl = r.get("pnl_50_exit") or -999.0
        return (exp, pnl, n)

    ranked = sorted(results, key=rank_key, reverse=True)
    for i, r in enumerate(ranked, start=1):
        r["rank"] = i

    baseline = next((r for r in ranked if r["id"] == "baseline_v1"), None)
    best = ranked[0] if ranked else None

    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "horizon_minutes": horizon_minutes,
        "start_equity": start_equity,
        "n_cases_universe": len(cases),
        "baseline_id": "baseline_v1",
        "best_id": best.get("id") if best else None,
        "baseline": baseline,
        "best": best,
        "winner_suggestions": profile.get("suggestions"),
        "leaderboard": ranked,
        "note": (
            "Paper study on delayed Yahoo closes — not live fills. "
            "Live rotate stays on baseline_v1 unless you promote a variant."
        ),
    }
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    # Also dump each variant definition for reference
    defs_path = OUT / "variants_catalog.json"
    defs_path.write_text(
        json.dumps([v.to_dict() for v in variants], indent=2),
        encoding="utf-8",
    )
    return payload


def main() -> int:
    p = argparse.ArgumentParser(description="Profit lab: score strategy variants")
    p.add_argument("--horizon-minutes", type=float, default=60.0)
    p.add_argument("--start-equity", type=float, default=50.0)
    p.add_argument("--out", type=Path, default=OUT / "lab_leaderboard.json")
    args = p.parse_args()
    payload = run_lab(
        horizon_minutes=args.horizon_minutes,
        start_equity=args.start_equity,
        out=args.out,
    )
    print(f"cases={payload['n_cases_universe']}  best={payload['best_id']}")
    print(f"suggestions: {payload.get('winner_suggestions')}")
    print("\nLEADERBOARD (exit-clamped, 50 EUR day-compounded):")
    for r in payload["leaderboard"]:
        if r.get("n", 0) == 0:
            print(f"  #{r['rank']:2d} {r['id']:20s}  n=0")
            continue
        mark = " <-- LIVE" if r["id"] == "baseline_v1" else ""
        print(
            f"  #{r['rank']:2d} {r['id']:20s}  n={r['n']:3d}  "
            f"win={r['win_rate']:.0%}  exp={r['avg_ret_exit']:+.2f}%  "
            f"50e→{r['equity_50_exit']:.2f} ({r['pnl_50_exit']:+.2f}){mark}"
        )
    print(f"\nWrote {args.out}")
    print(f"Wrote {OUT / 'winner_profile.json'}")
    print(f"Wrote {OUT / 'variants_catalog.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
