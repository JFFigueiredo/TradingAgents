"""Project A: chart-pattern scan via vendored BennyThadikaran/stock-pattern."""
from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

VENDOR_SRC = Path(__file__).resolve().parents[1] / "vendor" / "stock-pattern" / "src"


def _ensure_vendor() -> None:
    if not VENDOR_SRC.exists():
        raise FileNotFoundError(
            f"stock-pattern vendor missing at {VENDOR_SRC}. "
            "Clone https://github.com/BennyThadikaran/stock-pattern into pipeline/vendor/stock-pattern"
        )
    src = str(VENDOR_SRC)
    if src not in sys.path:
        sys.path.insert(0, src)


# Bullish structural setups (pre-breakout), matching stock-pattern's "bull" preset.
BULL_PATTERN_KEYS = ("vcpu", "hnsu", "dbot", "flagu")
BEAR_PATTERN_KEYS = ("vcpd", "hnsd", "dtop", "flagd")
STRUCTURE_KEYS = ("trng", "uptl", "dntl")  # triangles + trendlines


@dataclass
class PatternScanConfig:
    period: str = "1y"
    interval: str = "1d"
    pattern_set: str = "structure"  # bull | bear | structure | all
    bars_left: int = 6
    bars_right: int = 6
    max_workers: int = 8


def _pattern_fns(pattern_set: str):
    _ensure_vendor()
    import utils  # type: ignore  # noqa: WPS433 — vendored stock-pattern

    fn_dict = {
        "flagu": utils.find_bullish_flag,
        "flagd": utils.find_bearish_flag,
        "vcpu": utils.find_bullish_vcp,
        "dbot": utils.find_double_bottom,
        "hnsu": utils.find_reverse_hns,
        "vcpd": utils.find_bearish_vcp,
        "dtop": utils.find_double_top,
        "hnsd": utils.find_hns,
        "trng": utils.find_triangles,
        "uptl": utils.find_uptrend_line,
        "dntl": utils.find_downtrend_line,
    }
    key = pattern_set.lower()
    if key == "bull":
        keys = BULL_PATTERN_KEYS
    elif key == "bear":
        keys = BEAR_PATTERN_KEYS
    elif key == "structure":
        keys = BULL_PATTERN_KEYS + STRUCTURE_KEYS
    elif key == "all":
        keys = BULL_PATTERN_KEYS + BEAR_PATTERN_KEYS + STRUCTURE_KEYS
    else:
        raise ValueError(
            f"pattern_set must be bull|bear|structure|all, got {pattern_set!r}"
        )
    return tuple(fn_dict[k] for k in keys)


def _normalize_ohlc(df: pd.DataFrame) -> pd.DataFrame:
    cols = {c.lower(): c for c in df.columns}
    rename = {}
    for need in ("open", "high", "low", "close", "volume"):
        if need in cols:
            rename[cols[need]] = need.capitalize() if need != "volume" else "Volume"
            if need != "volume":
                rename[cols[need]] = need.title()
    # yfinance usually already has Open High Low Close Volume
    out = df.rename(
        columns={
            "Open": "Open",
            "High": "High",
            "Low": "Low",
            "Close": "Close",
            "Volume": "Volume",
        }
    )
    needed = ["Open", "High", "Low", "Close", "Volume"]
    missing = [c for c in needed if c not in out.columns]
    if missing:
        raise ValueError(f"OHLC missing columns {missing}")
    out = out[needed].dropna()
    if out.index.has_duplicates:
        out = out.loc[~out.index.duplicated()]
    if not out.index.is_monotonic_increasing:
        out = out.sort_index(ascending=True)
    return out


def _fetch_ohlc(ticker: str, period: str, interval: str) -> pd.DataFrame | None:
    import yfinance as yf

    raw = yf.download(
        ticker,
        period=period,
        interval=interval,
        auto_adjust=True,
        progress=False,
        threads=False,
    )
    if raw is None or raw.empty:
        return None
    # yfinance may return MultiIndex columns for a single ticker
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = [c[0] for c in raw.columns]
    try:
        return _normalize_ohlc(raw)
    except ValueError:
        return None


def _scan_one(
    ticker: str,
    fns: tuple,
    cfg: PatternScanConfig,
) -> list[dict[str, Any]]:
    _ensure_vendor()
    import utils  # type: ignore

    df = _fetch_ohlc(ticker, cfg.period, cfg.interval)
    if df is None or len(df) < 40:
        return []

    pivots = utils.get_max_min(
        df, barsLeft=cfg.bars_left, barsRight=cfg.bars_right, pivot_type="both"
    )
    if pivots is None or len(pivots) == 0:
        return []

    hits: list[dict[str, Any]] = []
    config: dict[str, Any] = {}
    for fn in fns:
        try:
            result = fn(ticker, df, pivots, config)
        except Exception:
            continue
        if not result:
            continue
        if isinstance(result, list):
            for item in result:
                if isinstance(item, dict):
                    row = utils.make_serializable(item)
                    row["ticker"] = ticker
                    row["source"] = "stock-pattern"
                    hits.append(row)
        elif isinstance(result, dict):
            row = utils.make_serializable(result)
            row["ticker"] = ticker
            row["source"] = "stock-pattern"
            hits.append(row)
    return hits


def scan_patterns(
    tickers: list[str],
    cfg: PatternScanConfig | None = None,
) -> list[dict[str, Any]]:
    """Scan tickers for chart patterns. Returns hit dicts with at least ticker+pattern."""
    cfg = cfg or PatternScanConfig()
    fns = _pattern_fns(cfg.pattern_set)
    unique = sorted({t.strip().upper() for t in tickers if t and t.strip()})
    hits: list[dict[str, Any]] = []
    if not unique:
        return hits

    workers = max(1, min(cfg.max_workers, len(unique)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(_scan_one, t, fns, cfg): t for t in unique}
        for fut in as_completed(futs):
            try:
                hits.extend(fut.result())
            except Exception:
                continue
    return hits


def pattern_tickers(hits: list[dict[str, Any]]) -> list[str]:
    return sorted({str(h.get("ticker") or h.get("sym") or "").upper() for h in hits if h})
