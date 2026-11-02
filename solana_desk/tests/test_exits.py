"""Unit tests for opt-in exit rules, entry timing filter, and the shared HTTP cache."""
from __future__ import annotations

import logging
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from desk import http_client  # noqa: E402
from desk.sources import dexscreener  # noqa: E402
from desk.stages import risk  # noqa: E402
from desk.stages.filter import _entry_timing_ok  # noqa: E402

_REAL_BATCH = dexscreener.enrich_tokens_batch


class _Log:
    def __init__(self) -> None:
        self.l = logging.getLogger("test")

    def info(self, *a, **k):
        pass

    def warning(self, *a, **k):
        pass

    def event(self, *a, **k):
        pass


BASE_CFG = {
    "hard_stop_pct": -6.0,
    "trail_activate_pct": 12.0,
    "trail_drawdown_pct": 6.0,
    "max_hold_minutes": 360,
    "rug_liquidity_drop_pct": 60.0,
    "slippage_pct": 0.0,
    "dex_fee_pct": 0.0,
    "network_fee_usd": 0.0,
    "price_impact_coeff": 0.0,
}


def _state(peak: float = 1.0, opened_min_ago: float = 1.0) -> dict:
    return {
        "cash_usd": 0.0,
        "positions": [
            {
                "id": "p1",
                "chain": "solana",
                "mint": "M",
                "opened_ts": time.time() - opened_min_ago * 60,
                "entry_fill_price": 1.0,
                "entry_decision_price": 1.0,
                "tokens": 100.0,
                "cost_usd": 100.0,
                "entry_fees_usd": 0.0,
                "entry_liquidity_usd": 1e6,
                "peak_price": peak,
            }
        ],
        "closed_trades": [],
        "counters": {},
    }


def _run(cfg: dict, state: dict, price: float) -> dict:
    risk.dexscreener.enrich_tokens_batch = lambda *a, **k: {}
    risk.dexscreener.price_snapshot = lambda *a, **k: {"price_usd": price, "liquidity_usd": 1e6}
    path = Path(tempfile.mkdtemp()) / "state.json"
    risk.reprice_and_manage(None, {**BASE_CFG, **cfg}, state, path, _Log())
    return state


def test_control_unchanged_without_new_keys():
    st = _run({}, _state(peak=1.08), price=1.01)
    assert st["positions"] and not st["closed_trades"]


def test_breakeven_lock():
    cfg = {"breakeven_activate_pct": 5.0, "breakeven_lock_pct": 1.5}
    st = _run(cfg, _state(peak=1.08), price=1.01)
    assert st["closed_trades"][0]["reason"] == "breakeven_stop"
    st = _run(cfg, _state(peak=1.03), price=1.01)
    assert not st["closed_trades"]


def test_trail_tiers_use_highest_reached():
    cfg = {"trail_tiers": [[5, 4], [20, 10]]}
    st = _run(cfg, _state(peak=1.10), price=1.05)  # 4.5% off peak, tier 5 -> exit
    assert st["closed_trades"][0]["reason"] == "trailing_stop"
    st = _run(cfg, _state(peak=1.25), price=1.19)  # 4.8% off peak, tier 20 allows 10%
    assert not st["closed_trades"]


def test_partial_take_profit_keeps_runner():
    cfg = {"take_profit_pct": 10.0, "take_profit_fraction": 0.5}
    st = _run(cfg, _state(peak=1.0), price=1.12)
    assert len(st["closed_trades"]) == 1 and st["closed_trades"][0]["partial"]
    pos = st["positions"][0]
    assert abs(pos["tokens"] - 50.0) < 1e-9 and abs(pos["cost_usd"] - 50.0) < 1e-9
    assert pos["tp_done"]
    _run(cfg, st, price=1.13)
    assert len(st["closed_trades"]) == 1


def test_no_progress_exit():
    cfg = {"no_progress_minutes": 20, "no_progress_min_peak_pct": 2.0}
    st = _run(cfg, _state(peak=1.01, opened_min_ago=25), price=1.0)
    assert st["closed_trades"][0]["reason"] == "no_progress"
    st = _run(cfg, _state(peak=1.05, opened_min_ago=25), price=1.0)
    assert not st["closed_trades"]


def test_entry_timing():
    cand = {"price_change_1h_pct": 40, "price_change_5m_pct": 1.0, "buys_5m": 30, "sells_5m": 10}
    assert _entry_timing_ok({}, cand)[0]
    assert _entry_timing_ok({"max_price_change_1h_pct": 30}, cand) == (False, "chasing_1h_pump")
    assert _entry_timing_ok({"min_price_change_5m_pct": 2}, cand) == (False, "falling_5m")
    assert _entry_timing_ok({"min_buy_share_5m": 0.6}, cand)[0]
    assert _entry_timing_ok({"min_buy_share_5m": 0.8}, cand) == (False, "buy_share_5m_low")
    assert _entry_timing_ok({"min_buy_share_5m": 0.5}, {"buys_5m": 2, "sells_5m": 1}) == (False, "txns_5m_thin")


