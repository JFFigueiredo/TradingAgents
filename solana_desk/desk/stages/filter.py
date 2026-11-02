"""Filter — fail closed. Soft rejects cooldown; hard rejects permanent. Multi-chain."""
from __future__ import annotations

import logging
import time
from typing import Any, Callable

from desk.chains import chain_by_dexscreener, resolve_chains
from desk.http_client import HttpClient
from desk.sources import goplus, rugcheck, solana_rpc
from desk.state import bump, bump_reject

log = logging.getLogger("desk.filter")

_BONDING_HINTS = ("pump.fun", "pumpfun-bc", "bonding", "moonshot")


def _dex_family(dex_id: str | None) -> str | None:
    if not dex_id:
        return None
    d = dex_id.lower().replace("_", "-").replace(" ", "")
    if "pump" in d and "swap" in d:
        return "pumpswap"
    if d in ("pumpfun", "pump-fun", "pump.fun") or d.endswith("pumpfun"):
        return "pumpfun"
    if "raydium" in d:
        return "raydium"
    if "pancake" in d:
        return "pancakeswap"
    if "uniswap" in d or d.startswith("uni"):
        return "uniswap"
    if "aerodrome" in d:
        return "aerodrome"
    if "baseswap" in d:
        return "baseswap"
    if "sushi" in d:
        return "sushiswap"
    if "camelot" in d:
        return "camelot"
    if "virtual" in d:
        return "virtuals"
    if "clanker" in d:
        return "clanker"
    if "biswap" in d:
        return "biswap"
    if "thena" in d:
        return "thena"
    if "ramses" in d:
        return "ramses"
    if "quickswap" in d:
        return "quickswap"
    if "traderjoe" in d or d.startswith("joe") or "lfj" in d:
        return "traderjoe" if "lfj" not in d else "lfj"
    if "pangolin" in d:
        return "pangolin"
    if "velodrome" in d:
        return "velodrome"
    if "thruster" in d:
        return "thruster"
    if "bladeswap" in d:
        return "bladeswap"
    if "syncswap" in d:
        return "syncswap"
    if "pumpswap" in d:
        return "pumpswap"
    return d


def _is_pre_graduation(cand: dict[str, Any]) -> bool:
    dex = str(cand.get("dex_id") or "").lower()
    name = str(cand.get("name") or "").lower()
    if "bonding" in dex or any(h in dex for h in _BONDING_HINTS):
        return True
    pair = str(cand.get("pair_address") or "")
    if dex in ("pumpfun", "pump-fun", "pump.fun") and pair.endswith("pump"):
        return True
    if "bonding curve" in name:
        return True
    return False


def memory_blocks(state: dict[str, Any], key: str, now: float) -> str | None:
    mem = state.get("reject_memory") or {}
    row = mem.get(key)
    if not row:
        return None
    if row.get("kind") == "hard":
        return f"hard:{row.get('reason')}"
    until = float(row.get("until_ts") or 0)
    if until > now:
        return f"soft_cooldown:{row.get('reason')}"
    return None


def remember(
    state: dict[str, Any],
    key: str,
    kind: str,
    reason: str,
    soft_min: float,
) -> None:
    now = time.time()
    entry: dict[str, Any] = {"kind": kind, "reason": reason, "ts": now}
    if kind == "soft":
        entry["until_ts"] = now + soft_min * 60.0
    state.setdefault("reject_memory", {})[key] = entry


def _mem_key(cand: dict[str, Any]) -> str:
    return f"{cand.get('chain') or 'unknown'}:{cand.get('mint')}".lower()


def _resolve_chain_pack(cfg: dict[str, Any], cand: dict[str, Any]) -> dict[str, Any] | None:
    chains = resolve_chains(cfg)
    chain = str(cand.get("chain") or "").lower()
    if chain:
        pack = chain_by_dexscreener(chains, chain)
        if pack:
            return pack
        for c in chains:
            if str(c.get("id")).lower() == chain or str(c.get("gecko_network")).lower() == chain:
                return c
    if len(chains) == 1:
        return chains[0]
    return None


