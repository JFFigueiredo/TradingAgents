"""GeckoTerminal public API — new pools and DEX pool lists (multi-network)."""
from __future__ import annotations

import logging
import time
from typing import Any

from desk.http_client import HttpClient

log = logging.getLogger("desk.gecko")
BASE = "https://api.geckoterminal.com/api/v2"


def _pool_row(item: dict[str, Any], network: str, chain_id: str) -> dict[str, Any] | None:
    try:
        attrs = item.get("attributes") or {}
        rel = item.get("relationships") or {}
        base = ((rel.get("base_token") or {}).get("data") or {})
        quote = ((rel.get("quote_token") or {}).get("data") or {})
        dex = ((rel.get("dex") or {}).get("data") or {})
        base_id = str(base.get("id") or "")
        # id format often "<network>_<mint>"
        mint = base_id.split("_", 1)[-1] if "_" in base_id else base_id
        if not mint:
            return None
        return {
            "source": "geckoterminal",
            "chain": chain_id,
            "gecko_network": network,
            "pool_id": item.get("id"),
            "pair_address": attrs.get("address"),
            "mint": mint,
            "name": attrs.get("name"),
            "dex_id": str(dex.get("id") or "").lower(),
            "created_at": attrs.get("pool_created_at") or attrs.get("created_at"),
            "price_usd": _f(attrs.get("base_token_price_usd")),
            "volume_1h_usd": _f((attrs.get("volume_usd") or {}).get("h1")),
            "volume_6h_usd": _f((attrs.get("volume_usd") or {}).get("h6")),
            "volume_5m_usd": _f((attrs.get("volume_usd") or {}).get("m5")),
            "liquidity_usd": _f(attrs.get("reserve_in_usd")),
            "quote_mint": (str(quote.get("id") or "").split("_", 1)[-1] if quote.get("id") else None),
        }
    except Exception as exc:  # noqa: BLE001
        log.debug("skip pool parse: %s", exc)
        return None


def _f(v: Any) -> float | None:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def fetch_new_pools(
    http: HttpClient,
    network: str,
    chain_id: str,
    pages: int = 2,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for page in range(1, pages + 1):
        if page > 1:
            time.sleep(2.2)
        data = http.get_json(
            f"{BASE}/networks/{network}/new_pools",
            params={"page": page},
            source=f"gecko.new_pools.{network}",
        )
        if not data or not isinstance(data, dict):
            continue
        for item in data.get("data") or []:
            row = _pool_row(item, network, chain_id)
            if row:
                out.append(row)
    return out


def fetch_dex_pools(
    http: HttpClient,
    network: str,
    chain_id: str,
    dex: str,
    page: int = 1,
) -> list[dict[str, Any]]:
    data = http.get_json(
        f"{BASE}/networks/{network}/dexes/{dex}/pools",
        params={"page": page},
        source=f"gecko.dex.{network}.{dex}",
    )
    if not data or not isinstance(data, dict):
        return []
    out: list[dict[str, Any]] = []
    for item in data.get("data") or []:
        row = _pool_row(item, network, chain_id)
        if row:
            row["dex_id"] = row.get("dex_id") or dex.lower()
            out.append(row)
    return out
