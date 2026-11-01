"""GoPlus free token-security API for EVM chains (no API key)."""
from __future__ import annotations

import logging
from typing import Any

from desk.http_client import HttpClient

log = logging.getLogger("desk.goplus")
BASE = "https://api.gopluslabs.io/api/v1/token_security"


def fetch_security(http: HttpClient, chain_id: str, address: str) -> dict[str, Any] | None:
    data = http.get_json(
        f"{BASE}/{chain_id}",
        params={"contract_addresses": address.lower()},
        source="goplus",
    )
    if not data or not isinstance(data, dict):
        return None
    if str(data.get("code")) not in ("1", "1.0") and data.get("code") != 1:
        log.warning("goplus code=%s msg=%s", data.get("code"), data.get("message"))
        # still try to parse result if present
    result = data.get("result") or {}
    if not isinstance(result, dict):
        return None
    # result keyed by lowercase address
    row = result.get(address.lower()) or result.get(address) 
    if not row and len(result) == 1:
        row = next(iter(result.values()))
    if not isinstance(row, dict):
        return None
    return parse_security(row)


def _flag_true(v: Any) -> bool:
    return str(v).lower() in ("1", "true", "yes")


def _f(v: Any) -> float | None:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_security(row: dict[str, Any]) -> dict[str, Any]:
    # LP locked % from lp_holders
    lp_locked_pct: float | None = None
    locked_sum = 0.0
    total_sum = 0.0
    for h in row.get("lp_holders") or []:
        try:
            bal = float(h.get("percent") or 0)
            # percent often 0-1 fraction
            pct = bal * 100.0 if bal <= 1.0 else bal
            total_sum += pct
            if _flag_true(h.get("is_locked")) or _flag_true(h.get("is_contract")):
                # locked OR burned-to-dead contract often marks locked LP
                locked_sum += pct
            tag = str(h.get("tag") or "").lower()
            addr = str(h.get("address") or "").lower()
            if "dead" in tag or addr.startswith("0x000000000000000000000000000000000000dead"):
                locked_sum += pct
        except (TypeError, ValueError):
            continue
    if total_sum > 0:
        lp_locked_pct = min(100.0, locked_sum)

    # top10 holders excluding LP/DEX hints
    top10 = 0.0
    counted = 0
    for h in row.get("holders") or []:
        try:
            tag = str(h.get("tag") or "").lower()
            if any(x in tag for x in ("lp", "pool", "uniswap", "pancake", "aerodrome", "locker")):
                continue
            if _flag_true(h.get("is_locked")) and "lock" in tag:
                continue
            pct = float(h.get("percent") or 0)
            if pct <= 1.0:
                pct *= 100.0
            top10 += pct
            counted += 1
            if counted >= 10:
                break
        except (TypeError, ValueError):
            continue
    top10_pct = top10 if counted else None

    holder_count = None
    try:
        if row.get("holder_count") is not None:
            holder_count = int(float(row["holder_count"]))
    except (TypeError, ValueError):
        holder_count = None

    buy_tax = _f(row.get("buy_tax"))
    sell_tax = _f(row.get("sell_tax"))
    # taxes sometimes 0-1
    if buy_tax is not None and buy_tax <= 1.0:
        buy_tax *= 100.0
    if sell_tax is not None and sell_tax <= 1.0:
        sell_tax *= 100.0

    serious = False
    for key in (
        "is_honeypot",
        "is_blacklisted",
        "can_take_back_ownership",
        "owner_change_balance",
        "hidden_owner",
        "selfdestruct",
        "trading_cooldown",
        "transfer_pausable",
        "is_airdrop_scam",
    ):
        if _flag_true(row.get(key)):
            serious = True

    mintable = _flag_true(row.get("is_mintable"))
    # creator / owner
    creator = row.get("creator_address") or row.get("owner_address")

    return {
        "mint_authority": "active" if mintable else None,  # None = revoked/ok
        "freeze_authority": "active" if _flag_true(row.get("transfer_pausable")) else None,
        "lp_locked_pct": lp_locked_pct,
        "lp_market_matched": lp_locked_pct is not None or bool(row.get("lp_holders")),
        "top10_pct": top10_pct,
        "holder_count": holder_count,
        "creator": creator,
        "rugged": _flag_true(row.get("is_airdrop_scam")) or _flag_true(row.get("is_honeypot")),
        "serious_risk": serious,
        "buy_tax": buy_tax,
        "sell_tax": sell_tax,
        "is_open_source": _flag_true(row.get("is_open_source")),
        "risk_names": [k for k in (
            "honeypot", "blacklist", "mintable", "hidden_owner", "pausable"
        ) if (
            (k == "honeypot" and _flag_true(row.get("is_honeypot")))
            or (k == "blacklist" and _flag_true(row.get("is_blacklisted")))
            or (k == "mintable" and mintable)
            or (k == "hidden_owner" and _flag_true(row.get("hidden_owner")))
            or (k == "pausable" and _flag_true(row.get("transfer_pausable")))
        )],
    }
