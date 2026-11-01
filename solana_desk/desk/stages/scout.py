"""Scout — collect + enrich candidates every ~30s (multi-chain)."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from desk.chains import dexscreener_ids, resolve_chains
from desk.http_client import HttpClient
from desk.sources import dexscreener, gecko

log = logging.getLogger("desk.scout")
_GECKO_GAP_SEC = 2.2


def _parse_created(v: Any) -> datetime | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        ts = float(v)
        if ts > 1e12:
            ts /= 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        try:
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            return datetime.fromisoformat(s)
        except ValueError:
            try:
                ts = float(s)
                if ts > 1e12:
                    ts /= 1000.0
                return datetime.fromtimestamp(ts, tz=timezone.utc)
            except ValueError:
                return None
    return None


def age_hours(candidate: dict[str, Any], now: datetime | None = None) -> float | None:
    now = now or datetime.now(timezone.utc)
    created = _parse_created(candidate.get("created_at"))
    if created is None:
        return None
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return max(0.0, (now - created).total_seconds() / 3600.0)


def _key(row: dict[str, Any]) -> str:
    chain = str(row.get("chain") or "unknown")
    mint = str(row.get("mint") or "")
    return f"{chain}:{mint}".lower()


def run_scout(http: HttpClient, cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Return enriched candidates deduped by chain:mint, age ≤ max hours when known."""
    max_age = float(cfg.get("max_pair_age_hours", 72))
    pages = int(cfg.get("gecko_new_pool_pages", 1 if len(resolve_chains(cfg)) > 1 else 2))
    # multi-chain: default 1 new-pool page to respect rate limits
    if cfg.get("gecko_new_pool_pages") is not None:
        pages = int(cfg["gecko_new_pool_pages"])
    chains = resolve_chains(cfg)
    allowed_dex = dexscreener_ids(chains)
    # also accept raw chain ids
    for c in chains:
        allowed_dex.add(str(c["id"]).lower())
        if c["id"] == "ethereum":
            allowed_dex.add("ethereum")
            allowed_dex.add("eth")

    merged: dict[str, dict[str, Any]] = {}

    def _add(rows: list[dict[str, Any]]) -> None:
        for r in rows:
            mint = r.get("mint")
            if not mint:
                continue
            k = _key(r)
            prev = merged.get(k)
            if prev is None:
                merged[k] = dict(r)
            else:
                for key, v in r.items():
                    if v is None:
                        continue
                    if key in ("liquidity_usd", "volume_1h_usd"):
                        try:
                            if float(v or 0) >= float(prev.get(key) or 0):
                                prev[key] = v
                        except (TypeError, ValueError):
                            prev[key] = v
                    elif prev.get(key) is None:
                        prev[key] = v

    for chain in chains:
        network = str(chain.get("gecko_network") or chain["id"])
        cid = str(chain["id"])
        try:
            _add(gecko.fetch_new_pools(http, network, cid, pages=pages))
        except Exception as exc:  # noqa: BLE001
            log.warning("new_pools %s failed: %s", network, exc)
        time.sleep(_GECKO_GAP_SEC)

        for dex in chain.get("gecko_dexes") or []:
            try:
                _add(gecko.fetch_dex_pools(http, network, cid, dex, page=1))
            except Exception as exc:  # noqa: BLE001
                log.warning("dex pools %s/%s failed: %s", network, dex, exc)
            time.sleep(_GECKO_GAP_SEC)

    try:
        _add(dexscreener.fetch_token_profiles(http, allowed_dex))
    except Exception as exc:  # noqa: BLE001
        log.warning("profiles failed: %s", exc)

    now = datetime.now(timezone.utc)
    out: list[dict[str, Any]] = []
    for _k, row in merged.items():
        chain = str(row.get("chain") or "")
        mint = str(row.get("mint"))
        enriched = None
        try:
            enriched = dexscreener.enrich_token(
                http,
                mint,
                preferred_chain=chain or None,
                allowed_chains=allowed_dex,
            )
            if enriched:
                for key, v in enriched.items():
                    if v is not None:
                        row[key] = v
        except Exception as exc:  # noqa: BLE001
            log.warning("enrich %s failed: %s", mint, exc)

        # attach matching chain pack id if dex returned ethereum etc.
        if not row.get("chain") and enriched and enriched.get("chain"):
            row["chain"] = enriched["chain"]

        ah = age_hours(row, now)
        row["age_hours"] = ah
        if ah is not None and ah > max_age:
            continue
        out.append(row)
    return out
