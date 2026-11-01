"""Simulated buyer/seller — paper fills only. Refuses unapproved tokens."""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from desk.logging_util import utc_now_iso
from desk.state import bump, equity_usd, save_state

log = logging.getLogger("desk.executor")


def _impact_pct(size_usd: float, liquidity_usd: float, coeff: float) -> float:
    if liquidity_usd <= 0:
        return 100.0
    return float(coeff) * (size_usd / liquidity_usd) * 100.0


def simulate_buy_fill(
    decision_price: float,
    size_usd: float,
    liquidity_usd: float,
    cfg: dict[str, Any],
) -> dict[str, Any]:
    slip = float(cfg.get("slippage_pct", 1.0))
    fee_pct = float(cfg.get("dex_fee_pct", 0.30))
    net_fee = float(cfg.get("network_fee_usd", 0.02))
    impact = _impact_pct(size_usd, liquidity_usd, float(cfg.get("price_impact_coeff", 1.0)))
    # buy: pay higher
    fill_price = decision_price * (1.0 + (slip + impact) / 100.0)
    dex_fee = size_usd * (fee_pct / 100.0)
    total_debit = size_usd + dex_fee + net_fee
    tokens = size_usd / fill_price if fill_price > 0 else 0.0
    return {
        "decision_price": decision_price,
        "fill_price": fill_price,
        "size_usd": size_usd,
        "tokens": tokens,
        "dex_fee_usd": dex_fee,
        "network_fee_usd": net_fee,
        "impact_pct": impact,
        "slippage_pct": slip,
        "total_debit_usd": total_debit,
    }


def simulate_sell_fill(
    decision_price: float,
    tokens: float,
    liquidity_usd: float,
    cfg: dict[str, Any],
) -> dict[str, Any]:
    slip = float(cfg.get("slippage_pct", 1.0))
    fee_pct = float(cfg.get("dex_fee_pct", 0.30))
    net_fee = float(cfg.get("network_fee_usd", 0.02))
    notional = tokens * decision_price
    impact = _impact_pct(notional, liquidity_usd, float(cfg.get("price_impact_coeff", 1.0)))
    fill_price = decision_price * (1.0 - (slip + impact) / 100.0)
    proceeds = tokens * fill_price
    dex_fee = proceeds * (fee_pct / 100.0)
    total_credit = max(0.0, proceeds - dex_fee - net_fee)
    return {
        "decision_price": decision_price,
        "fill_price": fill_price,
        "tokens": tokens,
        "gross_usd": proceeds,
        "dex_fee_usd": dex_fee,
        "network_fee_usd": net_fee,
        "impact_pct": impact,
        "slippage_pct": slip,
        "total_credit_usd": total_credit,
    }


def revenge_blocked(state: dict[str, Any], cfg: dict[str, Any]) -> bool:
    last = state.get("last_loss_ts")
    if not last:
        return False
    cool = float(cfg.get("revenge_cooldown_min", 20)) * 60.0
    return (time.time() - float(last)) < cool


def open_positions_mints(state: dict[str, Any]) -> set[str]:
    return {p["mint"] for p in state.get("positions") or []}