def _apply_holder_creator_rules(
    state: dict[str, Any],
    cand: dict[str, Any],
    parsed: dict[str, Any],
    cfg: dict[str, Any],
    mem: str,
    soft_min: float,
) -> tuple[bool, str | None, str | None]:
    top10 = parsed.get("top10_pct")
    if top10 is None:
        remember(state, mem, "hard", "holders_unavailable", soft_min)
        return False, "hard", "holders_unavailable"
    if float(top10) > float(cfg.get("max_top10_holder_pct", 20)):
        remember(state, mem, "hard", "holders_concentrated", soft_min)
        return False, "hard", "holders_concentrated"

    creator = parsed.get("creator")
    if not creator:
        remember(state, mem, "hard", "creator_unknown", soft_min)
        return False, "hard", "creator_unknown"
    bl = set(state.get("creator_blacklist") or [])
    if creator in bl:
        remember(state, mem, "hard", "creator_blacklisted", soft_min)
        return False, "hard", "creator_blacklisted"
    if parsed.get("rugged"):
        remember(state, mem, "hard", "creator_rugged", soft_min)
        return False, "hard", "creator_rugged"
    if parsed.get("serious_risk"):
        remember(state, mem, "hard", "serious_rugcheck_risk", soft_min)
        return False, "hard", "serious_rugcheck_risk"

    # optional EVM tax caps
    max_tax = cfg.get("max_tax_pct")
    if max_tax is not None:
        for k in ("buy_tax", "sell_tax"):
            t = parsed.get(k)
            if t is not None and float(t) > float(max_tax):
                remember(state, mem, "hard", f"{k}_high", soft_min)
                return False, "hard", f"{k}_high"

    cand["creator"] = creator
    if parsed.get("holder_count") is not None:
        cand["holder_count"] = parsed["holder_count"]
    cand["top10_pct"] = top10
    return True, None, None


def _heavy_solana(
    http: HttpClient,
    cfg: dict[str, Any],
    state: dict[str, Any],
    cand: dict[str, Any],
    mem: str,
    soft_min: float,
) -> tuple[bool, str | None, str | None]:
    mint = cand["mint"]
    report = rugcheck.fetch_report(http, mint)
    if not report:
        remember(state, mem, "soft", "rugcheck_unavailable", soft_min)
        return False, "soft", "rugcheck_unavailable"
    parsed = rugcheck.parse_report(report, cand.get("pair_address"))
    cand["safety"] = parsed

    rpc = solana_rpc.get_mint_authorities(http, mint)
    if not rpc:
        remember(state, mem, "soft", "rpc_unavailable", soft_min)
        return False, "soft", "rpc_unavailable"
    cand["rpc"] = rpc

    if parsed.get("mint_authority") is not None or rpc.get("mint_authority") is not None:
        remember(state, mem, "hard", "mint_authority_active", soft_min)
        return False, "hard", "mint_authority_active"
    if parsed.get("freeze_authority") is not None or rpc.get("freeze_authority") is not None:
        remember(state, mem, "hard", "freeze_authority_active", soft_min)
        return False, "hard", "freeze_authority_active"

    if not parsed.get("lp_market_matched"):
        remember(state, mem, "hard", "lp_pool_missing", soft_min)
        return False, "hard", "lp_pool_missing"
    lp_pct = parsed.get("lp_locked_pct")
    if lp_pct is None:
        remember(state, mem, "hard", "lp_lock_unknown", soft_min)
        return False, "hard", "lp_lock_unknown"
    if float(lp_pct) < float(cfg.get("min_lp_locked_pct", 95)):
        remember(state, mem, "hard", "lp_lock_low", soft_min)
        return False, "hard", "lp_lock_low"

    return _apply_holder_creator_rules(state, cand, parsed, cfg, mem, soft_min)