def test_loss_blocks_reentry_and_rug_blocks_forever():
    from desk.stages.filter import _mem_key, memory_blocks

    key = _mem_key({"chain": "solana", "mint": "M"})
    st = _run({"loss_reentry_block_hours": 6}, _state(peak=1.0), price=0.90)
    blocked = memory_blocks(st, key, time.time() + 5 * 3600)
    assert blocked and blocked.startswith("soft_cooldown:lost_on_us")
    assert memory_blocks(st, key, time.time() + 7 * 3600) is None

    st = _state(peak=1.0)
    risk.dexscreener.enrich_tokens_batch = lambda *a, **k: {}
    risk.dexscreener.price_snapshot = lambda *a, **k: {"price_usd": 0.05, "liquidity_usd": 1e5}
    risk.reprice_and_manage(None, {**BASE_CFG, "loss_reentry_block_hours": 6}, st,
                            Path(tempfile.mkdtemp()) / "s.json", _Log())
    assert st["closed_trades"][0]["reason"] == "rug_liquidity_drop"
    assert memory_blocks(st, key, time.time() + 10 * 86400) == "hard:rugged_on_us"

    st = _run({}, _state(peak=1.0), price=0.90)
    assert memory_blocks(st, key, time.time()) is None


def test_crash_floors():
    cand = {"price_change_1h_pct": -92, "price_change_6h_pct": -95}
    assert _entry_timing_ok({"min_price_change_1h_pct": -20}, cand) == (False, "crashed_1h")
    assert _entry_timing_ok({"min_price_change_6h_pct": -40}, cand) == (False, "crashed_6h")
    assert _entry_timing_ok({"min_price_change_1h_pct": -20}, {"price_change_1h_pct": 5})[0]


def test_http_cache_dedupes():
    http_client.CACHE_DIR = Path(tempfile.mkdtemp())
    c = http_client.HttpClient()
    calls = []

    def fake(url, *, params, source):
        calls.append(url)
        return {"n": len(calls)}

    c._get_json_live = fake
    assert c.get_json("https://x/a", source="gecko.x") == {"n": 1}
    assert c.get_json("https://x/a", source="gecko.x") == {"n": 1}
    assert c.get_json("https://x/a", source="solana.rpc") == {"n": 2}
    assert len(calls) == 2


def test_http_stale_fallback_only_for_slow_sources():
    http_client.CACHE_DIR = Path(tempfile.mkdtemp())
    c = http_client.HttpClient()
    answers = [{"v": 1}, {"p": 1}, None, None]
    c._get_json_live = lambda url, *, params, source: answers.pop(0)
    assert c.get_json("https://x/pools", source="gecko.x") == {"v": 1}
    assert c.get_json("https://x/price", source="dex.token") == {"p": 1}
    old = time.time() - 600
    for p in http_client.CACHE_DIR.glob("*.json"):
        os.utime(p, (old, old))
    assert c.get_json("https://x/pools", source="gecko.x") == {"v": 1}  # live failed -> stale list
    assert c.get_json("https://x/price", source="dex.token") is None  # prices are never stale


def test_batch_enrich_uses_base_token_deepest_pair():
    class H:
        def __init__(self):
            self.urls = []

        def get_json(self, url, *, source):
            self.urls.append(url)
            return [
                {"baseToken": {"address": "AAA"}, "priceUsd": "1", "liquidity": {"usd": 10}},
                {"baseToken": {"address": "AAA"}, "priceUsd": "2", "liquidity": {"usd": 50}},
                {"baseToken": {"address": "WSOL"}, "quoteToken": {"address": "BBB"}, "priceUsd": "9", "liquidity": {"usd": 99}},
            ]

    h = H()
    out = _REAL_BATCH(h, [("solana", "AAA"), ("solana", "BBB"), ("solana", "AAA")])
    assert len(h.urls) == 1 and h.urls[0].endswith("/solana/AAA,BBB")
    assert out[("solana", "AAA")]["price_usd"] == 2.0
    assert ("solana", "BBB") not in out


def test_paced_host_cooldown_skips_calls():
    http_client.CACHE_DIR = Path(tempfile.mkdtemp())
    url = "https://api.geckoterminal.com/api/v2/x"
    assert http_client._take_host_slot(url)
    http_client._start_host_cooldown(url)
    assert not http_client._take_host_slot(url)
    assert http_client._take_host_slot("https://api.dexscreener.com/x")


if __name__ == "__main__":
    test_control_unchanged_without_new_keys()
    test_breakeven_lock()
    test_trail_tiers_use_highest_reached()
    test_partial_take_profit_keeps_runner()
    test_no_progress_exit()
    test_entry_timing()
    test_loss_blocks_reentry_and_rug_blocks_forever()
    test_crash_floors()
    test_http_cache_dedupes()
    test_http_stale_fallback_only_for_slow_sources()
    test_batch_enrich_uses_base_token_deepest_pair()
    test_paced_host_cooldown_skips_calls()
    print("ok")
