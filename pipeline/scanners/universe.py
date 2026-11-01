"""Build a daytrading-oriented universe (liquid, movable names — not just SP500)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd


@dataclass
class DaytradeUniverseFilters:
    """Liquidity-first screen across US listings (America market)."""

    min_avg_volume_30d: float = 1_000_000
    min_price: float = 5.0
    max_price: float = 250.0
    min_market_cap: float = 300e6  # $300M — avoid illiquid microcaps
    min_volatility_week: float = 1.5  # % weekly vol — prefer names that move
    page_size: int = 500
    max_pages: int = 4  # up to ~2000 names
    common_stock_only: bool = True


def _ticker_from_symbol(symbol: str) -> str:
    if ":" in symbol:
        return symbol.split(":", 1)[1].strip().upper()
    return symbol.strip().upper()


def fetch_daytrade_universe_df(
    filters: DaytradeUniverseFilters | None = None,
) -> pd.DataFrame:
    """Pull a large liquid US common-stock list via tvscreener (paged)."""
    from tvscreener import StockField, StockScreener, SymbolType

    filters = filters or DaytradeUniverseFilters()
    frames: list[pd.DataFrame] = []

    for page in range(filters.max_pages):
        start = page * filters.page_size
        end = start + filters.page_size
        ss = StockScreener()
        if filters.common_stock_only:
            ss.set_symbol_types(SymbolType.COMMON_STOCK)
        ss.set_range(start, end)
        ss.where(StockField.AVERAGE_VOLUME_30_DAY > filters.min_avg_volume_30d)
        ss.where(StockField.PRICE.between(filters.min_price, filters.max_price))
        ss.where(StockField.MARKET_CAPITALIZATION > filters.min_market_cap)
        if filters.min_volatility_week and filters.min_volatility_week > 0:
            ss.where(StockField.VOLATILITY_WEEK > filters.min_volatility_week)
        ss.sort_by(StockField.AVERAGE_VOLUME_30_DAY, ascending=False)
        ss.select(
            StockField.NAME,
            StockField.PRICE,
            StockField.VOLUME,
            StockField.AVERAGE_VOLUME_30_DAY,
            StockField.RELATIVE_VOLUME,
            StockField.MARKET_CAPITALIZATION,
            StockField.VOLATILITY_WEEK,
        )
        df = ss.get()
        if df is None or df.empty:
            break
        frames.append(df)
        if len(df) < filters.page_size:
            break

    if not frames:
        return pd.DataFrame(
            columns=[
                "ticker",
                "tv_symbol",
                "price",
                "volume",
                "avg_volume_30d",
                "rel_volume",
                "market_cap",
                "volatility_week",
            ]
        )

    raw = pd.concat(frames, ignore_index=True)
    out = pd.DataFrame(
        {
            "ticker": raw["Symbol"].map(_ticker_from_symbol),
            "tv_symbol": raw["Symbol"],
            "price": raw["Price"],
            "volume": raw.get("Volume"),
            "avg_volume_30d": raw["Average Volume (30 day)"],
            "rel_volume": raw.get("Relative Volume"),
            "market_cap": raw["Market Capitalization"],
            "volatility_week": raw.get("Volatility Week"),
        }
    )
    return out.drop_duplicates(subset=["ticker"]).reset_index(drop=True)


def fetch_daytrade_tickers(filters: DaytradeUniverseFilters | None = None) -> list[str]:
    df = fetch_daytrade_universe_df(filters)
    return df["ticker"].tolist()


def save_universe(
    tickers: list[str],
    path: Path,
    *,
    header_comment: str = "",
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    if header_comment:
        for line in header_comment.strip().splitlines():
            lines.append(f"# {line}" if not line.startswith("#") else line)
    lines.extend(tickers)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def load_universe_file(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [
        ln.strip().upper()
        for ln in path.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]


def default_universe_path() -> Path:
    return Path(__file__).resolve().parents[1] / "out" / "universe_daytrade.txt"


def ensure_daytrade_universe(
    path: Path | None = None,
    filters: DaytradeUniverseFilters | None = None,
    *,
    refresh: bool = False,
) -> list[str]:
    """Load cached universe or build a new one."""
    path = path or default_universe_path()
    if path.exists() and not refresh:
        tickers = load_universe_file(path)
        if tickers:
            return tickers
    filters = filters or DaytradeUniverseFilters()
    df = fetch_daytrade_universe_df(filters)
    tickers = df["ticker"].tolist()
    meta = (
        f"daytrade universe | n={len(tickers)} | "
        f"avgVol30>{filters.min_avg_volume_30d:.0f} | "
        f"price {filters.min_price}-{filters.max_price} | "
        f"mcap>{filters.min_market_cap:.0f} | "
        f"volWeek>{filters.min_volatility_week}"
    )
    save_universe(tickers, path, header_comment=meta)
    # Also save a CSV sidecar for inspection
    csv_path = path.with_suffix(".csv")
    df.to_csv(csv_path, index=False)
    return tickers
