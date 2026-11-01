"""Project B: valuation + volume screen via tvscreener (TradingView fields)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd


@dataclass
class TvFilters:
    index: str = "SP500"  # SP500 | NASDAQ100 | NONE
    min_rel_volume: float = 1.5
    min_market_cap: float = 2e9
    min_volume: float = 500_000
    pe_min: float | None = 1.0
    pe_max: float | None = 80.0
    max_rows: int = 500


def _index_symbol(name: str):
    from tvscreener import IndexSymbol

    key = name.strip().upper().replace("&", "").replace(" ", "")
    mapping = {
        "SP500": IndexSymbol.SP500,
        "SPX": IndexSymbol.SP500,
        "NASDAQ100": IndexSymbol.NASDAQ_100,
        "NASDAQ_100": IndexSymbol.NASDAQ_100,
        "NDX": IndexSymbol.NASDAQ_100,
        "DJ30": IndexSymbol.DOW_JONES,
        "NONE": None,
        "": None,
    }
    if key not in mapping:
        raise ValueError(f"Unknown index {name!r}. Use SP500, NASDAQ100, or NONE.")
    return mapping[key]


def _ticker_from_symbol(symbol: str) -> str:
    # TradingView style: "NASDAQ:NVDA" → "NVDA"
    if ":" in symbol:
        return symbol.split(":", 1)[1].strip()
    return symbol.strip()


def screen_valuation_volume(filters: TvFilters | None = None) -> pd.DataFrame:
    """Return rows that pass valuation/volume heuristics."""
    from tvscreener import StockField, StockScreener

    filters = filters or TvFilters()
    ss = StockScreener()
    idx = _index_symbol(filters.index)
    if idx is not None:
        ss.set_index(idx)
    ss.set_range(0, max(1, filters.max_rows))

    ss.where(StockField.RELATIVE_VOLUME >= filters.min_rel_volume)
    ss.where(StockField.MARKET_CAPITALIZATION > filters.min_market_cap)
    ss.where(StockField.VOLUME >= filters.min_volume)
    if filters.pe_min is not None:
        ss.where(StockField.PE_RATIO_TTM >= filters.pe_min)
    if filters.pe_max is not None:
        ss.where(StockField.PE_RATIO_TTM <= filters.pe_max)

    ss.sort_by(StockField.RELATIVE_VOLUME, ascending=False)
    ss.select(
        StockField.NAME,
        StockField.PRICE,
        StockField.VOLUME,
        StockField.RELATIVE_VOLUME,
        StockField.PE_RATIO_TTM,
        StockField.MARKET_CAPITALIZATION,
        StockField.PRICE_TO_BOOK_FY,
    )
    df = ss.get()
    if df is None or df.empty:
        return pd.DataFrame(
            columns=[
                "ticker",
                "tv_symbol",
                "price",
                "volume",
                "rel_volume",
                "pe_ttm",
                "market_cap",
                "pb",
                "source",
            ]
        )

    out = pd.DataFrame(
        {
            "ticker": df["Symbol"].map(_ticker_from_symbol),
            "tv_symbol": df["Symbol"],
            "price": df["Price"],
            "volume": df["Volume"],
            "rel_volume": df["Relative Volume"],
            "pe_ttm": df["Price to Earnings Ratio (TTM)"],
            "market_cap": df["Market Capitalization"],
            "pb": df.get("Price to Book (FY)"),
            "source": "tvscreener",
        }
    )
    return out.drop_duplicates(subset=["ticker"]).reset_index(drop=True)


def list_index_tickers(index: str = "SP500", max_rows: int = 500) -> list[str]:
    """Universe helper: all symbols in an index (no valuation/volume filter)."""
    from tvscreener import StockField, StockScreener

    ss = StockScreener()
    idx = _index_symbol(index)
    if idx is not None:
        ss.set_index(idx)
    ss.set_range(0, max_rows)
    ss.select(StockField.NAME, StockField.PRICE)
    df = ss.get()
    if df is None or df.empty:
        return []
    return sorted({_ticker_from_symbol(s) for s in df["Symbol"].tolist()})


def screen_to_records(filters: TvFilters | None = None) -> list[dict[str, Any]]:
    return screen_valuation_volume(filters).to_dict(orient="records")
