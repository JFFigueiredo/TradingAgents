"""Short-horizon daytrade triggers using only the last ~week of bars.

Fetches fresh yfinance intraday data per ticker (no 1y history).
Triggers are intentionally simple and fast for rotation over large universes.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any

import pandas as pd


@dataclass
class DaytradeConfig:
    # yfinance: 5m/15m available for recent days; 5m max ~60d, we only keep 7d.
    period: str = "7d"
    interval: str = "15m"  # 5m | 15m | 30m | 60m
    max_workers: int = 12
    # Relative volume: last N bars vs prior lookback mean
    rvol_bars: int = 3
    rvol_lookback: int = 20
    min_rvol: float = 1.8
    # Break of prior session's high/low (approx via last ~1 trading day of bars)
    breakout_lookback_bars: int = 26  # ~1 day of 15m bars in regular hours
    min_range_expansion: float = 1.5  # today's bar range vs median
    min_gap_pct: float = 1.0  # % gap vs prior close (tighter after weak lone-gap outcomes)
    # If True, gap_up only counts when the day is still holding above prior close
    gap_must_hold: bool = True


def _flatten_yf(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        df.columns = [c[0] for c in df.columns]
    need = ["Open", "High", "Low", "Close", "Volume"]
    if any(c not in df.columns for c in need):
        return pd.DataFrame()
    out = df[need].dropna()
    if out.index.has_duplicates:
        out = out.loc[~out.index.duplicated()]
    return out.sort_index()


def _fetch(ticker: str, cfg: DaytradeConfig) -> pd.DataFrame:
    import yfinance as yf

    raw = yf.download(
        ticker,
        period=cfg.period,
        interval=cfg.interval,
        auto_adjust=True,
        progress=False,
        threads=False,
    )
    return _flatten_yf(raw)


def _session_slices(df: pd.DataFrame) -> list[pd.DataFrame]:
    """Split by calendar day (UTC/exchange-local as provided by Yahoo index)."""
    if df.empty:
        return []
    days = df.groupby(df.index.date)
    return [g for _, g in days if len(g) >= 3]


def _scan_one(ticker: str, cfg: DaytradeConfig) -> list[dict[str, Any]]:
    df = _fetch(ticker, cfg)
    if len(df) < max(cfg.rvol_lookback + cfg.rvol_bars, 30):
        return []

    hits: list[dict[str, Any]] = []
    close = df["Close"].astype(float)
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    vol = df["Volume"].astype(float)
    last = df.iloc[-1]
    last_ts = df.index[-1]

    # --- Relative volume on the most recent bars ---
    recent_vol = float(vol.iloc[-cfg.rvol_bars :].mean())
    base_vol = float(vol.iloc[-(cfg.rvol_lookback + cfg.rvol_bars) : -cfg.rvol_bars].mean())
    rvol = (recent_vol / base_vol) if base_vol > 0 else 0.0
    if rvol >= cfg.min_rvol:
        hits.append(
            {
                "ticker": ticker,
                "pattern": "rvol_spike",
                "rvol": round(rvol, 2),
                "price": float(last["Close"]),
                "asof": last_ts.isoformat(),
                "source": "daytrade",
            }
        )

    # --- Break of prior-day high / low ---
    sessions = _session_slices(df)
    if len(sessions) >= 2:
        prev, cur = sessions[-2], sessions[-1]
        prev_high = float(prev["High"].max())
        prev_low = float(prev["Low"].min())
        cur_close = float(cur["Close"].iloc[-1])
        cur_high = float(cur["High"].max())
        cur_low = float(cur["Low"].min())
        if cur_high > prev_high and cur_close >= prev_high:
            hits.append(
                {
                    "ticker": ticker,
                    "pattern": "break_prev_day_high",
                    "level": prev_high,
                    "price": cur_close,
                    "asof": last_ts.isoformat(),
                    "source": "daytrade",
                }
            )
        if cur_low < prev_low and cur_close <= prev_low:
            hits.append(
                {
                    "ticker": ticker,
                    "pattern": "break_prev_day_low",
                    "level": prev_low,
                    "price": cur_close,
                    "asof": last_ts.isoformat(),
                    "source": "daytrade",
                }
            )

        # Gap vs prior close
        prev_close = float(prev["Close"].iloc[-1])
        day_open = float(cur["Open"].iloc[0])
        if prev_close > 0:
            gap_pct = (day_open - prev_close) / prev_close * 100.0
            if abs(gap_pct) >= cfg.min_gap_pct:
                is_up = gap_pct > 0
                # Fade filter: don't flag gap_up if price already gave the gap back
                if is_up and cfg.gap_must_hold and cur_close < prev_close:
                    pass
                else:
                    hits.append(
                        {
                            "ticker": ticker,
                            "pattern": "gap_up" if is_up else "gap_down",
                            "gap_pct": round(gap_pct, 2),
                            "price": cur_close,
                            "asof": last_ts.isoformat(),
                            "source": "daytrade",
                        }
                    )

    # --- Range expansion on last bar ---
    bar_range = (high - low).astype(float)
    med = float(bar_range.iloc[-21:-1].median()) if len(bar_range) > 21 else float(bar_range.iloc[:-1].median())
    last_range = float(bar_range.iloc[-1])
    if med > 0 and last_range >= cfg.min_range_expansion * med and rvol >= 1.2:
        hits.append(
            {
                "ticker": ticker,
                "pattern": "range_expansion",
                "expansion": round(last_range / med, 2),
                "price": float(last["Close"]),
                "asof": last_ts.isoformat(),
                "source": "daytrade",
            }
        )

    # Deduplicate by pattern name
    seen = set()
    uniq = []
    for h in hits:
        key = h["pattern"]
        if key in seen:
            continue
        seen.add(key)
        uniq.append(h)
    return uniq


def scan_daytrade(
    tickers: list[str],
    cfg: DaytradeConfig | None = None,
) -> list[dict[str, Any]]:
    """Fetch latest ≤7d bars for each ticker and return short-horizon hits."""
    cfg = cfg or DaytradeConfig()
    unique = sorted({t.strip().upper() for t in tickers if t and t.strip()})
    hits: list[dict[str, Any]] = []
    if not unique:
        return hits

    workers = max(1, min(cfg.max_workers, len(unique)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(_scan_one, t, cfg): t for t in unique}
        for fut in as_completed(futs):
            try:
                hits.extend(fut.result())
            except Exception:
                continue
    return hits


def daytrade_tickers(hits: list[dict[str, Any]]) -> list[str]:
    return sorted({str(h.get("ticker") or "").upper() for h in hits if h.get("ticker")})
