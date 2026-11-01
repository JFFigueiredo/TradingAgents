"""Human desk.log + structured events.jsonl."""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_lock = threading.Lock()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class DeskLogger:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = data_dir / "desk.log"
        self.events_path = data_dir / "events.jsonl"

        self._logger = logging.getLogger("desk")
        self._logger.setLevel(logging.INFO)
        self._logger.handlers.clear()
        self._logger.propagate = False

        fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        fh = logging.FileHandler(self.log_path, encoding="utf-8")
        fh.setFormatter(fmt)
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        self._logger.addHandler(fh)
        self._logger.addHandler(sh)

    def info(self, msg: str, *args: Any, **fields: Any) -> None:
        text = msg % args if args else msg
        self._logger.info(text)
        self.event("info", text, **fields)

    def warning(self, msg: str, *args: Any, **fields: Any) -> None:
        text = msg % args if args else msg
        self._logger.warning(text)
        self.event("warning", text, **fields)

    def error(self, msg: str, *args: Any, **fields: Any) -> None:
        text = msg % args if args else msg
        self._logger.error(text)
        self.event("error", text, **fields)

    def event(self, kind: str, message: str, **fields: Any) -> None:
        row = {"ts": utc_now_iso(), "kind": kind, "message": message, **fields}
        line = json.dumps(row, default=str) + "\n"
        with _lock:
            with self.events_path.open("a", encoding="utf-8") as f:
                f.write(line)
