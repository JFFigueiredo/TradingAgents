"""Load control / variant JSON configs."""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONFIGS = ROOT / "configs"
DEFAULT_CONTROL = CONFIGS / "control.json"


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    cfg_path = Path(path) if path else DEFAULT_CONTROL
    if not cfg_path.is_file():
        # allow bare variant id: "control" or "aggressive_vol"
        name = cfg_path.name
        if name.endswith(".json"):
            stem = name
        else:
            stem = f"{name}.json"
        for alt in (
            CONFIGS / stem,
            CONFIGS / "variants" / stem,
            CONFIGS / "variants" / "auto" / stem,
        ):
            if alt.is_file():
                cfg_path = alt
                break
    with cfg_path.open(encoding="utf-8") as f:
        data = json.load(f)
    data["_config_path"] = str(cfg_path.resolve())
    return data


def merge_variant(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(base)
    for k, v in overrides.items():
        if k.startswith("_"):
            continue
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = {**out[k], **v}
        else:
            out[k] = v
    return out
