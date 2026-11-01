"""Public Solana RPC — mint + freeze authority from mint account."""
from __future__ import annotations

import base64
import logging
import struct
from typing import Any

from desk.http_client import HttpClient

log = logging.getLogger("desk.rpc")
RPC_URL = "https://api.mainnet-beta.solana.com"
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"


def get_mint_authorities(http: HttpClient, mint: str) -> dict[str, Any] | None:
    """
    Return {mint_authority, freeze_authority} where None means revoked.
    On any failure return None (caller fail-closes).
    """
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getAccountInfo",
        "params": [mint, {"encoding": "base64"}],
    }
    data = http.post_json(RPC_URL, payload, source="solana.rpc")
    if not data or not isinstance(data, dict):
        return None
    result = (data.get("result") or {}).get("value")
    if not result:
        log.warning("rpc mint account missing: %s", mint)
        return None
    owner = result.get("owner")
    raw_b64 = (result.get("data") or [None])[0]
    if not raw_b64:
        return None
    try:
        raw = base64.b64decode(raw_b64)
    except Exception as exc:  # noqa: BLE001
        log.warning("rpc decode fail %s: %s", mint, exc)
        return None

    # SPL Token Mint layout (82 bytes classic):
    # 0-4 mintAuthorityOption u32, 4-36 mintAuthority pubkey,
    # 36-44 supply u64, 44 decimals u8, 45 isInitialized u8,
    # 46-50 freezeAuthorityOption u32, 50-82 freezeAuthority
    if len(raw) < 82:
        log.warning("rpc mint account too short (%s): %s", len(raw), mint)
        return None

    try:
        mint_opt = struct.unpack_from("<I", raw, 0)[0]
        freeze_opt = struct.unpack_from("<I", raw, 46)[0]
        mint_auth = _pubkey_b58(raw[4:36]) if mint_opt == 1 else None
        freeze_auth = _pubkey_b58(raw[50:82]) if freeze_opt == 1 else None
        return {
            "mint_authority": mint_auth,
            "freeze_authority": freeze_auth,
            "owner_program": owner,
            "is_token_program": owner in (TOKEN_PROGRAM, TOKEN_2022),
        }
    except Exception as exc:  # noqa: BLE001
        log.warning("rpc parse fail %s: %s", mint, exc)
        return None


def _pubkey_b58(raw32: bytes) -> str:
    # local base58 to avoid extra dependency
    alphabet = b"123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    n = int.from_bytes(raw32, "big")
    enc = bytearray()
    while n > 0:
        n, r = divmod(n, 58)
        enc.append(alphabet[r])
    pad = 0
    for b in raw32:
        if b == 0:
            pad += 1
        else:
            break
    return (alphabet[:1] * pad + enc[::-1]).decode("ascii")
