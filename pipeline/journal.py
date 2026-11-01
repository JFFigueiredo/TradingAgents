"""Append-only decision journal for scanner hits + TradingAgents calls.

Logs every positive A and/or B trigger (even when TA says NO) so you can later
settle prices and measure whether skips/buys were right.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_JOURNAL = Path(__file__).resolve().parent / "out" / "decision_journal.jsonl"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, default=str) + "\n")


def read_journal(path: Path | None = None) -> list[dict[str, Any]]:
    path = path or DEFAULT_JOURNAL
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def build_cases(
    pattern_hits: list[dict[str, Any]],
    tv_rows: list[dict[str, Any]],
    *,
    run_id: str | None = None,
    scanner_a: str = "daytrade",
) -> list[dict[str, Any]]:
    """One case per ticker that triggered A and/or B (union)."""
    by_a: dict[str, list[dict[str, Any]]] = {}
    for hit in pattern_hits:
        t = str(hit.get("ticker") or hit.get("sym") or "").upper()
        if t:
            by_a.setdefault(t, []).append(hit)

    by_b: dict[str, dict[str, Any]] = {}
    for row in tv_rows:
        t = str(row.get("ticker") or "").upper()
        if t:
            by_b[t] = row

    run_id = run_id or uuid.uuid4().hex[:12]
    cases: list[dict[str, Any]] = []
    for t in sorted(set(by_a) | set(by_b)):
        a_hits = by_a.get(t, [])
        b = by_b.get(t, {})
        sources = []
        if a_hits:
            sources.append("A")
        if b:
            sources.append("B")
        patterns = sorted(
            {str(h.get("pattern") or h.get("name") or "pattern") for h in a_hits}
        )
        price = None
        if b.get("price") is not None:
            price = b.get("price")
        elif a_hits:
            price = a_hits[0].get("price")

        cases.append(
            {
                "id": f"{run_id}-{t}-{uuid.uuid4().hex[:8]}",
                "run_id": run_id,
                "ts": _now(),
                "ticker": t,
                "price_at_signal": price,
                "trigger_sources": sources,
                "triggered_by_a": bool(a_hits),
                "triggered_by_b": bool(b),
                "a_patterns": patterns,
                "a_details": a_hits,
                "b_rel_volume": b.get("rel_volume"),
                "b_pe_ttm": b.get("pe_ttm"),
                "b_market_cap": b.get("market_cap"),
                "b_details": b or None,
                "scanner_a": scanner_a,
                "ta_ran": False,
                "ta_action": None,
                "ta_rating": None,
                "ta_usage": None,
                "ta_error": None,
                "outcome": None,
            }
        )
    return cases


def log_cases(cases: list[dict[str, Any]], path: Path | None = None) -> Path:
    path = path or DEFAULT_JOURNAL
    for case in cases:
        _append(path, case)
    return path


def attach_ta_results(
    cases: list[dict[str, Any]],
    ta_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge TradingAgents batch results into in-memory cases (by ticker)."""
    by_ticker = {str(r.get("ticker") or "").upper(): r for r in ta_results}
    updated = []
    for case in cases:
        t = case["ticker"]
        row = dict(case)
        if t in by_ticker:
            r = by_ticker[t]
            row["ta_ran"] = True
            row["ta_action"] = r.get("action")
            row["ta_rating"] = r.get("rating")
            row["ta_usage"] = r.get("usage")
            row["ta_error"] = r.get("error")
            row["ta_ts"] = _now()
        updated.append(row)
    return updated


def rewrite_journal(rows: list[dict[str, Any]], path: Path | None = None) -> None:
    """Full rewrite (used after settling outcomes)."""
    path = path or DEFAULT_JOURNAL
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, default=str) + "\n")
    tmp.replace(path)


def score_outcome(
    *,
    ta_action: str | None,
    price_at_signal: float,
    price_later: float,
    side: str = "long",
) -> dict[str, Any]:
    """Did the TA decision look right after the fact?

    For long bias:
    - BUY/Overweight correct if later price > signal price
    - DO NOT BUY / HOLD correct if later price <= signal price (avoided loss / no edge)
    """
    if price_at_signal is None or price_at_signal == 0 or price_later is None:
        return {"return_pct": None, "decision_correct": None, "note": "missing_price"}

    ret = (price_later - float(price_at_signal)) / float(price_at_signal) * 100.0
    action = (ta_action or "").upper()
    bullish = "BUY" in action and "DO NOT" not in action
    bearish_or_skip = (
        "DO NOT" in action
        or "HOLD" in action
        or "REDUCE" in action
        or "UNDERWEIGHT" in action
        or "SELL" in action
        or not action
    )

    if side != "long":
        ret = -ret

    if bullish:
        correct = ret > 0
        label = "buy_followed"
    elif bearish_or_skip:
        # Skipping a long was "correct" if it wouldn't have paid
        correct = ret <= 0
        label = "skip_or_avoid"
    else:
        correct = None
        label = "unknown_action"

    return {
        "return_pct": round(ret, 4),
        "decision_correct": correct,
        "eval_label": label,
        "price_later": price_later,
    }
