"""Chain registry — Solana + EVM meme venues (paper data only)."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

# Canonical chain packs. Configs reference these by id, or embed overrides.
CHAINS: dict[str, dict[str, Any]] = {
    "solana": {
        "id": "solana",
        "gecko_network": "solana",
        "dexscreener": "solana",
        "safety": "solana_rugcheck",
        "gecko_dexes": ["pumpswap", "raydium", "raydium-clmm", "raydium-launchlab", "pump-fun"],
        "allowed_dex_families": ["pumpfun", "pumpswap", "raydium"],
        "reject_pre_graduation_pumpfun": True,
    },
    "base": {
        "id": "base",
        "gecko_network": "base",
        "dexscreener": "base",
        "safety": "goplus",
        "goplus_chain_id": "8453",
        "gecko_dexes": [
            "uniswap-v2-base",
            "uniswap-v3-base",
            "aerodrome-base",
            "baseswap",
            "sushiswap-v3-base",
        ],
        "allowed_dex_families": [
            "uniswap",
            "aerodrome",
            "baseswap",
            "sushiswap",
            "virtuals",
            "clanker",
        ],
    },
    "ethereum": {
        "id": "ethereum",
        "gecko_network": "eth",
        "dexscreener": "ethereum",
        "safety": "goplus",
        "goplus_chain_id": "1",
        "gecko_dexes": ["uniswap-v2", "uniswap-v3", "sushiswap"],
        "allowed_dex_families": ["uniswap", "sushiswap"],
    },
    "bsc": {
        "id": "bsc",
        "gecko_network": "bsc",
        "dexscreener": "bsc",
        "safety": "goplus",
        "goplus_chain_id": "56",
        "gecko_dexes": ["pancakeswap-v2", "pancakeswap-v3", "uniswap-v3-bsc"],
        "allowed_dex_families": ["pancakeswap", "uniswap", "biswap", "thena"],
    },
    "arbitrum": {
        "id": "arbitrum",
        "gecko_network": "arbitrum",
        "dexscreener": "arbitrum",
        "safety": "goplus",
        "goplus_chain_id": "42161",
        "gecko_dexes": ["uniswap-v3-arbitrum", "camelot", "sushiswap-arbitrum"],
        "allowed_dex_families": ["uniswap", "camelot", "sushiswap", "ramses"],
    },
    "polygon": {
        "id": "polygon",
        "gecko_network": "polygon_pos",
        "dexscreener": "polygon",
        "safety": "goplus",
        "goplus_chain_id": "137",
        "gecko_dexes": ["quickswap", "uniswap-v3-polygon", "sushiswap-polygon"],
        "allowed_dex_families": ["quickswap", "uniswap", "sushiswap"],
    },
    "avalanche": {
        "id": "avalanche",
        "gecko_network": "avax",
        "dexscreener": "avalanche",
        "safety": "goplus",
        "goplus_chain_id": "43114",
        "gecko_dexes": ["traderjoe", "pangolin", "uniswap-v3-avalanche"],
        "allowed_dex_families": ["traderjoe", "pangolin", "uniswap", "lfj"],
    },
    "optimism": {
        "id": "optimism",
        "gecko_network": "optimism",
        "dexscreener": "optimism",
        "safety": "goplus",
        "goplus_chain_id": "10",
        "gecko_dexes": ["uniswap-v3-optimism", "velodrome-optimism", "sushiswap-optimism"],
        "allowed_dex_families": ["uniswap", "velodrome", "sushiswap"],
    },
    "blast": {
        "id": "blast",
        "gecko_network": "blast",
        "dexscreener": "blast",
        "safety": "goplus",
        "goplus_chain_id": "81457",
        "gecko_dexes": ["uniswap-v2-blast", "uniswap-v3-blast", "thruster"],
        "allowed_dex_families": ["uniswap", "thruster", "bladeswap"],
    },
    "linea": {
        "id": "linea",
        "gecko_network": "linea",
        "dexscreener": "linea",
        "safety": "goplus",
        "goplus_chain_id": "59144",
        "gecko_dexes": ["uniswap-v3-linea", "syncswap-linea", "pancake-linea"],
        "allowed_dex_families": ["uniswap", "syncswap", "pancakeswap"],
    },
}


def list_chain_ids() -> list[str]:
    return sorted(CHAINS.keys())


def resolve_chains(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Build active chain list from config.
    Prefer cfg['chains'] (list of ids or dicts). Fallback: solana-only from legacy keys.
    """
    raw = cfg.get("chains")
    if not raw:
        pack = deepcopy(CHAINS["solana"])
        if cfg.get("gecko_dexes"):
            pack["gecko_dexes"] = list(cfg["gecko_dexes"])
        if cfg.get("allowed_dex_families"):
            pack["allowed_dex_families"] = list(cfg["allowed_dex_families"])
        if "reject_pre_graduation_pumpfun" in cfg:
            pack["reject_pre_graduation_pumpfun"] = bool(cfg["reject_pre_graduation_pumpfun"])
        return [pack]

    out: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, str):
            if item not in CHAINS:
                raise ValueError(f"unknown chain id: {item}")
            out.append(deepcopy(CHAINS[item]))
        elif isinstance(item, dict):
            base_id = item.get("id") or item.get("chain")
            if base_id and base_id in CHAINS:
                pack = deepcopy(CHAINS[base_id])
                pack.update({k: v for k, v in item.items() if v is not None})
            else:
                pack = dict(item)
                pack.setdefault("id", pack.get("gecko_network") or "unknown")
            out.append(pack)
        else:
            raise ValueError(f"bad chain entry: {item!r}")

    if len(out) == 1 and out[0].get("id") == "solana":
        if cfg.get("gecko_dexes"):
            out[0]["gecko_dexes"] = list(cfg["gecko_dexes"])
        if cfg.get("allowed_dex_families"):
            out[0]["allowed_dex_families"] = list(cfg["allowed_dex_families"])
        if "reject_pre_graduation_pumpfun" in cfg:
            out[0]["reject_pre_graduation_pumpfun"] = bool(cfg["reject_pre_graduation_pumpfun"])
    return out


def dexscreener_ids(chains: list[dict[str, Any]]) -> set[str]:
    return {str(c.get("dexscreener") or c["id"]).lower() for c in chains}


def chain_by_dexscreener(chains: list[dict[str, Any]], dex_chain: str) -> dict[str, Any] | None:
    d = dex_chain.lower()
    for c in chains:
        if str(c.get("dexscreener") or "").lower() == d or str(c.get("id") or "").lower() == d:
            return c
    if d in ("ethereum", "eth"):
        for c in chains:
            if c.get("id") == "ethereum":
                return c
    if d in ("polygon", "polygon_pos"):
        for c in chains:
            if c.get("id") == "polygon":
                return c
    if d in ("avalanche", "avax"):
        for c in chains:
            if c.get("id") == "avalanche":
                return c
    return None
