#!/usr/bin/env python3
"""Profit rules layered on quality A∩B buys.

Session window, SPY regime filter, RVOL top-N, position sizing, and exit plan.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


@dataclass
class ExitPlan:
    # TP raised toward sweet-spot avg (~+2.5–2.8%); give winners time to tag it
    take_profit_pct: float = 2.25
    stop_loss_pct: float = 1.0
    time_stop_minutes: float = 30.0
    confirm_minutes: float = 2.0  # wait ~1 bar before entry if using confirmation

    def targets(self, entry: float) -> dict[str, float]:
        return {
            "entry": entry,
            "take_profit": round(entry * (1.0 + self.take_profit_pct / 100.0), 4),
            "stop_loss": round(entry * (1.0 - self.stop_loss_pct / 100.0), 4),
            "take_profit_pct": self.take_profit_pct,
            "stop_loss_pct": self.stop_loss_pct,
            "time_stop_minutes": self.time_stop_minutes,
            "confirm_minutes": self.confirm_minutes,
        }


@dataclass
class SessionConfig:
    """Regular-session focus: prefer open drive, skip overnight noise."""

    allow_premarket: bool = False
    allow_afterhours: bool = False
    # Prefer first N minutes after 09:30 ET (0 = whole regular session)
    open_window_minutes: int = 90
    regular_open: time = time(9, 30)
    regular_close: time = time(16, 0)


@dataclass
class SizingConfig:
    # Concentrate capital: fewer, better names (small-account friendly)
    top_n: int = 3
    # RVOL bands → relative size (1.0 = base unit). Peak size in 2.5–4 sweet spot.
    rvol_soft_cap: float = 5.0  # at/above soft cap → tiny
    size_low: float = 0.5   # below mid (should be rare with min_rvol=2.5)
    size_mid: float = 1.5   # 2.5–4.0 sweet spot
    size_high: float = 1.0  # 4.0–soft_cap
    size_extreme: float = 0.25
    mid_rvol: float = 2.5
    high_rvol: float = 4.0
    sweet_rvol: float = 3.2  # rank preference center
    range_bonus: float = 15.0  # rank boost when range_expansion present


def now_et(ts: datetime | None = None) -> datetime:
    ts = ts or datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(ET)


def in_regular_session(ts: datetime | None = None, cfg: SessionConfig | None = None) -> bool:
    cfg = cfg or SessionConfig()
    local = now_et(ts)
    if local.weekday() >= 5:
        return False
    t = local.time()
    return cfg.regular_open <= t < cfg.regular_close


def in_trade_window(ts: datetime | None = None, cfg: SessionConfig | None = None) -> bool:
    """True if we are allowed to open new buys now."""
    cfg = cfg or SessionConfig()
    local = now_et(ts)
    if local.weekday() >= 5:
        return False
    t = local.time()
    if cfg.regular_open <= t < cfg.regular_close:
        if cfg.open_window_minutes and cfg.open_window_minutes > 0:
            open_dt = local.replace(
                hour=cfg.regular_open.hour,
                minute=cfg.regular_open.minute,
                second=0,
                microsecond=0,
            )
            mins = (local - open_dt).total_seconds() / 60.0
            return 0 <= mins <= cfg.open_window_minutes
        return True
    if t < cfg.regular_open:
        return cfg.allow_premarket
    return cfg.allow_afterhours


def session_label(ts: datetime | None = None, cfg: SessionConfig | None = None) -> str:
    cfg = cfg or SessionConfig()
    local = now_et(ts)
    t = local.time()
    if local.weekday() >= 5:
        return "weekend"
    if t < cfg.regular_open:
        return "premarket"
    if t >= cfg.regular_close:
        return "afterhours"
    if in_trade_window(ts, cfg):
        return "open_window"
    return "regular_late"


def spy_regime_ok(
    *,
    require_green: bool = True,
    min_chg_pct: float = -0.1,
    ticker: str = "SPY",
) -> tuple[bool, dict[str, Any]]:
    """SPY day-change filter. Allow mild red by default (min_chg_pct=-0.1).

    Fail-open if data missing so a Yahoo blip doesn't freeze the scanner.
    """
    info: dict[str, Any] = {
        "ticker": ticker,
        "ok": True,
        "reason": "skipped",
        "min_chg_pct": min_chg_pct,
    }
    if not require_green:
        return True, info
    try:
        import yfinance as yf

        raw = yf.download(
            ticker,
            period="5d",
            interval="5m",
            auto_adjust=True,
            progress=False,
            threads=False,
        )
        if raw is None or raw.empty:
            info["reason"] = "no_data"
            return True, info
        if hasattr(raw.columns, "nlevels") and raw.columns.nlevels > 1:
            raw.columns = [c[0] for c in raw.columns]
        # last calendar day in index
        idx = raw.index
        last_day = idx[-1].date() if hasattr(idx[-1], "date") else idx[-1]
        day = raw[[hasattr(i, "date") and i.date() == last_day for i in idx]]
        if day.empty:
            day = raw.tail(78)  # ~1 RTH of 5m bars
        day_open = float(day["Open"].iloc[0])
        last = float(day["Close"].iloc[-1])
        chg = ((last - day_open) / day_open * 100.0) if day_open else 0.0
        ok = chg >= float(min_chg_pct)
        info.update(
            {
                "ok": ok,
                "day_open": day_open,
                "last": last,
                "chg_pct": round(chg, 3),
                "reason": (
                    "spy_ok"
                    if ok
                    else f"spy_below_min:{round(chg, 3)}<{min_chg_pct}"
                ),
            }
        )
        return ok, info
    except Exception as exc:  # noqa: BLE001
        info["reason"] = f"error:{exc}"
        return True, info


def size_multiplier(rvol: float | None, cfg: SizingConfig | None = None) -> float:
    cfg = cfg or SizingConfig()
    try:
        rv = float(rvol) if rvol is not None else 0.0
    except (TypeError, ValueError):
        rv = 0.0
    if rv <= 0:
        return 0.0
    if rv >= cfg.rvol_soft_cap:
        return cfg.size_extreme
    if rv >= cfg.high_rvol:
        return cfg.size_high
    if rv >= cfg.mid_rvol:
        return cfg.size_mid
    return cfg.size_low


def _rank_score(row: dict[str, Any], sizing: SizingConfig) -> float:
    """Prefer sweet-spot RVOL (~3.2) and range_expansion over raw max RVOL."""
    try:
        rv = float(row.get("rel_volume") or 0.0)
    except (TypeError, ValueError):
        rv = 0.0
    score = 100.0 - abs(rv - sizing.sweet_rvol) * 12.0
    pats = set(row.get("patterns") or [])
    if "range_expansion" in pats:
        score += sizing.range_bonus
    if sizing.mid_rvol <= rv < sizing.high_rvol:
        score += 8.0
    return score


def rank_and_size(
    buys: list[dict[str, Any]],
    *,
    sizing: SizingConfig | None = None,
    exits: ExitPlan | None = None,
) -> list[dict[str, Any]]:
    """Rank by sweet-spot quality, keep top_n, attach size + exit plan."""
    sizing = sizing or SizingConfig()
    exits = exits or ExitPlan()
    scored: list[dict[str, Any]] = []
    for r in buys:
        row = dict(r)
        try:
            rv = float(row.get("rel_volume") or 0.0)
        except (TypeError, ValueError):
            rv = 0.0
        row["_rank_score"] = _rank_score(row, sizing)
        row["size_mult"] = size_multiplier(rv, sizing)
        px = row.get("price")
        try:
            px_f = float(px) if px is not None else None
        except (TypeError, ValueError):
            px_f = None
        if px_f is not None:
            row["exit_plan"] = exits.targets(px_f)
        else:
            row["exit_plan"] = asdict(exits)
        scored.append(row)
    scored.sort(key=lambda x: x.get("_rank_score") or 0.0, reverse=True)
    out = scored[: max(1, sizing.top_n)]
    for i, row in enumerate(out, start=1):
        row["rank"] = i
        row.pop("_rank_score", None)
    return out


def simulate_exit(
    entry: float,
    path: list[tuple[float, float]],
    plan: ExitPlan | None = None,
) -> dict[str, Any]:
    """path = list of (minutes_after_entry, price). Returns exit result."""
    plan = plan or ExitPlan()
    if not path or entry <= 0:
        return {"exit": None, "return_pct": None, "reason": "no_path"}
    tp = entry * (1.0 + plan.take_profit_pct / 100.0)
    sl = entry * (1.0 - plan.stop_loss_pct / 100.0)
    # optional confirmation: ignore prices before confirm_minutes for entry fill
    filled = False
    fill_px = entry
    for mins, px in path:
        if mins >= plan.confirm_minutes:
            filled = True
            fill_px = px
            tp = fill_px * (1.0 + plan.take_profit_pct / 100.0)
            sl = fill_px * (1.0 - plan.stop_loss_pct / 100.0)
            break
    if not filled:
        mins, px = path[-1]
        return {
            "exit": px,
            "return_pct": (px - entry) / entry * 100.0,
            "reason": "no_confirm",
            "minutes": mins,
        }
    for mins, px in path:
        if mins < plan.confirm_minutes:
            continue
        if px >= tp:
            return {
                "exit": tp,
                "return_pct": plan.take_profit_pct,
                "reason": "take_profit",
                "minutes": mins,
                "fill": fill_px,
            }
        if px <= sl:
            return {
                "exit": sl,
                "return_pct": -plan.stop_loss_pct,
                "reason": "stop_loss",
                "minutes": mins,
                "fill": fill_px,
            }
        if mins >= plan.time_stop_minutes:
            return {
                "exit": px,
                "return_pct": (px - fill_px) / fill_px * 100.0,
                "reason": "time_stop",
                "minutes": mins,
                "fill": fill_px,
            }
    mins, px = path[-1]
    return {
        "exit": px,
        "return_pct": (px - fill_px) / fill_px * 100.0,
        "reason": "path_end",
        "minutes": mins,
        "fill": fill_px,
    }
