#!/usr/bin/env python3
"""Generate / refresh experiment variant configs. Never overwrites control or locked variants."""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "configs" / "variants"
CONTROL = ROOT / "configs" / "control.json"

# Existing variants we must not overwrite
LOCKED = {
    "higher_volume",
    "tighter_stops",
    "momentum_bias",
    "deep_liquidity",
}


def base() -> dict:
    with CONTROL.open(encoding="utf-8") as f:
        return json.load(f)


def write(cfg: dict, *, overwrite: bool = True) -> None:
    cid = cfg["id"]
    path = OUT / f"{cid}.json"
    if cid in LOCKED and path.exists() and not overwrite:
        print(f"skip locked {cid}")
        return
    if cid in LOCKED:
        print(f"keep locked {cid} (not overwritten)")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {path.name}")


def mk(bid: str, name: str, desc: str, **over) -> dict:
    cfg = deepcopy(base())
    cfg.pop("_config_path", None)
    cfg["id"] = bid
    cfg["name"] = name
    cfg["description"] = desc
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k] = {**cfg[k], **v}
        else:
            cfg[k] = v
    # If not pure-solana, drop solana-specific top-level dex keys cloned from control
    chains = cfg.get("chains") or ["solana"]
    pure_sol = chains == ["solana"] or chains == [{"id": "solana"}]
    if not pure_sol:
        cfg.pop("gecko_dexes", None)
        cfg.pop("allowed_dex_families", None)
        cfg.pop("reject_pre_graduation_pumpfun", None)
    return cfg


