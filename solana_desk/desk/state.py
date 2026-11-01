"""Atomic state persistence."""
from __future__ import annotations

import json
import os
import threading
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any

_write_lock = threading.Lock()


def default_state(starting_cash: float, config_id: str) -> dict[str, Any]:
    return {
        "version": 1,
        "config_id": config_id,
        "cash_usd": float(starting_cash),
        "starting_cash_usd": float(starting_cash),
        "positions": [],
        "closed_trades": [],
        "reject_memory": {},  # mint -> {kind, reason, until_ts or permanent}
        "creator_blacklist": [],
        "counters": {
            "pairs_scanned": 0,
            "evaluations": 0,
            "filter_killed": 0,
            "filter_passed": 0,
            "scored": 0,
            "score_under_threshold": 0,
            "traded": 0,
            "stop_overshoots": 0,
        },
        "reject_reasons": {},
        "last_loss_ts": None,
        "updated_at": None,
    }


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
    payload = json.dumps(data, indent=2, default=str)
    with _write_lock:
        with tmp.open("w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)


def load_state(path: Path, starting_cash: float, config_id: str) -> dict[str, Any]:
    if not path.is_file():
        return default_state(starting_cash, config_id)
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        # ensure required keys exist
        base = default_state(starting_cash, config_id)
        for k, v in base.items():
            if k not in data:
                data[k] = deepcopy(v)
        for k, v in base["counters"].items():
            data["counters"].setdefault(k, v)
        return data
    except (OSError, json.JSONDecodeError):
        return default_state(starting_cash, config_id)


def save_state(path: Path, state: dict[str, Any]) -> None:
    from desk.logging_util import utc_now_iso

    state["updated_at"] = utc_now_iso()
    atomic_write_json(path, state)


def bump(state: dict[str, Any], key: str, n: int = 1) -> None:
    state["counters"][key] = int(state["counters"].get(key, 0)) + n


def bump_reject(state: dict[str, Any], reason: str) -> None:
    rr = state.setdefault("reject_reasons", {})
    rr[reason] = int(rr.get(reason, 0)) + 1


def equity_usd(state: dict[str, Any]) -> float:
    eq = float(state["cash_usd"])
    for p in state.get("positions", []):
        eq += float(p.get("mark_value_usd", p.get("cost_usd", 0.0)))
    return eq
