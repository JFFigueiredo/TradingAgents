"""Strategy variants for profit lab experiments.

`baseline_v1` matches the live rotate defaults. Other variants are experiments —
scored offline by lab_backtest.py; live rotate stays on baseline unless promoted.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Variant:
    id: str
    name: str
    description: str
    # quality filter
    min_rvol: float = 2.5
    max_rvol: float = 4.0
    min_price: float = 5.0
    max_price: float = 200.0
    require_gap_break: bool = True
    require_range_expansion: bool = False
    require_rvol_spike: bool = False
    forbid_bear: bool = True
    # optional fundamentals from tvscreener B
    max_pe: float | None = None
    min_mcap: float | None = None  # USD
    max_mcap: float | None = None
    # session / regime
    open_window_minutes: int = 90
    spy_min_chg_pct: float = -0.1
    require_spy_green: bool = True
    # sizing / exits
    top_n: int = 3
    take_profit_pct: float = 2.25
    stop_loss_pct: float = 1.0
    time_stop_minutes: float = 30.0
    # ranking preference center
    sweet_rvol: float = 3.2
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def all_variants() -> list[Variant]:
    """Baseline first, then experiments. Do not remove baseline_v1."""
    return [
        Variant(
            id="baseline_v1",
            name="Live baseline",
            description="Current live rotate: gap+break, RVOL 2.5-4, top3, TP2.25",
            min_rvol=2.5,
            max_rvol=4.0,
            top_n=3,
            take_profit_pct=2.25,
            open_window_minutes=90,
            spy_min_chg_pct=-0.1,
            tags=["live", "baseline"],
        ),
        Variant(
            id="sweet_tight",
            name="Tight sweet spot",
            description="Narrower RVOL 2.5-3.5, top2 — fewer trades, cleaner mid-band",
            min_rvol=2.5,
            max_rvol=3.5,
            top_n=2,
            sweet_rvol=3.0,
            take_profit_pct=2.25,
            tags=["rvol", "concentrate"],
        ),
        Variant(
            id="range_required",
            name="Range expansion required",
            description="gap+break+range_expansion; RVOL 2.5-5 — historically higher win-rate",
            min_rvol=2.5,
            max_rvol=5.0,
            require_range_expansion=True,
            top_n=3,
            take_profit_pct=2.0,
            tags=["pattern", "winrate"],
        ),
        Variant(
            id="top1_sniper",
            name="Top-1 sniper",
            description="Full capital on single best sweet-spot name; TP 2.5",
            min_rvol=2.5,
            max_rvol=4.0,
            top_n=1,
            take_profit_pct=2.5,
            time_stop_minutes=35.0,
            sweet_rvol=3.2,
            tags=["concentrate", "aggressive"],
        ),
        Variant(
            id="liquid_midprice",
            name="Liquid mid-price",
            description="Price $10-80 only; RVOL 2.5-4 — avoid cheap junk / mega names",
            min_rvol=2.5,
            max_rvol=4.0,
            min_price=10.0,
            max_price=80.0,
            top_n=3,
            tags=["price", "liquidity"],
        ),
        Variant(
            id="early_open60",
            name="First 60 minutes",
            description="Only first hour after open; same sweet RVOL",
            min_rvol=2.5,
            max_rvol=4.0,
            open_window_minutes=60,
            top_n=3,
            tags=["session"],
        ),
        Variant(
            id="spy_strict",
            name="SPY must be green",
            description="No mild-red SPY; same filters as baseline",
            min_rvol=2.5,
            max_rvol=4.0,
            spy_min_chg_pct=0.0,
            top_n=3,
            tags=["regime"],
        ),
        Variant(
            id="rvol_spike_bonus",
            name="Require rvol_spike pattern",
            description="gap+break+rvol_spike; RVOL 2.5-5",
            min_rvol=2.5,
            max_rvol=5.0,
            require_rvol_spike=True,
            top_n=3,
            take_profit_pct=2.25,
            tags=["pattern"],
        ),
        Variant(
            id="wide_net",
            name="Wider net (control)",
            description="RVOL 2.0-6, top5 — more fills, expected weaker edge (control)",
            min_rvol=2.0,
            max_rvol=6.0,
            top_n=5,
            take_profit_pct=1.75,
            time_stop_minutes=25.0,
            tags=["control", "wide"],
        ),
        Variant(
            id="fast_scalp",
            name="Fast scalp",
            description="Tight TP/SL/time; sweet RVOL; harvest quick pops",
            min_rvol=2.5,
            max_rvol=4.0,
            top_n=2,
            take_profit_pct=1.5,
            stop_loss_pct=0.75,
            time_stop_minutes=20.0,
            tags=["exits", "scalp"],
        ),
        Variant(
            id="runner",
            name="Let runners work",
            description="Higher TP + longer time stop on sweet spot",
            min_rvol=2.5,
            max_rvol=4.0,
            top_n=2,
            take_profit_pct=3.0,
            stop_loss_pct=1.0,
            time_stop_minutes=40.0,
            tags=["exits", "runner"],
        ),
        Variant(
            id="valueish_pe",
            name="PE not extreme",
            description="Reject crazy PE (>80) when available; sweet RVOL",
            min_rvol=2.5,
            max_rvol=4.0,
            max_pe=80.0,
            top_n=3,
            tags=["fundamental"],
        ),
        Variant(
            id="range_sweet_combo",
            name="Range + tight sweet RVOL",
            description=(
                "Winner traits combo: require range_expansion AND RVOL 2.5-3.5, "
                "top2, TP 2.5 — highest-conviction intersection"
            ),
            min_rvol=2.5,
            max_rvol=3.5,
            require_range_expansion=True,
            top_n=2,
            take_profit_pct=2.5,
            stop_loss_pct=1.0,
            time_stop_minutes=35.0,
            sweet_rvol=3.0,
            open_window_minutes=90,
            spy_min_chg_pct=-0.1,
            tags=["pattern", "rvol", "combo", "new"],
        ),
        Variant(
            id="gap_quality",
            name="Moderate gap quality",
            description=(
                "Sweet RVOL 2.5-4, price $8-60 (skip micro junk & mega), "
                "top2, mild SPY floor — aim for cleaner gap-and-go names"
            ),
            min_rvol=2.5,
            max_rvol=4.0,
            min_price=8.0,
            max_price=60.0,
            top_n=2,
            take_profit_pct=2.25,
            stop_loss_pct=0.9,
            time_stop_minutes=30.0,
            sweet_rvol=3.2,
            open_window_minutes=75,
            spy_min_chg_pct=-0.05,
            max_pe=100.0,
            tags=["price", "quality", "new"],
        ),
    ]


def get_variant(variant_id: str) -> Variant:
    for v in all_variants():
        if v.id == variant_id:
            return v
    raise KeyError(f"unknown variant: {variant_id}")


def baseline() -> Variant:
    return get_variant("baseline_v1")