def main() -> None:
    # --- Solana strategy grid (winner-inspired from live opens: high vol, mid age, low top10, buy pressure) ---
    specs = [
        mk(
            "winner_like_v1",
            "Winner-like (from first control fills)",
            "Mirrors open winners: vol≥80k, liq≥40k, top10≤12%, age 2–24h, buy share≥0.55",
            min_volume_1h_usd=80000,
            min_liquidity_usd=40000,
            max_top10_holder_pct=12,
            min_pair_age_hours=2,
            max_pair_age_hours=24,
            min_buy_share_1h=0.55,
            score_threshold=0.62,
        ),
        mk(
            "mid_age_sweet",
            "Mid-age sweet spot",
            "Avoid brand-new rugs and stale bags: 4–36h only",
            min_pair_age_hours=4,
            max_pair_age_hours=36,
            min_volume_1h_usd=60000,
            min_liquidity_usd=25000,
        ),
        mk(
            "young_only",
            "Young only",
            "Max age 6h — catch early momentum",
            max_pair_age_hours=6,
            min_volume_1h_usd=40000,
            score_threshold=0.58,
            trail_activate_pct=10.0,
            max_hold_minutes=120,
        ),
        mk(
            "buy_pressure",
            "Buy-pressure gate",
            "Require ≥60% buys in 1h txns",
            min_buy_share_1h=0.60,
            score_weights={"liquidity": 0.15, "momentum": 0.25, "holders": 0.15, "age": 0.10, "buy_sell": 0.35},
        ),
        mk(
            "low_concentration",
            "Low holder concentration",
            "Top10 ≤10% excluding pools",
            max_top10_holder_pct=10,
            min_liquidity_usd=20000,
            score_weights={"liquidity": 0.20, "momentum": 0.15, "holders": 0.35, "age": 0.15, "buy_sell": 0.15},
        ),
        mk(
            "score_strict",
            "Strict score 0.70",
            "Fewer trades, higher conviction",
            score_threshold=0.70,
            max_open_positions=3,
            max_position_pct_equity=0.10,
        ),
        mk(
            "score_loose",
            "Loose score 0.50",
            "More flow, lower bar — measure if volume helps",
            score_threshold=0.50,
            min_volume_1h_usd=35000,
            max_open_positions=5,
        ),
        mk(
            "scalp_fast",
            "Scalp fast",
            "Earlier trail, shorter hold, tighter stop",
            hard_stop_pct=-4.0,
            trail_activate_pct=6.0,
            trail_drawdown_pct=4.0,
            max_hold_minutes=90,
            revenge_cooldown_min=15,
            max_position_pct_equity=0.06,
        ),
        mk(
            "wide_trail",
            "Wide trail — let winners run",
            "Trail activates +20%, drawdown 10%, hold up to 12h",
            trail_activate_pct=20.0,
            trail_drawdown_pct=10.0,
            max_hold_minutes=720,
            hard_stop_pct=-7.0,
        ),
        mk(
            "size_up_few",
            "Size up, few positions",
            "12% equity, max 2 opens — concentrated bets",
            max_position_pct_equity=0.12,
            max_open_positions=2,
            score_threshold=0.65,
            min_liquidity_usd=30000,
        ),
        mk(
            "anti_overshoot",
            "Anti stop-overshoot",
            "Deep pools + smaller size to reduce gap past -6%",
            min_liquidity_usd=75000,
            min_volume_1h_usd=100000,
            max_position_pct_equity=0.05,
            hard_stop_pct=-5.0,
            price_impact_coeff=1.5,
        ),
        mk(
            "raydium_only",
            "Raydium-only Solana",
            "Skip pump ecosystem entirely",
            chains=["solana"],
            gecko_dexes=["raydium", "raydium-clmm", "raydium-launchlab"],
            allowed_dex_families=["raydium"],
            min_liquidity_usd=20000,
        ),
        mk(
            "pumpswap_graduated",
            "PumpSwap graduated only",
            "pumpswap + raydium, no pumpfun bonding",
            allowed_dex_families=["pumpswap", "raydium"],
            reject_pre_graduation_pumpfun=True,
            min_volume_1h_usd=60000,
        ),
        # --- Other chains ---
        mk(
            "base_control",
            "Base control",
            "Same risk rules on Base (GoPlus safety)",
            chains=["base"],
            gecko_new_pool_pages=1,
            min_volume_1h_usd=40000,
            min_liquidity_usd=15000,
            min_lp_locked_pct=80,
            max_tax_pct=10,
            rugcheck_rpc_cap_per_cycle=10,
        ),
        mk(
            "base_momentum",
            "Base momentum",
            "Base + momentum/buy weights, score 0.62",
            chains=["base"],
            gecko_new_pool_pages=1,
            min_volume_1h_usd=30000,
            min_liquidity_usd=12000,
            min_lp_locked_pct=75,
            max_tax_pct=8,
            score_threshold=0.62,
            score_weights={"liquidity": 0.15, "momentum": 0.30, "holders": 0.15, "age": 0.15, "buy_sell": 0.25},
            trail_activate_pct=10.0,
            max_hold_minutes=240,
        ),
        mk(
            "base_deep_liq",
            "Base deep liquidity",
            "Base with higher liq/vol floors",
            chains=["base"],
            gecko_new_pool_pages=1,
            min_volume_1h_usd=80000,
            min_liquidity_usd=50000,
            min_lp_locked_pct=85,
            max_tax_pct=5,
            max_open_positions=3,
            max_top10_holder_pct=15,
        ),
        mk(
            "eth_control",
            "Ethereum control",
            "ETH mainnet memes — stricter liquidity",
            chains=["ethereum"],
            gecko_new_pool_pages=1,
            min_volume_1h_usd=100000,
            min_liquidity_usd=80000,
            min_lp_locked_pct=85,
            max_tax_pct=5,
            max_open_positions=3,
            max_position_pct_equity=0.08,
            network_fee_usd=0.50,
        ),
        mk(
            "eth_strict",
            "Ethereum strict",
            "Very selective ETH: score 0.68, top10≤12%",
            chains=["ethereum"],
            gecko_new_pool_pages=1,
            min_volume_1h_usd=150000,
            min_liquidity_usd=120000,
            min_lp_locked_pct=90,
            max_tax_pct=3,
            max_top10_holder_pct=12,
            score_threshold=0.68,
            max_open_positions=2,
            network_fee_usd=0.50,
        ),
        mk(
            "bsc_control",
            "BSC control",
            "BSC / Pancake memes via GoPlus",
            chains=["bsc"],
            gecko_new_pool_pages=1,
            min_volume_1h_usd=50000,
            min_liquidity_usd=20000,
            min_lp_locked_pct=80,
            max_tax_pct=10,
            rugcheck_rpc_cap_per_cycle=10,
        ),
        mk(
            "bsc_anti_tax",
            "BSC anti-tax",
            "BSC with max 3% buy/sell tax",
            chains=["bsc"],
            gecko_new_pool_pages=1,
            min_volume_1h_usd=60000,
            min_liquidity_usd=25000,
            min_lp_locked_pct=85,
            max_tax_pct=3,
            max_top10_holder_pct=15,
        ),
        mk(
            "arb_control",
            "Arbitrum control",
            "Arbitrum meme venues",
            chains=["arbitrum"],
            gecko_new_pool_pages=1,
            min_volume_1h_usd=35000,
            min_liquidity_usd=15000,
            min_lp_locked_pct=75,
            max_tax_pct=8,
        ),
        mk(
            "multi_l2",
            "Multi L2 (Base + Arbitrum)",
            "Shared paper book across Base and Arbitrum",
            chains=["base", "arbitrum"],
            gecko_new_pool_pages=1,
            min_volume_1h_usd=40000,
            min_liquidity_usd=15000,
            min_lp_locked_pct=75,
            max_tax_pct=8,
            rugcheck_rpc_cap_per_cycle=8,
            scout_interval_sec=45,
        ),
        mk(
            "multi_sol_base",
            "Solana + Base",
            "Two hottest meme chains, shared equity",
            chains=["solana", "base"],
            gecko_new_pool_pages=1,
            min_volume_1h_usd=50000,
            min_liquidity_usd=20000,
            min_lp_locked_pct=90,
            max_tax_pct=8,
            rugcheck_rpc_cap_per_cycle=8,
            scout_interval_sec=45,
        ),
        mk(
            "multi_all",
            "All chains lab",
            "solana+base+bsc+eth+arb — rate-limit friendly pacing",
            chains=["solana", "base", "bsc", "ethereum", "arbitrum"],
            gecko_new_pool_pages=1,
            min_volume_1h_usd=60000,
            min_liquidity_usd=25000,
            min_lp_locked_pct=80,
            max_tax_pct=8,
            rugcheck_rpc_cap_per_cycle=6,
            scout_interval_sec=60,
            max_open_positions=5,
        ),
        mk(
            "winner_like_base",
            "Winner-like on Base",
            "Apply Solana winner profile thresholds on Base",
            chains=["base"],
            gecko_new_pool_pages=1,
            min_volume_1h_usd=80000,
            min_liquidity_usd=40000,
            max_top10_holder_pct=12,
            min_pair_age_hours=2,
            max_pair_age_hours=24,
            min_buy_share_1h=0.55,
            min_lp_locked_pct=80,
            max_tax_pct=8,
            score_threshold=0.62,
        ),
    ]

    # --- Wave 2: more chains + strategy grid ---
    evm_defaults = dict(
        gecko_new_pool_pages=1,
        min_lp_locked_pct=80,
        max_tax_pct=8,
        rugcheck_rpc_cap_per_cycle=8,
        scout_interval_sec=40,
    )
    new_chains = [
        ("polygon", 35000, 12000),
        ("avalanche", 35000, 12000),
        ("optimism", 30000, 12000),
        ("blast", 25000, 10000),
        ("linea", 25000, 10000),
    ]
    for chain, vol, liq in new_chains:
        specs.append(
            mk(
                f"{chain}_control",
                f"{chain.title()} control",
                f"Baseline paper desk on {chain}",
                chains=[chain],
                min_volume_1h_usd=vol,
                min_liquidity_usd=liq,
                **evm_defaults,
            )
        )
        specs.append(
            mk(
                f"{chain}_momentum",
                f"{chain.title()} momentum",
                f"Momentum/buy bias on {chain}",
                chains=[chain],
                min_volume_1h_usd=int(vol * 0.8),
                min_liquidity_usd=liq,
                score_threshold=0.62,
                score_weights={
                    "liquidity": 0.15,
                    "momentum": 0.30,
                    "holders": 0.15,
                    "age": 0.15,
                    "buy_sell": 0.25,
                },
                trail_activate_pct=10.0,
                max_hold_minutes=240,
                **evm_defaults,
            )
        )
        specs.append(
            mk(
                f"{chain}_deep",
                f"{chain.title()} deep liquidity",
                f"Higher floors on {chain}",
                chains=[chain],
                min_volume_1h_usd=int(vol * 2),
                min_liquidity_usd=int(liq * 3),
                min_lp_locked_pct=85,
                max_tax_pct=5,
                max_open_positions=3,
                max_top10_holder_pct=15,
                gecko_new_pool_pages=1,
                rugcheck_rpc_cap_per_cycle=8,
                scout_interval_sec=40,
            )
        )
        specs.append(
            mk(
                f"winner_like_{chain}",
                f"Winner-like on {chain.title()}",
                "Winner profile thresholds ported to this chain",
                chains=[chain],
                min_volume_1h_usd=80000,
                min_liquidity_usd=40000,
                max_top10_holder_pct=12,
                min_pair_age_hours=2,
                max_pair_age_hours=24,
                min_buy_share_1h=0.55,
                score_threshold=0.62,
                **evm_defaults,
            )
        )

    # Solana extra parameter grid
    for stop, trail_on, trail_dd, tag in (
        (-5.0, 10.0, 5.0, "stop5_trail10"),
        (-8.0, 15.0, 8.0, "stop8_trail15"),
        (-3.0, 5.0, 3.0, "stop3_trail5"),
    ):
        specs.append(
            mk(
                f"sol_{tag}",
                f"Solana {tag}",
                "Exit-grid experiment on Solana",
                chains=["solana"],
                hard_stop_pct=stop,
                trail_activate_pct=trail_on,
                trail_drawdown_pct=trail_dd,
            )
        )

    for thr, tag in ((0.55, "thr55"), (0.68, "thr68"), (0.75, "thr75")):
        specs.append(
            mk(
                f"sol_score_{tag}",
                f"Solana score {tag}",
                "Score-threshold sweep",
                chains=["solana"],
                score_threshold=thr,
            )
        )

    for pct, npos, tag in ((0.05, 5, "size5x5"), (0.10, 3, "size10x3"), (0.15, 2, "size15x2")):
        specs.append(
            mk(
                f"sol_{tag}",
                f"Solana {tag}",
                "Position sizing sweep",
                chains=["solana"],
                max_position_pct_equity=pct,
                max_open_positions=npos,
                score_threshold=0.62,
            )
        )

    # Multi-chain baskets
    specs.extend(
        [
            mk(
                "multi_meme_hot",
                "Hot meme basket",
                "solana+base+bsc",
                chains=["solana", "base", "bsc"],
                gecko_new_pool_pages=1,
                min_volume_1h_usd=50000,
                min_liquidity_usd=20000,
                min_lp_locked_pct=80,
                max_tax_pct=8,
                rugcheck_rpc_cap_per_cycle=6,
                scout_interval_sec=55,
            ),
            mk(
                "multi_l2_plus",
                "L2 plus",
                "base+arb+op+blast",
                chains=["base", "arbitrum", "optimism", "blast"],
                gecko_new_pool_pages=1,
                min_volume_1h_usd=35000,
                min_liquidity_usd=12000,
                min_lp_locked_pct=75,
                max_tax_pct=8,
                rugcheck_rpc_cap_per_cycle=6,
                scout_interval_sec=55,
            ),
            mk(
                "multi_alt_evm",
                "Alt EVM basket",
                "polygon+avalanche+linea",
                chains=["polygon", "avalanche", "linea"],
                gecko_new_pool_pages=1,
                min_volume_1h_usd=30000,
                min_liquidity_usd=12000,
                min_lp_locked_pct=75,
                max_tax_pct=8,
                rugcheck_rpc_cap_per_cycle=6,
                scout_interval_sec=55,
            ),
            mk(
                "multi_universe",
                "Full universe",
                "All registered chains — slow scout, shared book",
                chains=[
                    "solana",
                    "base",
                    "ethereum",
                    "bsc",
                    "arbitrum",
                    "polygon",
                    "avalanche",
                    "optimism",
                    "blast",
                    "linea",
                ],
                gecko_new_pool_pages=1,
                min_volume_1h_usd=70000,
                min_liquidity_usd=30000,
                min_lp_locked_pct=80,
                max_tax_pct=8,
                rugcheck_rpc_cap_per_cycle=5,
                scout_interval_sec=90,
                max_open_positions=5,
            ),
            mk(
                "bsc_scalp",
                "BSC scalp",
                "Fast exits on BSC",
                chains=["bsc"],
                gecko_new_pool_pages=1,
                min_volume_1h_usd=40000,
                min_liquidity_usd=15000,
                hard_stop_pct=-4.0,
                trail_activate_pct=6.0,
                trail_drawdown_pct=4.0,
                max_hold_minutes=90,
                max_tax_pct=5,
                min_lp_locked_pct=80,
            ),
            mk(
                "eth_wide_trail",
                "ETH wide trail",
                "Let ETH winners run",
                chains=["ethereum"],
                gecko_new_pool_pages=1,
                min_volume_1h_usd=120000,
                min_liquidity_usd=100000,
                trail_activate_pct=20.0,
                trail_drawdown_pct=10.0,
                max_hold_minutes=720,
                max_tax_pct=5,
                min_lp_locked_pct=85,
                network_fee_usd=0.50,
                max_open_positions=2,
            ),
        ]
    )

    # --- Wave 3 (v2): live stops during scout + exit management + entry timing ---
    # 107/145 closes were hard stops (−$372) vs trailing +$73; 30 stopped trades had been ≥+6% first.
    fast = dict(reprice_during_scout=True)
    be = dict(breakeven_activate_pct=5.0, breakeven_lock_pct=1.5)
    tiers = dict(trail_tiers=[[6, 4], [15, 7], [40, 12]])
    no_chase = dict(max_price_change_1h_pct=100, min_price_change_5m_pct=0.0, min_buy_share_5m=0.55)
    quality = dict(min_volume_1h_usd=150000, min_liquidity_usd=50000, min_buy_share_1h=0.62)
    partial_tp = dict(take_profit_pct=12.0, take_profit_fraction=0.5)
    stale = dict(no_progress_minutes=20, no_progress_min_peak_pct=2.0)
    v2_evm = dict(gecko_new_pool_pages=1, min_lp_locked_pct=80, max_tax_pct=8, scout_interval_sec=40)

    specs.extend(
        [
            mk("v2_fastrisk", "v2 fast risk", "Control + reprice during scout (isolates the stop-lag fix)",
               chains=["solana"], **fast),
            mk("v2_breakeven", "v2 breakeven", "Fast risk + stop moves to +1.5% after +5%",
               chains=["solana"], **fast, **be),
            mk("v2_tiered", "v2 tiered trail", "Fast risk + breakeven + trail 6/4, 15/7, 40/12",
               chains=["solana"], **fast, **be, **tiers),
            mk("v2_partial_tp", "v2 partial TP", "v2_tiered + sell half at +12%, runner trails",
               chains=["solana"], **fast, **be, **tiers, **partial_tp),
            mk("v2_no_chase", "v2 no chase", "v2_tiered + skip >100% 1h pumps, need green 5m and 55% 5m buys",
               chains=["solana"], **fast, **be, **tiers, **no_chase),
            mk("v2_no_progress", "v2 no progress", "v2_tiered + exit after 20m if never +2%",
               chains=["solana"], **fast, **be, **tiers, **stale),
            mk("v2_quality", "v2 quality", "v2_tiered + vol≥150k, liq≥50k, 1h buy share≥62%",
               chains=["solana"], **fast, **be, **tiers, **quality),
            mk("v2_combo", "v2 combo", "All v2 levers on Solana",
               chains=["solana"], **fast, **be, **tiers, **no_chase, **quality, **partial_tp, **stale),
            mk("v2_scalp_be", "v2 scalp breakeven", "Early lock (+3→+1%), tight tiers, −5% stop",
               chains=["solana"], **fast, hard_stop_pct=-5.0,
               breakeven_activate_pct=3.0, breakeven_lock_pct=1.0,
               trail_tiers=[[4, 3], [10, 5], [30, 10]], **no_chase),
            mk("v2_combo_sol_base", "v2 combo Sol+Base", "v2 combo on solana+base (best W/L basket)",
               chains=["solana", "base"], gecko_new_pool_pages=1, min_lp_locked_pct=90, scout_interval_sec=45,
               **fast, **be, **tiers, **no_chase, **partial_tp, **stale,
               min_volume_1h_usd=100000, min_liquidity_usd=40000, min_buy_share_1h=0.6),
            mk("v2_eth_wide", "v2 ETH wide", "eth_wide_trail (only green desk) + fast risk + breakeven",
               chains=["ethereum"], gecko_new_pool_pages=1, min_volume_1h_usd=120000, min_liquidity_usd=100000,
               trail_activate_pct=20.0, trail_drawdown_pct=10.0, max_hold_minutes=720, max_tax_pct=5,
               min_lp_locked_pct=85, network_fee_usd=0.50, max_open_positions=2,
               **fast, breakeven_activate_pct=8.0, breakeven_lock_pct=2.0),
            # EVM desks rejected ~95% on volume_low at Solana-sized floors; test chain-sized floors.
            mk("v2_base_lowvol", "v2 Base low-vol", "Base with 15k/10k floors + v2 exits + no chase",
               chains=["base"], min_volume_1h_usd=15000, min_liquidity_usd=10000,
               **v2_evm, **fast, **be, **tiers, **no_chase, **stale),
            mk("v2_bsc_lowvol", "v2 BSC low-vol", "BSC with 20k/10k floors + v2 exits + no chase",
               chains=["bsc"], min_volume_1h_usd=20000, min_liquidity_usd=10000, max_tax_pct=5,
               **{k: v for k, v in v2_evm.items() if k != "max_tax_pct"}, **fast, **be, **tiers, **no_chase, **stale),
            mk("v2_evm_lowvol", "v2 EVM low-vol basket", "base+bsc+arbitrum at 15k/10k floors + v2 exits",
               chains=["base", "bsc", "arbitrum"], min_volume_1h_usd=15000, min_liquidity_usd=10000,
               rugcheck_rpc_cap_per_cycle=8, **v2_evm, **fast, **be, **tiers, **no_chase, **stale),
        ]
    )

    # --- Wave 4 (v3): guards + entry styles that pick different tokens ---
    # 107 trades were re-buys of one token (ANAKIN, −$120); XTO was bought after −92% in 1h and rugged (−$254).
    guard = dict(loss_reentry_block_hours=6, min_price_change_1h_pct=-20, min_price_change_6h_pct=-40)
    v2_combo_keys = dict(**fast, **be, **tiers, **no_chase, **quality, **partial_tp, **stale)

    specs.extend(
        [
            mk("v3_control_guard", "v3 control + guard", "Control + re-entry block + crash floors only",
               chains=["solana"], **guard),
            mk("v3_tiered_guard", "v3 tiered + guard", "v2_tiered + guard (isolates the guard)",
               chains=["solana"], **fast, **be, **tiers, **guard),
            mk("v3_partial_guard", "v3 partial TP + guard", "v2_partial_tp + guard",
               chains=["solana"], **fast, **be, **tiers, **partial_tp, **guard),
            mk("v3_quality_guard", "v3 quality + guard", "v2_quality (best active v2) + guard",
               chains=["solana"], **fast, **be, **tiers, **quality, **guard),
            mk("v3_combo_guard", "v3 combo + guard", "v2_combo + guard",
               chains=["solana"], **v2_combo_keys, **guard),
            mk("v3_combo_sol_base_guard", "v3 combo Sol+Base + guard", "v2_combo_sol_base + guard",
               chains=["solana", "base"], gecko_new_pool_pages=1, min_lp_locked_pct=90, scout_interval_sec=45,
               **fast, **be, **tiers, **no_chase, **partial_tp, **stale, **guard,
               min_volume_1h_usd=100000, min_liquidity_usd=40000, min_buy_share_1h=0.6),
            mk("v3_trend_up", "v3 trend up", "Only steady uptrends: 1h +5..+60%, 6h green, 5m green",
               chains=["solana"], **fast, **be, **tiers, **stale, loss_reentry_block_hours=6,
               min_price_change_1h_pct=5, max_price_change_1h_pct=60, min_price_change_6h_pct=0,
               min_price_change_5m_pct=0.0, min_buy_share_5m=0.55, min_volume_1h_usd=80000),
            mk("v3_dip_buy", "v3 dip buy", "Pullback in an uptrend: 6h ≥+20%, 1h −15..0%, 5m turning up",
               chains=["solana"], **fast, **be, **tiers, **stale, loss_reentry_block_hours=6,
               min_price_change_6h_pct=20, min_price_change_1h_pct=-15, max_price_change_1h_pct=0,
               min_price_change_5m_pct=0.5, min_buy_share_5m=0.55, min_volume_1h_usd=80000),
            mk("v3_young_momo", "v3 young momentum", "Pools 1–6h old (away from the 15h+ cluster) + guard",
               chains=["solana"], **fast, **be, **tiers, **stale, **guard,
               min_pair_age_hours=1, max_pair_age_hours=6, min_volume_1h_usd=60000, min_liquidity_usd=25000,
               max_price_change_1h_pct=150, min_buy_share_5m=0.55),
            mk("v3_deep_few", "v3 deep & few", "Liq-weighted score, liq≥150k, 3 slots at 10% + guard",
               chains=["solana"], **fast, **be, **tiers, **guard,
               min_liquidity_usd=150000, min_volume_1h_usd=150000, max_open_positions=3,
               max_position_pct_equity=0.10, score_threshold=0.65,
               score_weights={"liquidity": 0.40, "momentum": 0.15, "holders": 0.20, "age": 0.10, "buy_sell": 0.15}),
            # EVM v2 desks never traded: timing rules rejected the few tokens that cleared volume.
            mk("v3_evm_open", "v3 EVM open", "base+bsc+arbitrum at 10k/8k floors; crash floor + re-entry block only",
               chains=["base", "bsc", "arbitrum"], min_volume_1h_usd=10000, min_liquidity_usd=8000,
               rugcheck_rpc_cap_per_cycle=8, **v2_evm, **fast, **be, **tiers, **stale, **guard),
        ]
    )

    # --- Wave 5 (v4): breakeven lock above round-trip cost ---
    # Slippage 1%/side + 0.3% fee/side ≈ 2.6% round trip, so a +1.5% lock still lost (11 exits, −$17).
    be3 = dict(breakeven_activate_pct=6.0, breakeven_lock_pct=3.0)
    specs.extend(
        [
            mk("v4_tiered_guard", "v4 tiered + guard", "v3_tiered_guard with breakeven lock +3% after +6%",
               chains=["solana"], **fast, **be3, **tiers, **guard),
            mk("v4_quality_guard", "v4 quality + guard", "v3_quality_guard with breakeven lock +3% after +6%",
               chains=["solana"], **fast, **be3, **tiers, **quality, **guard),
            mk("v4_combo_guard", "v4 combo + guard", "v3_combo_guard with breakeven lock +3% after +6%",
               chains=["solana"], **{**v2_combo_keys, **be3}, **guard),
            mk("v4_combo_sol_base_guard", "v4 combo Sol+Base + guard",
               "v3_combo_sol_base_guard with breakeven lock +3% after +6%",
               chains=["solana", "base"], gecko_new_pool_pages=1, min_lp_locked_pct=90, scout_interval_sec=45,
               **fast, **be3, **tiers, **no_chase, **partial_tp, **stale, **guard,
               min_volume_1h_usd=100000, min_liquidity_usd=40000, min_buy_share_1h=0.6),
        ]
    )

    # --- Wave 6 (v5): clones of the only desk printing profit so far (v4_tiered_guard) ---
    # Bias harder toward trailing-stop exits (the only exit type netting positive lab-wide).
    early_trail = dict(trail_tiers=[[4, 3], [10, 6], [25, 12]])
    specs.extend(
        [
            mk(
                "v5_runner",
                "v5 runner",
                "v4_tiered_guard with earlier trail (4/3, 10/6, 25/12) so more trades lock gains",
                chains=["solana"],
                **fast,
                **be3,
                **early_trail,
                **guard,
            ),
            mk(
                "v5_quality_runner",
                "v5 quality runner",
                "v5_runner + quality floors (vol≥150k, liq≥50k, buy share≥62%)",
                chains=["solana"],
                **fast,
                **be3,
                **early_trail,
                **quality,
                **guard,
            ),
            mk(
                "v5_runner_sol_base",
                "v5 runner Sol+Base",
                "v5_runner on solana+base",
                chains=["solana", "base"],
                gecko_new_pool_pages=1,
                min_lp_locked_pct=90,
                scout_interval_sec=45,
                **fast,
                **be3,
                **early_trail,
                **guard,
                min_volume_1h_usd=80000,
                min_liquidity_usd=30000,
            ),
        ]
    )

    for cfg in specs:
        write(cfg)

    # catalog (variants/ only + control)
    catalog = []
    with CONTROL.open(encoding="utf-8") as f:
        c = json.load(f)
    catalog.append(
        {"id": c["id"], "name": c["name"], "chains": c.get("chains"), "description": c.get("description")}
    )
    for p in sorted(OUT.glob("*.json")):
        with p.open(encoding="utf-8") as f:
            d = json.load(f)
        catalog.append(
            {
                "id": d.get("id"),
                "name": d.get("name"),
                "chains": d.get("chains"),
                "description": d.get("description"),
            }
        )
    cat_path = ROOT / "configs" / "variants_catalog.json"
    cat_path.write_text(json.dumps(catalog, indent=2) + "\n", encoding="utf-8")
    print(f"catalog {len(catalog)} configs -> {cat_path}")


if __name__ == "__main__":
    main()
