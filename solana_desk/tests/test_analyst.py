"""Unit tests for control scoring formulas."""
from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from desk.stages.analyst import (  # noqa: E402
    clamp,
    score_age,
    score_buy_sell,
    score_candidate,
    score_liquidity,
    score_momentum,
)


def test_liquidity_anchors():
    assert abs(score_liquidity(10_000) - 0.0) < 1e-9
    assert abs(score_liquidity(500_000) - 1.0) < 1e-9
    mid = score_liquidity(math.sqrt(10_000 * 500_000))
    assert 0.4 < mid < 0.6


def test_age_curve():
    assert abs(score_age(0) - 0.0) < 1e-9
    assert abs(score_age(10 / 60) - 0.5) < 1e-9  # 10 min
    assert abs(score_age(30 / 60) - 1.0) < 1e-9
    assert abs(score_age(3.0) - 1.0) < 1e-9  # 3h inside plateau
    assert abs(score_age(72.0) - 0.3) < 1e-6


def test_buy_sell_map():
    assert abs(score_buy_sell({"buys_1h": 40, "sells_1h": 60}) - 0.0) < 1e-9
    assert abs(score_buy_sell({"buys_1h": 70, "sells_1h": 30}) - 1.0) < 1e-9


def test_momentum_chase_halves():
    base = {
        "volume_5m_usd": 5000,
        "volume_1h_usd": 50000,
        "volume_6h_usd": 200000,
        "price_change_1h_pct": 10,
    }
    hot = dict(base, price_change_1h_pct=400)
    assert abs(score_momentum(hot) - 0.5 * score_momentum(base)) < 1e-9


def test_threshold_path():
    cfg = {
        "score_threshold": 0.6,
        "score_weights": {
            "liquidity": 0.25,
            "momentum": 0.20,
            "holders": 0.20,
            "age": 0.15,
            "buy_sell": 0.20,
        },
    }
    cand = {
        "liquidity_usd": 200_000,
        "volume_5m_usd": 20_000,
        "volume_1h_usd": 80_000,
        "volume_6h_usd": 300_000,
        "top10_pct": 10,
        "holder_count": 1000,
        "age_hours": 1.0,
        "buys_1h": 70,
        "sells_1h": 30,
        "price_change_1h_pct": 20,
    }
    s = score_candidate(cand, cfg)
    assert s >= 0.6
    assert 0.0 <= clamp(s) <= 1.0


if __name__ == "__main__":
    test_liquidity_anchors()
    test_age_curve()
    test_buy_sell_map()
    test_momentum_chase_halves()
    test_threshold_path()
    print("ok")
