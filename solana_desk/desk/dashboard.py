"""Terminal dashboard (Rich when available; plain text fallback)."""
from __future__ import annotations

from typing import Any

from desk.state import equity_usd


def _wins_losses(state: dict[str, Any]) -> tuple[int, int]:
    wins = losses = 0
    for t in state.get("closed_trades") or []:
        if float(t.get("pnl_usd") or 0) >= 0:
            wins += 1
        else:
            losses += 1
    return wins, losses


def render_text(state: dict[str, Any], cfg: dict[str, Any]) -> str:
    start = float(state.get("starting_cash_usd") or cfg.get("starting_cash_usd") or 280)
    eq = equity_usd(state)
    pnl = eq - start
    pnl_pct = (pnl / start * 100.0) if start else 0.0
    wins, losses = _wins_losses(state)
    c = state.get("counters") or {}
    lines = [
        "=" * 72,
        f"MEME DESK (PAPER)  config={cfg.get('id', '?')}  chains={cfg.get('chains', ['solana'])}  {cfg.get('name', '')}",
        f"Equity ${eq:.2f}  vs start ${start:.2f}  ({pnl:+.2f} / {pnl_pct:+.1f}%)",
        f"Cash ${float(state.get('cash_usd') or 0):.2f}  Open {len(state.get('positions') or [])}  "
        f"Closed {len(state.get('closed_trades') or [])}  W/L {wins}/{losses}",
        (
            "Funnel  scanned={pairs_scanned} eval={evaluations} killed={filter_killed} "
            "passed={filter_passed} scored={scored} under={score_under_threshold} traded={traded} "
            "overshoots={stop_overshoots}"
        ).format(**{k: c.get(k, 0) for k in (
            "pairs_scanned", "evaluations", "filter_killed", "filter_passed",
            "scored", "score_under_threshold", "traded", "stop_overshoots",
        )}),
        "-" * 72,
    ]
    for p in state.get("positions") or []:
        mark = float(p.get("mark_price") or p.get("entry_fill_price") or 0)
        entry = float(p.get("entry_fill_price") or 0)
        pct = (mark / entry - 1.0) * 100.0 if entry else 0.0
        lines.append(
            f"  OPEN {p.get('symbol') or p['mint'][:8]}  "
            f"entry={entry:.8g} mark={mark:.8g} ({pct:+.1f}%)  "
            f"val=${float(p.get('mark_value_usd') or 0):.2f}  "
            f"trail={'Y' if p.get('trail_active') else 'n'}"
        )
    if not state.get("positions"):
        lines.append("  (no open positions)")
    lines.append("-" * 72)
    rr = state.get("reject_reasons") or {}
    if rr:
        top = sorted(rr.items(), key=lambda kv: -kv[1])[:12]
        lines.append("Rejects: " + ", ".join(f"{k}={v}" for k, v in top))
    else:
        lines.append("Rejects: (none yet)")
    lines.append("=" * 72)
    return "\n".join(lines)


def print_dashboard(state: dict[str, Any], cfg: dict[str, Any], *, headless: bool) -> None:
    text = render_text(state, cfg)
    if headless:
        # still useful as a heartbeat line occasionally — caller decides
        return
    try:
        from rich.console import Console
        from rich.panel import Panel

        Console().print(Panel(text, title="Paper Desk", border_style="cyan"))
    except Exception:  # noqa: BLE001
        print(text)
