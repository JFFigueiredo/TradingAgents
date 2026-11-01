"""Analyst — score survivors 0..1 with control weights/formulas."""
from __future__ import annotations

import math
from typing import Any

from desk.state import bump


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def score_liquidity(liquidity_usd: float) -> float:
    # log10(liq) scaled so $10k -> 0 and $500k -> 1
    lo = math.log10(10_000.0)
    hi = math.log10(500_000.0)
    return clamp((math.log10(max(liquidity_usd, 1.0)) - lo) / (hi - lo))


def score_momentum(cand: dict[str, Any]) -> float:
    v5 = float(cand.get("volume_5m_usd") or 0.0)
    v1 = float(cand.get("volume_1h_usd") or 0.0)
    v6 = float(cand.get("volume_6h_usd") or 0.0)
    if v1 <= 0:
        return 0.0
    short = clamp((v5 * 12.0 / v1) / 1.5)
    # 1h vs average hourly over 6h
    hourly6 = (v6 / 6.0) if v6 > 0 else v1
    medium = clamp((v1 / max(hourly6, 1e-9)) / 3.0)
    m = 0.6 * short + 0.4 * medium
    chg = cand.get("price_change_1h_pct")
    if chg is not None and float(chg) > 300.0:
        m *= 0.5
    return clamp(m)


def score_holders(cand: dict[str, Any]) -> float:
    top10 = float(cand.get("top10_pct") or 20.0)
    dist = 0.6 * (1.0 - top10 / 20.0)
    hc = cand.get("holder_count")
    if hc is None:
        count_part = 0.3
    else:
        count_part = clamp((math.log10(max(float(hc), 1.0)) - 2.0) / 2.0)
    return clamp(dist + 0.4 * count_part)


def score_age(age_hours: float) -> float:
    # age feature uses minutes internally per spec
    age_min = age_hours * 60.0
    if age_min < 10:
        return clamp((age_min / 10.0) * 0.5)
    if age_min < 30:
        # 10 -> 0.5, 30 -> 1.0
        return clamp(0.5 + (age_min - 10.0) / 20.0 * 0.5)
    if age_min <= 360:  # 6h
        return 1.0
    # decay linearly toward 0.3 at 72h
    # 6h=360min -> 1.0; 72h=4320min -> 0.3
    t = (age_min - 360.0) / (4320.0 - 360.0)
    return clamp(1.0 + t * (0.3 - 1.0))


def score_buy_sell(cand: dict[str, Any]) -> float:
    buys = float(cand.get("buys_1h") or 0.0)
    sells = float(cand.get("sells_1h") or 0.0)
    total = buys + sells
    if total <= 0:
        return 0.0
    share = buys / total  # 0..1
    # 40% -> 0, 70% -> 1
    return clamp((share - 0.40) / 0.30)


def score_candidate(cand: dict[str, Any], cfg: dict[str, Any]) -> float:
    w = cfg.get("score_weights") or {}
    wl = float(w.get("liquidity", 0.25))
    wm = float(w.get("momentum", 0.20))
    wh = float(w.get("holders", 0.20))
    wa = float(w.get("age", 0.15))
    wb = float(w.get("buy_sell", 0.20))
    age_h = float(cand.get("age_hours") or 0.0)
    s = (
        wl * score_liquidity(float(cand.get("liquidity_usd") or 0.0))
        + wm * score_momentum(cand)
        + wh * score_holders(cand)
        + wa * score_age(age_h)
        + wb * score_buy_sell(cand)
    )
    return clamp(s)


def run_analyst(cfg: dict[str, Any], state: dict[str, Any], passed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    thr = float(cfg.get("score_threshold", 0.6))
    scored: list[dict[str, Any]] = []
    for cand in passed:
        bump(state, "scored")
        s = score_candidate(cand, cfg)
        cand["score"] = s
        if s >= thr:
            scored.append(cand)
        else:
            bump(state, "score_under_threshold")
    scored.sort(key=lambda c: float(c.get("score") or 0.0), reverse=True)
    return scored