def _heavy_goplus(
    http: HttpClient,
    cfg: dict[str, Any],
    state: dict[str, Any],
    cand: dict[str, Any],
    pack: dict[str, Any],
    mem: str,
    soft_min: float,
) -> tuple[bool, str | None, str | None]:
    chain_id = str(pack.get("goplus_chain_id") or "")
    if not chain_id:
        remember(state, mem, "hard", "goplus_chain_missing", soft_min)
        return False, "hard", "goplus_chain_missing"
    parsed = goplus.fetch_security(http, chain_id, cand["mint"])
    if not parsed:
        remember(state, mem, "soft", "goplus_unavailable", soft_min)
        return False, "soft", "goplus_unavailable"
    cand["safety"] = parsed

    if parsed.get("mint_authority") is not None:
        remember(state, mem, "hard", "mint_authority_active", soft_min)
        return False, "hard", "mint_authority_active"
    if parsed.get("freeze_authority") is not None:
        remember(state, mem, "hard", "freeze_authority_active", soft_min)
        return False, "hard", "freeze_authority_active"

    if not parsed.get("lp_market_matched"):
        remember(state, mem, "hard", "lp_pool_missing", soft_min)
        return False, "hard", "lp_pool_missing"
    lp_pct = parsed.get("lp_locked_pct")
    if lp_pct is None:
        remember(state, mem, "hard", "lp_lock_unknown", soft_min)
        return False, "hard", "lp_lock_unknown"
    min_lp = float(cfg.get("min_lp_locked_pct", 95))
    # EVM often reports lower lock precision — allow config override per variant
    if float(lp_pct) < min_lp:
        remember(state, mem, "hard", "lp_lock_low", soft_min)
        return False, "hard", "lp_lock_low"

    return _apply_holder_creator_rules(state, cand, parsed, cfg, mem, soft_min)


def _entry_timing_ok(cfg: dict[str, Any], cand: dict[str, Any]) -> tuple[bool, str | None]:
    """Optional short-term confirmation; every key is off unless set in the config."""
    def num(key: str) -> float | None:
        v = cand.get(key)
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    max_1h = cfg.get("max_price_change_1h_pct")
    min_1h = cfg.get("min_price_change_1h_pct")
    if max_1h is not None or min_1h is not None:
        chg = num("price_change_1h_pct")
        if chg is None:
            return False, "change_1h_unavailable"
        if max_1h is not None and chg > float(max_1h):
            return False, "chasing_1h_pump"
        if min_1h is not None and chg < float(min_1h):
            return False, "crashed_1h"

    min_6h = cfg.get("min_price_change_6h_pct")
    if min_6h is not None:
        chg6 = num("price_change_6h_pct")
        if chg6 is None:
            return False, "change_6h_unavailable"
        if chg6 < float(min_6h):
            return False, "crashed_6h"

    min_5m = cfg.get("min_price_change_5m_pct")
    max_5m = cfg.get("max_price_change_5m_pct")
    if min_5m is not None or max_5m is not None:
        chg5 = num("price_change_5m_pct")
        if chg5 is None:
            return False, "change_5m_unavailable"
        if min_5m is not None and chg5 < float(min_5m):
            return False, "falling_5m"
        if max_5m is not None and chg5 > float(max_5m):
            return False, "chasing_5m_spike"

    min_bs5 = cfg.get("min_buy_share_5m")
    if min_bs5 is not None:
        b, s = num("buys_5m"), num("sells_5m")
        min_tx = float(cfg.get("min_txns_5m", 10))
        if b is None or s is None or (b + s) < min_tx:
            return False, "txns_5m_thin"
        if b / (b + s) < float(min_bs5):
            return False, "buy_share_5m_low"

    return True, None


