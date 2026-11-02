"""Risk — reprice every ~5s and apply hard exit rules."""
from __future__ import annotations

import logging
import time
from typing import Any

from desk.http_client import HttpClient
from desk.sources import dexscreener
from desk.stages.executor import close_position

log = logging.getLogger("desk.risk")


def reprice_and_manage(
    http: HttpClient,
    cfg: dict[str, Any],
    state: dict[str, Any],
    state_path,
    desk_log,
) -> None:
    hard_stop = float(cfg.get("hard_stop_pct", -6.0))
    trail_on = float(cfg.get("trail_activate_pct", 12.0))
    trail_dd = float(cfg.get("trail_drawdown_pct", 6.0))
    max_hold = float(cfg.get("max_hold_minutes", 360))
    rug_drop = float(cfg.get("rug_liquidity_drop_pct", 60.0))

    positions = list(state.get("positions") or [])
    try:
        batch = dexscreener.enrich_tokens_batch(
            http, [(str(p.get("chain") or ""), p["mint"]) for p in positions]
        )
    except Exception as exc:  # noqa: BLE001
        desk_log.warning("batch reprice failed: %s", exc)
        batch = {}

    # copy list — may mutate positions while iterating
    for pos in positions:
        try:
            snap = batch.get((str(pos.get("chain") or ""), pos["mint"])) or dexscreener.price_snapshot(
                http,
                pos["mint"],
                chain=pos.get("chain"),
                allowed_chains={str(pos["chain"])} if pos.get("chain") else None,
            )
        except Exception as exc:  # noqa: BLE001
            desk_log.warning("reprice failed %s: %s", pos["mint"], exc)
            continue
        if not snap or not snap.get("price_usd"):
            desk_log.warning("reprice unavailable %s; keeping position", pos["mint"])
            continue

        price = float(snap["price_usd"])
        liq = float(snap.get("liquidity_usd") or pos.get("entry_liquidity_usd") or 0.0)
        entry = float(pos["entry_fill_price"])
        pos["mark_price"] = price
        pos["mark_value_usd"] = float(pos["tokens"]) * price
        pos["last_liquidity_usd"] = liq

        if price > float(pos.get("peak_price") or 0):
            pos["peak_price"] = price

        pnl_pct = (price / entry - 1.0) * 100.0 if entry else 0.0
        hold_min = (time.time() - float(pos.get("opened_ts") or time.time())) / 60.0

        # rug protection: liquidity drop ≥ 60% from entry
        entry_liq = float(pos.get("entry_liquidity_usd") or 0.0)
        if entry_liq > 0 and liq > 0:
            drop = (entry_liq - liq) / entry_liq * 100.0
            if drop >= rug_drop:
                creator = pos.get("creator")
                if creator and creator not in state.setdefault("creator_blacklist", []):
                    state["creator_blacklist"].append(creator)
                close_position(
                    cfg,
                    state,
                    pos,
                    price,
                    liq,
                    "rug_liquidity_drop",
                    state_path,
                    desk_log,
                )
                continue

        peak = float(pos.get("peak_price") or price)
        peak_pnl = (peak / entry - 1.0) * 100.0 if entry else 0.0

        # hard stop (may gap past -6%); breakeven lock raises it once the trade has run
        stop_level, stop_reason = hard_stop, "hard_stop"
        be_on = cfg.get("breakeven_activate_pct")
        if be_on is not None and peak_pnl >= float(be_on):
            stop_level = max(hard_stop, float(cfg.get("breakeven_lock_pct", 1.5)))
            stop_reason = "breakeven_stop"
        if pnl_pct <= stop_level:
            overshoot = pnl_pct < stop_level - 0.01
            close_position(
                cfg,
                state,
                pos,
                price,
                liq,
                stop_reason,
                state_path,
                desk_log,
                stop_overshoot=overshoot,
            )
            continue

        tp = cfg.get("take_profit_pct")
        if tp is not None and not pos.get("tp_done") and pnl_pct >= float(tp):
            close_position(
                cfg,
                state,
                pos,
                price,
                liq,
                "take_profit_partial",
                state_path,
                desk_log,
                fraction=float(cfg.get("take_profit_fraction", 0.5)),
            )
            pos["tp_done"] = True

        # trailing stop: tiers [[activate_pct, drawdown_pct], ...] or legacy single tier
        tiers = cfg.get("trail_tiers")
        if tiers:
            reached = [t for t in tiers if peak_pnl >= float(t[0])]
            if reached:
                pos["trail_active"] = True
                trail_dd = float(max(reached, key=lambda t: float(t[0]))[1])
        elif pnl_pct >= trail_on:
            pos["trail_active"] = True
        if pos.get("trail_active"):
            dd = (price / peak - 1.0) * 100.0 if peak else 0.0
            if dd <= -trail_dd:
                close_position(
                    cfg,
                    state,
                    pos,
                    price,
                    liq,
                    "trailing_stop",
                    state_path,
                    desk_log,
                )
                continue

        np_min = cfg.get("no_progress_minutes")
        if np_min is not None and hold_min >= float(np_min) and peak_pnl < float(
            cfg.get("no_progress_min_peak_pct", 2.0)
        ):
            close_position(
                cfg,
                state,
                pos,
                price,
                liq,
                "no_progress",
                state_path,
                desk_log,
            )
            continue

        # max hold
        if hold_min >= max_hold:
            close_position(
                cfg,
                state,
                pos,
                price,
                liq,
                "max_hold",
                state_path,
                desk_log,
            )
            continue
