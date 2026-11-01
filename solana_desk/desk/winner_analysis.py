"""Compare closed trades across variant data dirs; profile what winners share."""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_states() -> list[tuple[str, dict[str, Any]]]:
    data = ROOT / "data"
    out: list[tuple[str, dict[str, Any]]] = []
    if not data.is_dir():
        return out
    for d in sorted(data.iterdir()):
        sp = d / "state.json"
        if not sp.is_file():
            continue
        try:
            with sp.open(encoding="utf-8") as f:
                out.append((d.name, json.load(f)))
        except (OSError, json.JSONDecodeError):
            continue
    return out


def _num(trades: list[dict], key: str, nested: str | None = None) -> list[float]:
    vals: list[float] = []
    for t in trades:
        src = t.get(nested) if nested else t
        if not isinstance(src, dict):
            continue
        v = src.get(key)
        if v is None:
            continue
        try:
            vals.append(float(v))
        except (TypeError, ValueError):
            pass
    return vals


def _mean(xs: list[float]) -> float | None:
    return statistics.mean(xs) if xs else None


def summarize_variant(name: str, state: dict[str, Any]) -> dict[str, Any]:
    start = float(state.get("starting_cash_usd") or 280)
    cash = float(state.get("cash_usd") or 0)
    # approximate equity = cash + mark of opens
    eq = cash
    for p in state.get("positions") or []:
        eq += float(p.get("mark_value_usd") or p.get("cost_usd") or 0)
    closed = list(state.get("closed_trades") or [])
    wins = [t for t in closed if float(t.get("pnl_usd") or 0) >= 0]
    losses = [t for t in closed if float(t.get("pnl_usd") or 0) < 0]
    snap_keys = ("liquidity_usd", "volume_1h_usd", "top10_pct", "age_hours")
    win_profile = {k: _mean(_num(wins, k, "entry_snapshot")) for k in snap_keys}
    loss_profile = {k: _mean(_num(losses, k, "entry_snapshot")) for k in snap_keys}
    win_scores = _mean(_num(wins, "score"))
    loss_scores = _mean(_num(losses, "score"))
    reasons: dict[str, int] = {}
    for t in closed:
        r = str(t.get("reason") or "?")
        reasons[r] = reasons.get(r, 0) + 1
    return {
        "variant": name,
        "equity": round(eq, 2),
        "pnl_usd": round(eq - start, 2),
        "pnl_pct": round((eq - start) / start * 100.0, 2) if start else 0.0,
        "closed": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(closed) * 100.0, 1) if closed else None,
        "avg_win_usd": round(_mean([float(t["pnl_usd"]) for t in wins]) or 0, 2) if wins else None,
        "avg_loss_usd": round(_mean([float(t["pnl_usd"]) for t in losses]) or 0, 2) if losses else None,
        "stop_overshoots": (state.get("counters") or {}).get("stop_overshoots", 0),
        "exit_reasons": reasons,
        "winner_entry_avg": win_profile,
        "loser_entry_avg": loss_profile,
        "winner_avg_score": win_scores,
        "loser_avg_score": loss_scores,
    }


def _open_profile() -> dict[str, Any]:
    snaps: list[dict] = []
    chain_counts: dict[str, int] = {}
    for _name, state in _load_states():
        for p in state.get("positions") or []:
            snap = dict(p.get("entry_snapshot") or {})
            snap["score"] = p.get("score")
            ch = str(snap.get("chain") or p.get("chain") or "solana")
            chain_counts[ch] = chain_counts.get(ch, 0) + 1
            snaps.append(snap)
    keys = ("liquidity_usd", "volume_1h_usd", "top10_pct", "age_hours", "buy_share", "score")
    return {
        "n_open": len(snaps),
        "chains": chain_counts,
        "avg": {k: _mean(_num(snaps, k) if k != "score" else [float(s["score"]) for s in snaps if s.get("score") is not None]) for k in keys},
    }


