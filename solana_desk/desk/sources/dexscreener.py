"""DexScreener public API — profiles + live pair data (multi-chain)."""
from __future__ import annotations

import logging
from typing import Any

from desk.http_client import HttpClient

log = logging.getLogger("desk.dex")
BASE = "https://api.dexscreener.com"


def _f(v: Any) -> float | None:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def fetch_token_profiles(http: HttpClient, allowed_chains: set[str]) -> list[dict[str, Any]]:
    data = http.get_json(f"{BASE}/token-profiles/latest/v1", source="dex.profiles")
    if not data or not isinstance(data, list):
        return []
    allowed = {c.lower() for c in allowed_chains}
    out: list[dict[str, Any]] = []
    for item in data:
        chain = str(item.get("chainId") or "").lower()
        if chain not in allowed:
            continue
        mint = item.get("tokenAddress")
        if not mint:
            continue
        out.append(
            {
                "source": "dexscreener_profile",
                "chain": chain if chain != "ethereum" else "ethereum",
                "mint": mint,
                "pair_address": None,
                "dex_id": None,
                "created_at": None,
                "name": item.get("description") or mint[:8],
            }
        )
    return out


def enrich_token(
    http: HttpClient,
    mint: str,
    *,
    preferred_chain: str | None = None,
    allowed_chains: set[str] | None = None,
) -> dict[str, Any] | None:
    data = None
    if preferred_chain:
        dex_chain = preferred_chain
        if preferred_chain == "ethereum":
            dex_chain = "ethereum"
        data = http.get_json(f"{BASE}/tokens/v1/{dex_chain}/{mint}", source="dex.token")
    if not data:
        data = http.get_json(f"{BASE}/latest/dex/tokens/{mint}", source="dex.token.legacy")
    pairs = _extract_pairs(data)
    if not pairs:
        return None
    allowed = {c.lower() for c in (allowed_chains or set())}
    if preferred_chain:
        preferred = preferred_chain.lower()
        # map internal id ethereum
        pref_set = {preferred}
        if preferred == "ethereum":
            pref_set.add("eth")
        filtered = [p for p in pairs if str(p.get("chainId") or "").lower() in pref_set or str(p.get("chainId") or "").lower() == preferred]
        if filtered:
            pairs = filtered
    elif allowed:
        filtered = [p for p in pairs if str(p.get("chainId") or "").lower() in allowed]
        if filtered:
            pairs = filtered
    pairs.sort(
        key=lambda p: _f(p.get("liquidity", {}).get("usd") if isinstance(p.get("liquidity"), dict) else p.get("liquidity"))
        or 0.0,
        reverse=True,
    )
    p = pairs[0]
    return pair_to_candidate(p, mint)


def enrich_pair(http: HttpClient, chain: str, pair_address: str) -> dict[str, Any] | None:
    data = http.get_json(f"{BASE}/latest/dex/pairs/{chain}/{pair_address}", source="dex.pair")
    if not data or not isinstance(data, dict):
        return None
    pair = data.get("pair") or (data.get("pairs") or [None])[0]
    if not pair:
        return None
    mint = ((pair.get("baseToken") or {}).get("address")) or ""
    return pair_to_candidate(pair, mint)


def _extract_pairs(data: Any) -> list[dict[str, Any]]:
    if data is None:
        return []
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        if isinstance(data.get("pairs"), list):
            return data["pairs"]
        if "pairAddress" in data or "baseToken" in data:
            return [data]
    return []


def pair_to_candidate(p: dict[str, Any], mint: str) -> dict[str, Any]:
    liq = p.get("liquidity")
    liq_usd = _f(liq.get("usd") if isinstance(liq, dict) else liq)
    vol = p.get("volume") or {}
    txns = p.get("txns") or {}
    h1 = txns.get("h1") or {}
    buys = int(h1.get("buys") or 0)
    sells = int(h1.get("sells") or 0)
    pc = p.get("priceChange") or {}
    chain = str(p.get("chainId") or "").lower()
    if chain == "eth":
        chain = "ethereum"
    return {
        "source": "dexscreener",
        "chain": chain,
        "mint": mint or ((p.get("baseToken") or {}).get("address")),
        "pair_address": p.get("pairAddress"),
        "dex_id": str(p.get("dexId") or "").lower(),
        "name": ((p.get("baseToken") or {}).get("symbol")) or ((p.get("baseToken") or {}).get("name")),
        "created_at": p.get("pairCreatedAt"),
        "price_usd": _f(p.get("priceUsd")),
        "volume_5m_usd": _f(vol.get("m5")),
        "volume_1h_usd": _f(vol.get("h1")),
        "volume_6h_usd": _f(vol.get("h6")),
        "liquidity_usd": liq_usd,
        "buys_1h": buys,
        "sells_1h": sells,
        "price_change_1h_pct": _f(pc.get("h1")),
        "holder_count": None,
        "url": p.get("url"),
    }


def price_snapshot(
    http: HttpClient,
    mint: str,
    *,
    chain: str | None = None,
    allowed_chains: set[str] | None = None,
) -> dict[str, Any] | None:
    return enrich_token(http, mint, preferred_chain=chain, allowed_chains=allowed_chains)