def try_entries(
    cfg: dict[str, Any],
    state: dict[str, Any],
    scored: list[dict[str, Any]],
    state_path,
    desk_log,
) -> int:
    """Open paper positions for top scored approved candidates. Returns # opened."""
    opened = 0
    max_pos = int(cfg.get("max_open_positions", 5))
    max_pct = float(cfg.get("max_position_pct_equity", 0.08))
    if revenge_blocked(state, cfg):
        desk_log.info("revenge cooldown active; skipping entries")
        return 0

    held = open_positions_mints(state)
    for cand in scored:
        if len(state.get("positions") or []) >= max_pos:
            break
        if not cand.get("filter_approved"):
            desk_log.warning("executor refused unapproved mint %s", cand.get("mint"))
            continue
        mint = cand["mint"]
        if mint in held:
            continue
        eq = equity_usd(state)
        size = eq * max_pct
        # hard assertion: never exceed 8% of CURRENT equity
        assert size <= eq * max_pct + 1e-9, "position size exceeds max equity fraction"
        if size < 1.0 or state["cash_usd"] < size * 1.05:
            continue
        price = float(cand["price_usd"])
        liq = float(cand.get("liquidity_usd") or 0.0)
        fill = simulate_buy_fill(price, size, liq, cfg)
        if fill["total_debit_usd"] > state["cash_usd"]:
            continue
        state["cash_usd"] = float(state["cash_usd"]) - fill["total_debit_usd"]
        pos = {
            "id": uuid.uuid4().hex[:12],
            "chain": cand.get("chain") or "solana",
            "mint": mint,
            "symbol": cand.get("name"),
            "pair_address": cand.get("pair_address"),
            "creator": cand.get("creator"),
            "opened_at": utc_now_iso(),
            "opened_ts": time.time(),
            "entry_decision_price": fill["decision_price"],
            "entry_fill_price": fill["fill_price"],
            "tokens": fill["tokens"],
            "cost_usd": fill["total_debit_usd"],
            "entry_fees_usd": fill["dex_fee_usd"] + fill["network_fee_usd"],
            "entry_liquidity_usd": liq,
            "peak_price": fill["fill_price"],
            "trail_active": False,
            "mark_price": fill["fill_price"],
            "mark_value_usd": fill["tokens"] * fill["fill_price"],
            "score": cand.get("score"),
            "entry_snapshot": {
                "chain": cand.get("chain"),
                "volume_1h_usd": cand.get("volume_1h_usd"),
                "liquidity_usd": liq,
                "top10_pct": cand.get("top10_pct"),
                "age_hours": cand.get("age_hours"),
                "buys_1h": cand.get("buys_1h"),
                "sells_1h": cand.get("sells_1h"),
                "buy_share": (
                    float(cand["buys_1h"]) / (float(cand["buys_1h"]) + float(cand["sells_1h"]))
                    if (cand.get("buys_1h") is not None and cand.get("sells_1h") is not None
                        and (float(cand.get("buys_1h") or 0) + float(cand.get("sells_1h") or 0)) > 0)
                    else None
                ),
            },
        }
        state.setdefault("positions", []).append(pos)
        bump(state, "traded")
        opened += 1
        held.add(mint)
        desk_log.event(
            "fill_buy",
            f"BUY {mint} size=${size:.2f} fill={fill['fill_price']:.8g}",
            mint=mint,
            fill=fill,
            position_id=pos["id"],
            score=cand.get("score"),
        )
        desk_log.info(
            "BUY %s score=%.3f size=$%.2f fill=%.8g cash=$%.2f",
            mint,
            float(cand.get("score") or 0),
            size,
            fill["fill_price"],
            state["cash_usd"],
        )
        save_state(state_path, state)
    return opened


def close_position(
    cfg: dict[str, Any],
    state: dict[str, Any],
    pos: dict[str, Any],
    decision_price: float,
    liquidity_usd: float,
    reason: str,
    state_path,
    desk_log,
    *,
    stop_overshoot: bool = False,
) -> dict[str, Any]:
    fill = simulate_sell_fill(decision_price, float(pos["tokens"]), liquidity_usd, cfg)
    state["cash_usd"] = float(state["cash_usd"]) + fill["total_credit_usd"]
    pnl = fill["total_credit_usd"] - float(pos["cost_usd"])
    pnl_pct = (pnl / float(pos["cost_usd"]) * 100.0) if pos["cost_usd"] else 0.0
    trade = {
        "id": pos["id"],
        "mint": pos["mint"],
        "symbol": pos.get("symbol"),
        "creator": pos.get("creator"),
        "opened_at": pos.get("opened_at"),
        "closed_at": utc_now_iso(),
        "reason": reason,
        "entry_decision_price": pos.get("entry_decision_price"),
        "entry_fill_price": pos.get("entry_fill_price"),
        "exit_decision_price": fill["decision_price"],
        "exit_fill_price": fill["fill_price"],
        "cost_usd": pos.get("cost_usd"),
        "proceeds_usd": fill["total_credit_usd"],
        "pnl_usd": pnl,
        "pnl_pct": pnl_pct,
        "score": pos.get("score"),
        "entry_snapshot": pos.get("entry_snapshot"),
        "hold_minutes": (time.time() - float(pos.get("opened_ts") or time.time())) / 60.0,
        "stop_overshoot": stop_overshoot,
        "peak_price": pos.get("peak_price"),
        "fees_usd": float(pos.get("entry_fees_usd") or 0)
        + fill["dex_fee_usd"]
        + fill["network_fee_usd"],
    }
    state.setdefault("closed_trades", []).append(trade)
    state["positions"] = [p for p in state.get("positions", []) if p["id"] != pos["id"]]
    if pnl < 0:
        state["last_loss_ts"] = time.time()
    if stop_overshoot:
        bump(state, "stop_overshoots")
    desk_log.event("fill_sell", f"SELL {pos['mint']} {reason} pnl={pnl:.2f}", trade=trade)
    desk_log.info(
        "SELL %s reason=%s pnl=$%.2f (%.1f%%) cash=$%.2f",
        pos["mint"],
        reason,
        pnl,
        pnl_pct,
        state["cash_usd"],
    )
    save_state(state_path, state)
    return trade
