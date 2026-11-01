"""Merge scanner A (patterns) and B (tvscreener) into a shortlist."""
from __future__ import annotations

from typing import Any, Literal


Mode = Literal["and", "or"]

# Tuned from live journal 60m outcomes (n≈85, 2026-10-28..30):
# - gap_up + break_prev_day_high required; gap alone toxic
# - sweet spot RVOL 2.5–4 ≈ +2.8% / 72% win; 2.2–2.5 and 5–8 weak/negative
# - +range_expansion lifts win-rate (~80%) with slightly lower avg
REQUIRED_BUY_PATTERNS = frozenset({"gap_up", "break_prev_day_high"})
FORBIDDEN_BUY_PATTERNS = frozenset({"gap_down", "break_prev_day_low"})
BONUS_BUY_PATTERNS = frozenset({"range_expansion"})


def is_quality_buy(
    row: dict[str, Any],
    *,
    min_rvol: float = 2.5,
    max_rvol: float = 5.0,
    min_price: float = 5.0,
    max_price: float = 200.0,
    require_range_expansion: bool = False,
) -> bool:
    """Return True if this shortlist row passes the tuned long-buy filter."""
    pats = set(row.get("patterns") or [])
    if not REQUIRED_BUY_PATTERNS.issubset(pats):
        return False
    if pats & FORBIDDEN_BUY_PATTERNS:
        return False
    if require_range_expansion and not (pats & BONUS_BUY_PATTERNS):
        return False
    rvol = row.get("rel_volume")
    try:
        rvol_f = float(rvol) if rvol is not None else 0.0
    except (TypeError, ValueError):
        rvol_f = 0.0
    if rvol_f < min_rvol or rvol_f > max_rvol:
        return False
    px = row.get("price")
    try:
        px_f = float(px) if px is not None else None
    except (TypeError, ValueError):
        px_f = None
    if px_f is not None and (px_f < min_price or px_f > max_price):
        return False
    return True


def filter_quality_buys(
    shortlist: list[dict[str, Any]],
    *,
    min_rvol: float = 2.5,
    max_rvol: float = 5.0,
    min_price: float = 5.0,
    max_price: float = 200.0,
    require_range_expansion: bool = False,
) -> list[dict[str, Any]]:
    return [
        r
        for r in shortlist
        if is_quality_buy(
            r,
            min_rvol=min_rvol,
            max_rvol=max_rvol,
            min_price=min_price,
            max_price=max_price,
            require_range_expansion=require_range_expansion,
        )
    ]


def merge_shortlist(
    pattern_hits: list[dict[str, Any]],
    tv_rows: list[dict[str, Any]],
    mode: Mode = "and",
) -> list[dict[str, Any]]:
    """Return shortlist rows with reasons from A and/or B."""
    by_pattern: dict[str, list[dict[str, Any]]] = {}
    for hit in pattern_hits:
        t = str(hit.get("ticker") or hit.get("sym") or "").upper()
        if not t:
            continue
        by_pattern.setdefault(t, []).append(hit)

    by_tv: dict[str, dict[str, Any]] = {}
    for row in tv_rows:
        t = str(row.get("ticker") or "").upper()
        if t:
            by_tv[t] = row

    a = set(by_pattern)
    b = set(by_tv)
    if mode == "and":
        keep = a & b
    elif mode == "or":
        keep = a | b
    else:
        raise ValueError(f"mode must be and|or, got {mode!r}")

    out: list[dict[str, Any]] = []
    for t in sorted(keep):
        patterns = by_pattern.get(t, [])
        pattern_names = sorted(
            {
                str(p.get("pattern") or p.get("name") or "pattern")
                for p in patterns
            }
        )
        tv = by_tv.get(t, {})
        reasons = []
        if patterns:
            reasons.append("pattern:" + ",".join(pattern_names))
        if tv:
            rv = tv.get("rel_volume")
            pe = tv.get("pe_ttm")
            reasons.append(
                f"tv:rvol={rv if rv is not None else '?'} pe={pe if pe is not None else '?'}"
            )
        out.append(
            {
                "ticker": t,
                "reasons": reasons,
                "patterns": pattern_names,
                "rel_volume": tv.get("rel_volume"),
                "pe_ttm": tv.get("pe_ttm"),
                "market_cap": tv.get("market_cap"),
                "price": tv.get("price"),
                "passed_pattern": t in a,
                "passed_tvscreener": t in b,
            }
        )
    return out
