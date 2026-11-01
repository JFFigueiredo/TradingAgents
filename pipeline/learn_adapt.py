#!/usr/bin/env python3
"""Learn from journal outcomes and write adaptive_config.json for rotate.py.

Conservative nudges only — never blows up filters from tiny samples.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

PIPELINE = Path(__file__).resolve().parent
OUT = PIPELINE / "out"
DEFAULT_CONFIG = OUT / "adaptive_config.json"
sys.path.insert(0, str(PIPELINE))

from journal import DEFAULT_JOURNAL, read_journal  # noqa: E402


BASE = {
    # Journal study: 2.5–4 sweet spot; cut weak 2.2–2.5 and toxic 5–8
    "min_rvol": 2.5,
    "max_rvol": 5.0,
    "top_n": 3,
    "take_profit_pct": 2.25,
    "stop_loss_pct": 1.0,
    "time_stop_minutes": 30.0,
    "open_window_minutes": 90,
    "require_spy_green": True,
    "spy_min_chg_pct": -0.1,
    "require_range_expansion": False,
}


def _parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _later_close(ticker: str, after: datetime, horizon_minutes: float) -> float | None:
    import yfinance as yf

    target = after + timedelta(minutes=horizon_minutes)
    start = after.date()
    end = (target + timedelta(days=2)).date()
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
    future = raw[idx >= target_cmp]
    if future.empty:
        future = raw[idx >= after_cmp]
    if future.empty or "Close" not in future.columns:
        return None
    return float(future["Close"].iloc[0])


def score_cases(
    rows: list[dict[str, Any]],
    *,
    horizon_minutes: float = 60.0,
    min_age_minutes: float = 45.0,
    limit: int = 80,
) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    # earliest per ticker
    # earliest per ticker per day (don't let day-1 shadow day-2)
    by_key: dict[tuple[str, str], dict] = {}
    for r in sorted(rows, key=lambda x: x.get("ts") or ""):
        t = r.get("ticker")
        if not t or r.get("price_at_signal") is None:
            continue
        sources = set(r.get("trigger_sources") or [])
        if not (sources >= {"A", "B"} or (r.get("triggered_by_a") and r.get("triggered_by_b"))):
            continue
        pats = set(r.get("a_patterns") or [])
        if not {"gap_up", "break_prev_day_high"}.issubset(pats):
            continue
        day = str(r.get("ts") or "")[:10]
        key = (str(t).upper(), day)
        if key not in by_key:
            by_key[key] = r
    scored: list[dict[str, Any]] = []
    for (_t, _day), r in list(by_key.items())[-limit:]:
        ts = _parse_ts(r["ts"])
        if now - ts < timedelta(minutes=min_age_minutes):
            continue
        px0 = float(r["price_at_signal"])
        later = _later_close(r["ticker"], ts, horizon_minutes)
        if later is None or px0 <= 0:
            continue
        ret = (later - px0) / px0 * 100.0
        scored.append(
            {
                "ticker": r["ticker"],
                "ret": ret,
                "rvol": r.get("b_rel_volume"),
                "patterns": r.get("a_patterns"),
                "ts": r["ts"],
            }
        )
    return scored


def propose(scored: list[dict[str, Any]], prev: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = dict(BASE)
    if prev:
        for k in BASE:
            if k in prev:
                cfg[k] = prev[k]

    n = len(scored)
    notes: list[str] = []
    if n < 8:
        notes.append(f"sample_too_small n={n}; keep prior/base")
        return {
            **cfg,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "sample_n": n,
            "win_rate": None,
            "avg_return": None,
            "notes": notes,
            "rvol_band_stats": {},
        }

    wins = sum(1 for s in scored if s["ret"] > 0)
    win_rate = wins / n
    avg_ret = sum(s["ret"] for s in scored) / n
    notes.append(f"overall win={wins}/{n} ({100*win_rate:.0f}%) avg={avg_ret:+.2f}%")

    # RVOL bands
    bands = {
        "1.8-2.5": [],
        "2.5-4.0": [],
        "4.0-8.0": [],
        "8.0+": [],
    }
    for s in scored:
        try:
            rv = float(s.get("rvol") or 0)
        except (TypeError, ValueError):
            rv = 0.0
        if rv < 2.5:
            bands["1.8-2.5"].append(s["ret"])
        elif rv < 4.0:
            bands["2.5-4.0"].append(s["ret"])
        elif rv < 8.0:
            bands["4.0-8.0"].append(s["ret"])
        else:
            bands["8.0+"].append(s["ret"])

    band_stats = {}
    best_band = None
    best_score = -1e9
    for name, rets in bands.items():
        if len(rets) < 3:
            band_stats[name] = {"n": len(rets), "avg": None, "win": None}
            continue
        avg = sum(rets) / len(rets)
        wr = sum(1 for x in rets if x > 0) / len(rets)
        band_stats[name] = {"n": len(rets), "avg": round(avg, 3), "win": round(wr, 3)}
        score = avg + (wr - 0.5) * 2
        if score > best_score:
            best_score = score
            best_band = name

    # Push hard into 2.5–4 sweet spot; cut weak skirts
    low = band_stats.get("1.8-2.5") or {}
    mid = band_stats.get("2.5-4.0") or {}
    hi4 = band_stats.get("4.0-8.0") or {}
    hi8 = band_stats.get("8.0+") or {}

    if best_band == "2.5-4.0" or (mid.get("n") or 0) >= 5 and (mid.get("avg") or 0) >= 1.5:
        cfg["min_rvol"] = max(float(cfg["min_rvol"]), 2.5)
        cfg["max_rvol"] = min(float(cfg["max_rvol"]), 5.0)
        notes.append("lock sweet-spot min_rvol≥2.5 max_rvol≤5")
    elif best_band == "4.0-8.0":
        cfg["min_rvol"] = max(float(cfg["min_rvol"]), 2.5)
        notes.append("raise min_rvol→2.5 (higher band leading)")
    elif (low.get("n") or 0) >= 5 and (low.get("avg") or 0) < 0.5:
        cfg["min_rvol"] = max(float(cfg["min_rvol"]), 2.5)
        notes.append("raise min_rvol→2.5 (low band weak)")

    if (hi4.get("n") or 0) >= 5 and (hi4.get("avg") or 0) < 0.5:
        cfg["max_rvol"] = min(float(cfg["max_rvol"]), 4.0)
        notes.append("cap max_rvol→4 (4–8 weak)")
    if (hi8.get("n") or 0) >= 3 and (hi8.get("avg") or 0) < 0.5:
        cfg["max_rvol"] = min(float(cfg["max_rvol"]), 5.0)
        notes.append("cap max_rvol→5 (8+ weak)")

    # Concentrate: strong edge → keep top_n small; weak → even smaller
    if win_rate >= 0.65 and avg_ret > 1.5:
        cfg["top_n"] = min(4, max(int(cfg["top_n"]), 3))
        notes.append("keep concentrated top_n (strong)")
    elif win_rate < 0.55 or avg_ret < 0.8:
        cfg["top_n"] = max(2, int(cfg["top_n"]) - 1)
        notes.append("shrink top_n (weak)")

    # exits: harvest more of mid-band avg when edge is strong
    if win_rate >= 0.6 and avg_ret >= 2.0:
        cfg["take_profit_pct"] = min(2.75, float(cfg["take_profit_pct"]) + 0.15)
        cfg["time_stop_minutes"] = min(35.0, float(cfg["time_stop_minutes"]) + 2)
        notes.append("loosen TP / give time (strong edge)")
    elif avg_ret < 0.5:
        cfg["take_profit_pct"] = max(1.5, float(cfg["take_profit_pct"]) - 0.15)
        cfg["time_stop_minutes"] = max(18.0, float(cfg["time_stop_minutes"]) - 2)
        notes.append("tighter TP / faster time stop")

    if win_rate < 0.5:
        cfg["stop_loss_pct"] = max(0.75, float(cfg["stop_loss_pct"]) - 0.1)
        notes.append("tighter stop")

    # Clamp safety — stay near profit-tuned band
    cfg["min_rvol"] = float(min(3.5, max(2.0, cfg["min_rvol"])))
    cfg["max_rvol"] = float(min(8.0, max(cfg["min_rvol"] + 1.0, cfg["max_rvol"])))
    cfg["top_n"] = int(min(5, max(2, cfg["top_n"])))
    cfg["take_profit_pct"] = float(min(3.0, max(1.25, cfg["take_profit_pct"])))
    cfg["stop_loss_pct"] = float(min(1.5, max(0.5, cfg["stop_loss_pct"])))
    cfg["time_stop_minutes"] = float(min(40.0, max(15.0, cfg["time_stop_minutes"])))
    cfg["open_window_minutes"] = int(cfg.get("open_window_minutes") or 90)
    cfg["spy_min_chg_pct"] = float(cfg.get("spy_min_chg_pct", -0.1))

    return {
        **cfg,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "sample_n": n,
        "win_rate": round(win_rate, 3),
        "avg_return": round(avg_ret, 3),
        "notes": notes,
        "rvol_band_stats": band_stats,
        "best_band": best_band,
    }


def load_config(path: Path | None = None) -> dict[str, Any]:
    path = path or DEFAULT_CONFIG
    if not path.exists():
        return dict(BASE)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return dict(BASE)


def run_adapt(
    *,
    journal: Path | None = None,
    out: Path | None = None,
    horizon_minutes: float = 60.0,
) -> dict[str, Any]:
    out = out or DEFAULT_CONFIG
    prev = load_config(out) if out.exists() else None
    rows = read_journal(journal or DEFAULT_JOURNAL)
    scored = score_cases(rows, horizon_minutes=horizon_minutes)
    cfg = propose(scored, prev)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return cfg


def main() -> int:
    p = argparse.ArgumentParser(description="Adapt rotate params from journal outcomes")
    p.add_argument("--journal", type=Path, default=DEFAULT_JOURNAL)
    p.add_argument("--out", type=Path, default=DEFAULT_CONFIG)
    p.add_argument("--horizon-minutes", type=float, default=60.0)
    args = p.parse_args()
    cfg = run_adapt(journal=args.journal, out=args.out, horizon_minutes=args.horizon_minutes)
    print(json.dumps(cfg, indent=2), flush=True)
    print(f"Wrote {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
