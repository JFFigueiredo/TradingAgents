"""Mine winner/loser traits from data/*/state.json and propose new variant JSON files."""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CONTROL = ROOT / "configs" / "control.json"
OUT = ROOT / "configs" / "variants" / "auto"


def _mean(xs: list[float]) -> float | None:
    return statistics.mean(xs) if xs else None


def _collect() -> tuple[list[dict], list[dict], list[dict]]:
    """closed wins, closed losses, open positions (provisional)."""
    wins: list[dict] = []
    losses: list[dict] = []
    opens: list[dict] = []
    data = ROOT / "data"
    if not data.is_dir():
        return wins, losses, opens
    for d in data.iterdir():
        sp = d / "state.json"
        if not sp.is_file():
            continue
        try:
            state = json.loads(sp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for t in state.get("closed_trades") or []:
            snap = dict(t.get("entry_snapshot") or {})
            snap["score"] = t.get("score")
            snap["pnl_usd"] = t.get("pnl_usd")
            snap["chain"] = snap.get("chain") or t.get("chain")
            if float(t.get("pnl_usd") or 0) >= 0:
                wins.append(snap)
            else:
                losses.append(snap)
        for p in state.get("positions") or []:
            snap = dict(p.get("entry_snapshot") or {})
            snap["score"] = p.get("score")
            snap["chain"] = snap.get("chain") or p.get("chain")
            # mark pnl provisional
            entry = float(p.get("entry_fill_price") or 0)
            mark = float(p.get("mark_price") or entry)
            snap["unrealized_pct"] = ((mark / entry) - 1.0) * 100.0 if entry else 0.0
            opens.append(snap)
    return wins, losses, opens


def _vals(rows: list[dict], key: str) -> list[float]:
    out: list[float] = []
    for r in rows:
        v = r.get(key)
        if v is None:
            continue
        try:
            out.append(float(v))
        except (TypeError, ValueError):
            pass
    return out


def build_proposal(tag: str) -> dict[str, Any]:
    wins, losses, opens = _collect()
    # Prefer closed wins; if few, blend with opens that are green
    sample = list(wins)
    for o in opens:
        if float(o.get("unrealized_pct") or 0) >= 0:
            sample.append(o)
    control = json.loads(CONTROL.read_text(encoding="utf-8"))
    cfg = deepcopy(control)
    cfg.pop("_config_path", None)
    cfg["id"] = f"auto_{tag}"
    cfg["name"] = f"Auto-proposed {tag}"
    cfg["description"] = (
        f"Generated from {len(wins)} closed wins, {len(losses)} losses, {len(opens)} opens. "
        "Review before promoting."
    )
    cfg["chains"] = cfg.get("chains") or ["solana"]

    notes: list[str] = []
    if len(sample) < 2:
        notes.append("Insufficient winner sample — used mild defaults toward deeper liquidity.")
        cfg["min_liquidity_usd"] = max(float(cfg.get("min_liquidity_usd", 10000)), 25000)
        cfg["min_volume_1h_usd"] = max(float(cfg.get("min_volume_1h_usd", 50000)), 60000)
    else:
        w_liq = _mean(_vals(sample, "liquidity_usd"))
        w_vol = _mean(_vals(sample, "volume_1h_usd"))
        w_top = _mean(_vals(sample, "top10_pct"))
        w_age = _mean(_vals(sample, "age_hours"))
        w_buy = _mean(_vals(sample, "buy_share"))
        w_score = _mean(_vals(sample, "score"))
        l_liq = _mean(_vals(losses, "liquidity_usd"))
        l_top = _mean(_vals(losses, "top10_pct"))

        if w_liq:
            # floor at ~60% of winner avg liquidity
            cfg["min_liquidity_usd"] = round(max(10000.0, w_liq * 0.6), 0)
            notes.append(f"min_liquidity_usd from winners≈{w_liq:.0f}")
        if w_vol:
            cfg["min_volume_1h_usd"] = round(max(30000.0, w_vol * 0.5), 0)
            notes.append(f"min_volume_1h_usd from winners≈{w_vol:.0f}")
        if w_top is not None:
            # slightly above winner avg but below loser avg if available
            cap = w_top * 1.25
            if l_top is not None:
                cap = min(cap, (w_top + l_top) / 2)
            cfg["max_top10_holder_pct"] = round(max(8.0, min(20.0, cap)), 1)
            notes.append(f"max_top10 from winners≈{w_top:.1f}")
        if w_age is not None:
            cfg["min_pair_age_hours"] = round(max(0.0, w_age * 0.25), 2)
            cfg["max_pair_age_hours"] = round(min(72.0, max(12.0, w_age * 2.5)), 1)
            notes.append(f"age window around winners≈{w_age:.1f}h")
        if w_buy is not None:
            cfg["min_buy_share_1h"] = round(max(0.45, min(0.70, w_buy * 0.95)), 2)
            notes.append(f"min_buy_share from winners≈{w_buy:.2f}")
        if w_score is not None:
            cfg["score_threshold"] = round(max(0.5, min(0.75, w_score * 0.92)), 2)
            notes.append(f"score_threshold from winners≈{w_score:.2f}")
        if l_liq and w_liq and l_liq < w_liq * 0.7:
            notes.append("Losers had thinner liquidity than winners — raising liq floor.")

    # chain mix from samples
    chain_counts: dict[str, int] = {}
    for r in sample:
        ch = str(r.get("chain") or "solana")
        chain_counts[ch] = chain_counts.get(ch, 0) + 1
    if chain_counts:
        top = sorted(chain_counts, key=lambda c: -chain_counts[c])[:2]
        cfg["chains"] = top
        notes.append(f"chains from sample: {chain_counts}")

    cfg["_proposal_notes"] = notes
    cfg["_sample_sizes"] = {"wins": len(wins), "losses": len(losses), "opens": len(opens), "used": len(sample)}
    return cfg


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default=None, help="suffix for auto_<tag>; default=UTC date")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    from datetime import datetime, timezone

    tag = args.tag or datetime.now(timezone.utc).strftime("%Y%m%d")
    cfg = build_proposal(tag)
    text = json.dumps(cfg, indent=2)
    if args.dry_run:
        print(text)
        return 0
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{cfg['id']}.json"
    # strip private notes keys into sidecar
    notes = cfg.pop("_proposal_notes", [])
    sizes = cfg.pop("_sample_sizes", {})
    path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    meta = path.with_suffix(".meta.json")
    meta.write_text(json.dumps({"notes": notes, "sample_sizes": sizes}, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {path}")
    for n in notes:
        print(f"  • {n}")
    print("Run with: python run.py --config", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