def evaluate_candidate(
    http: HttpClient,
    cfg: dict[str, Any],
    state: dict[str, Any],
    cand: dict[str, Any],
    *,
    heavy_budget: list[int],
) -> tuple[bool, str | None, str | None]:
    mint = cand.get("mint")
    if not mint:
        return False, "hard", "no_mint"

    soft_min = float(cfg.get("soft_reject_cooldown_min", 15))
    now = time.time()
    mem = _mem_key(cand)
    blocked = memory_blocks(state, mem, now)
    if blocked:
        return False, None, blocked

    pack = _resolve_chain_pack(cfg, cand)
    if pack is None:
        remember(state, mem, "soft", "chain_unknown", soft_min)
        return False, "soft", "chain_unknown"
    cand["chain"] = pack.get("id") or cand.get("chain")

    family = _dex_family(cand.get("dex_id"))
    allowed = [x.lower() for x in (pack.get("allowed_dex_families") or cfg.get("allowed_dex_families") or [])]
    if family is None:
        remember(state, mem, "soft", "dex_unknown", soft_min)
        return False, "soft", "dex_unknown"
    # family match: allow substring for uniswap-v3 etc already normalized
    if family not in allowed:
        remember(state, mem, "hard", "dex_not_allowed", soft_min)
        return False, "hard", "dex_not_allowed"

    if pack.get("reject_pre_graduation_pumpfun", cfg.get("reject_pre_graduation_pumpfun", True)):
        if family == "pumpfun" and _is_pre_graduation(cand):
            remember(state, mem, "hard", "pre_graduation_pumpfun", soft_min)
            return False, "hard", "pre_graduation_pumpfun"

    age = cand.get("age_hours")
    if age is None:
        remember(state, mem, "soft", "age_unknown", soft_min)
        return False, "soft", "age_unknown"
    min_age = cfg.get("min_pair_age_hours")
    if min_age is not None and float(age) < float(min_age):
        remember(state, mem, "soft", "too_young", soft_min)
        return False, "soft", "too_young"
    if float(age) > float(cfg.get("max_pair_age_hours", 72)):
        remember(state, mem, "hard", "too_old", soft_min)
        return False, "hard", "too_old"

    vol = cand.get("volume_1h_usd")
    if vol is None:
        remember(state, mem, "soft", "volume_unavailable", soft_min)
        return False, "soft", "volume_unavailable"
    if float(vol) < float(cfg.get("min_volume_1h_usd", 50000)):
        remember(state, mem, "soft", "volume_low", soft_min)
        return False, "soft", "volume_low"

    liq = cand.get("liquidity_usd")
    if liq is None:
        remember(state, mem, "soft", "liquidity_unavailable", soft_min)
        return False, "soft", "liquidity_unavailable"
    if float(liq) < float(cfg.get("min_liquidity_usd", 10000)):
        remember(state, mem, "soft", "liquidity_low", soft_min)
        return False, "soft", "liquidity_low"

    price = cand.get("price_usd")
    if price is None or float(price) <= 0:
        remember(state, mem, "soft", "price_unavailable", soft_min)
        return False, "soft", "price_unavailable"

    # optional min buy share (winner-like)
    min_buy = cfg.get("min_buy_share_1h")
    if min_buy is not None:
        buys = float(cand.get("buys_1h") or 0)
        sells = float(cand.get("sells_1h") or 0)
        tot = buys + sells
        if tot <= 0 or (buys / tot) < float(min_buy):
            remember(state, mem, "soft", "buy_share_low", soft_min)
            return False, "soft", "buy_share_low"

    ok, reason = _entry_timing_ok(cfg, cand)
    if not ok:
        remember(state, mem, "soft", reason, soft_min)
        return False, "soft", reason

    if heavy_budget[0] <= 0:
        remember(state, mem, "soft", "heavy_budget_exhausted", soft_min)
        return False, "soft", "heavy_budget_exhausted"
    heavy_budget[0] -= 1

    safety = str(pack.get("safety") or "solana_rugcheck")
    if safety == "solana_rugcheck":
        ok, kind, reason = _heavy_solana(http, cfg, state, cand, mem, soft_min)
    elif safety == "goplus":
        ok, kind, reason = _heavy_goplus(http, cfg, state, cand, pack, mem, soft_min)
    else:
        remember(state, mem, "hard", "unknown_safety", soft_min)
        return False, "hard", "unknown_safety"

    if ok:
        cand["filter_approved"] = True
    return ok, kind, reason


def run_filter(
    http: HttpClient,
    cfg: dict[str, Any],
    state: dict[str, Any],
    candidates: list[dict[str, Any]],
    on_tick: Callable[[], None] | None = None,
) -> list[dict[str, Any]]:
    cap = int(cfg.get("rugcheck_rpc_cap_per_cycle", 12))
    budget = [cap]
    passed: list[dict[str, Any]] = []
    for cand in candidates:
        if on_tick:
            on_tick()
        bump(state, "evaluations")
        ok, kind, reason = evaluate_candidate(http, cfg, state, cand, heavy_budget=budget)
        if ok:
            bump(state, "filter_passed")
            passed.append(cand)
        else:
            bump(state, "filter_killed")
            if reason and not str(reason).startswith("hard:") and not str(reason).startswith("soft_cooldown:"):
                bump_reject(state, reason or "unknown")
            elif reason:
                bump_reject(state, reason.split(":", 1)[-1])
            if kind and reason and not str(reason).startswith(("hard:", "soft_cooldown:")):
                log.info("reject %s %s/%s", cand.get("mint"), kind, reason)
    return passed