def cross_winner_hints(rows: list[dict[str, Any]]) -> list[str]:
    """Heuristic notes comparing winner vs loser entry snapshots across variants."""
    hints: list[str] = []
    all_wins: list[dict] = []
    all_losses: list[dict] = []
    for _name, state in _load_states():
        for t in state.get("closed_trades") or []:
            if float(t.get("pnl_usd") or 0) >= 0:
                all_wins.append(t)
            else:
                all_losses.append(t)

    opens = _open_profile()
    if opens["n_open"]:
        avg = opens["avg"]
        hints.append(
            f"Open entries (n={opens['n_open']}, chains={opens['chains']}): "
            f"liq≈{avg.get('liquidity_usd')}, vol≈{avg.get('volume_1h_usd')}, "
            f"top10≈{avg.get('top10_pct')}, age≈{avg.get('age_hours')}h, score≈{avg.get('score')}"
        )
        if avg.get("liquidity_usd"):
            hints.append("Provisional: prefer deeper liquidity variants (deep_liquidity / anti_overshoot / winner_like_v1).")
        if avg.get("age_hours") and float(avg["age_hours"]) > 6:
            hints.append("Provisional: opens are mid-age — try mid_age_sweet / winner_like_v1, not only young_only.")
        if avg.get("top10_pct") is not None and float(avg["top10_pct"]) < 12:
            hints.append("Provisional: opens have low concentration — low_concentration may fit.")

    if len(all_wins) < 3 or len(all_losses) < 3:
        hints.append("Need more closed trades (≥3 wins and ≥3 losses) for stable closed-trade profiles.")
        hints.append("Run: python -m desk.propose_variants  (writes configs/variants/auto/)")
        return hints

    for key, label in (
        ("liquidity_usd", "entry liquidity"),
        ("volume_1h_usd", "1h volume"),
        ("top10_pct", "top10 holder %"),
        ("age_hours", "age hours"),
    ):
        w = _mean(_num(all_wins, key, "entry_snapshot"))
        l = _mean(_num(all_losses, key, "entry_snapshot"))
        if w is None or l is None:
            continue
        if key == "top10_pct":
            if w + 1 < l:
                hints.append(f"Winners had lower {label} ({w:.1f}% vs {l:.1f}%) — tighten holder cap.")
        elif key == "age_hours":
            hints.append(f"Winners avg {label}={w:.2f}h vs losers {l:.2f}h.")
        else:
            if w > l * 1.2:
                hints.append(f"Winners had higher {label} (${w:,.0f} vs ${l:,.0f}) — raise the floor.")
            elif l > w * 1.2:
                hints.append(f"Losers had higher {label} (${l:,.0f} vs ${w:,.0f}) — unusual; inspect exits.")
    overshoots = sum(int(r.get("stop_overshoots") or 0) for r in rows)
    if overshoots:
        hints.append(
            f"{overshoots} stop overshoots logged — thin pools / gap risk; try deep_liquidity or tighter_stops."
        )
    hints.append("Refresh auto variant: python -m desk.propose_variants")
    return hints


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Winner profile across meme desk variants")
    ap.add_argument("--json", action="store_true", help="print JSON only")
    args = ap.parse_args(argv)
    states = _load_states()
    rows = [summarize_variant(n, s) for n, s in states]
    rows.sort(key=lambda r: r["equity"], reverse=True)
    hints = cross_winner_hints(rows)
    payload = {"leaderboard": rows, "hints": hints, "open_profile": _open_profile()}
    out_path = ROOT / "data" / "lab_leaderboard.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0
    print("MEME DESK LAB LEADERBOARD (paper)")
    print("-" * 72)
    if not rows:
        print("No data/*/state.json yet. Run variants first.")
        return 0
    for r in rows:
        print(
            f"{r['variant']:18} equity=${r['equity']:.2f} ({r['pnl_pct']:+.1f}%)  "
            f"closed={r['closed']} W/L={r['wins']}/{r['losses']}  "
            f"overshoots={r['stop_overshoots']}"
        )
    print("-" * 72)
    print("Winner hints:")
    for h in hints:
        print(f"  • {h}")
    print(f"\nWrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
