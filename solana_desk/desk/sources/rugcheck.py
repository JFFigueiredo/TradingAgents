"""RugCheck public API — authorities, LP lock, holders, creator, risks."""
from __future__ import annotations

import logging
from typing import Any

from desk.http_client import HttpClient

log = logging.getLogger("desk.rugcheck")
BASE = "https://api.rugcheck.xyz/v1"


def fetch_report(http: HttpClient, mint: str) -> dict[str, Any] | None:
    data = http.get_json(f"{BASE}/tokens/{mint}/report", source="rugcheck")
    if not data or not isinstance(data, dict):
        return None
    return data


def parse_report(report: dict[str, Any], pair_address: str | None) -> dict[str, Any]:
    """Extract filter-relevant fields. Missing data → None (caller fail-closes)."""
    token_meta = report.get("token") or report.get("tokenMeta") or {}
    mint_auth = report.get("mintAuthority")
    if mint_auth is None:
        mint_auth = token_meta.get("mintAuthority")
    freeze_auth = report.get("freezeAuthority")
    if freeze_auth is None:
        freeze_auth = token_meta.get("freezeAuthority")

    # normalize empty string to None (revoked)
    def _norm_auth(v: Any) -> Any:
        if v is None:
            return None
        if isinstance(v, str) and v.strip() in ("", "null", "None"):
            return None
        return v

    mint_auth = _norm_auth(mint_auth)
    freeze_auth = _norm_auth(freeze_auth)

    markets = report.get("markets") or []
    lp_locked_pct: float | None = None
    matched_market = None
    pair_l = (pair_address or "").lower()
    for m in markets:
        addr = str(m.get("pubkey") or m.get("market") or m.get("address") or "").lower()
        if pair_l and addr and (pair_l == addr or pair_l in addr or addr in pair_l):
            matched_market = m
            break
    if matched_market is None and len(markets) == 1:
        matched_market = markets[0]
    if matched_market is not None:
        lp = matched_market.get("lp") or matched_market
        # try several field names used across RugCheck versions
        for key in ("lpLockedPct", "lp_locked_pct", "lockedPct", "percentLocked", "lpLocked"):
            if key in lp and lp[key] is not None:
                try:
                    val = float(lp[key])
                    # some APIs use 0-1 fraction
                    lp_locked_pct = val * 100.0 if val <= 1.0 else val
                    break
                except (TypeError, ValueError):
                    pass
        if lp_locked_pct is None:
            locked = lp.get("lpLocked") or lp.get("locked")
            total = lp.get("lpTotal") or lp.get("total")
            try:
                if locked is not None and total:
                    lp_locked_pct = float(locked) / float(total) * 100.0
            except (TypeError, ValueError, ZeroDivisionError):
                pass

    top10_pct = _top10_ex_pools(report)
    creator = (
        report.get("creator")
        or (report.get("topHolders") or [{}])[0].get("owner")
        or report.get("deployer")
    )
    risks = report.get("risks") or report.get("risk") or []
    risk_names = []
    rugged = False
    serious = False
    for r in risks if isinstance(risks, list) else []:
        if isinstance(r, str):
            name = r
            level = ""
        else:
            name = str(r.get("name") or r.get("level") or r.get("description") or "")
            level = str(r.get("level") or r.get("severity") or "").lower()
        risk_names.append(name)
        low = name.lower()
        if "rug" in low:
            rugged = True
        if level in ("danger", "critical", "high", "warn") or any(
            x in low for x in ("insider", "network", "honeypot", "freeze", "mint auth")
        ):
            if "insider" in low or "network" in low or level in ("danger", "critical"):
                serious = True

    holder_count = report.get("totalHolders") or report.get("holderCount")
    try:
        holder_count = int(holder_count) if holder_count is not None else None
    except (TypeError, ValueError):
        holder_count = None

    return {
        "mint_authority": mint_auth,
        "freeze_authority": freeze_auth,
        "lp_locked_pct": lp_locked_pct,
        "lp_market_matched": matched_market is not None,
        "top10_pct": top10_pct,
        "holder_count": holder_count,
        "creator": creator,
        "rugged": rugged,
        "serious_risk": serious,
        "risk_names": risk_names,
        "raw_markets": len(markets),
    }


# Known AMM / locker-ish account name fragments to exclude from top-10 concentration
_EXCLUDED_OWNER_HINTS = (
    "pool",
    "vault",
    "amm",
    "raydium",
    "pump",
    "lock",
    "locker",
    "authority",
    "pda",
)


def _top10_ex_pools(report: dict[str, Any]) -> float | None:
    holders = report.get("topHolders") or report.get("holders") or []
    if not holders:
        # some reports expose a precomputed pct
        for key in ("top10HolderPercent", "top10HoldersPercent", "top10Pct"):
            if report.get(key) is not None:
                try:
                    v = float(report[key])
                    return v * 100.0 if v <= 1.0 else v
                except (TypeError, ValueError):
                    pass
        return None

    scored: list[float] = []
    for h in holders:
        try:
            pct = h.get("pct")
            if pct is None:
                pct = h.get("percentage") or h.get("percent")
            if pct is None and h.get("uiAmount") is not None:
                continue
            owner = str(h.get("owner") or h.get("address") or h.get("pubkey") or "").lower()
            label = str(h.get("insider") or h.get("name") or h.get("tag") or "").lower()
            if any(x in owner for x in _EXCLUDED_OWNER_HINTS) or any(
                x in label for x in _EXCLUDED_OWNER_HINTS
            ):
                continue
            # exclude known pool flags
            if h.get("isPool") or h.get("pool") or h.get("liquidity"):
                continue
            pv = float(pct)
            if pv <= 1.0 and pv > 0:
                # ambiguous; RugCheck often uses 0-100 already when >1 holders sum ~100
                # Prefer treating values <=1 as fraction only when sum of top looks fractional
                pass
            scored.append(pv)
        except (TypeError, ValueError):
            continue
    if not scored:
        return None
    # If values look like fractions, scale
    if sum(scored[:10]) <= 1.5:
        scored = [s * 100.0 for s in scored]
    return float(sum(scored[:10]))
