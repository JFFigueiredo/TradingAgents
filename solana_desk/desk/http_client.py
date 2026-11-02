"""HTTP helpers with timeout, retries, and 429 backoff. Never raises into the desk loop."""
from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import random
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlencode, urlparse

import requests

log = logging.getLogger("desk.http")

# Shared by every desk process on the host so parallel variants don't multiply API load.
CACHE_DIR = Path(__file__).resolve().parents[1] / "data" / "_http_cache"

# Longest-prefix match on `source`; dex.* is used for repricing so it stays under reprice_interval_sec.
CACHE_TTL_SEC: dict[str, float] = {
    "gecko": 180.0,
    "dex.profiles": 60.0,
    "dex.token": 4.0,
    "dex.pair": 4.0,
    "dex.batch": 4.0,
    "dex.scan": 30.0,
    "rugcheck": 300.0,
    "goplus": 300.0,
}

# Minimum spacing between requests to a host, across all desk processes. Paced hosts never
# queue or retry: a busy slot, a 429, or an active cooldown returns None (callers fall back to stale cache).
HOST_MIN_GAP_SEC: dict[str, float] = {
    "api.geckoterminal.com": 2.5,
}
HOST_COOLDOWN_SEC = 30.0
_SLOT_WAIT_SEC = 5.0

# Slow-moving lists (pools, safety reports) may be served stale when the live call fails.
# Prices (dex.token / dex.pair) never are: stops must not act on old marks.
STALE_MIN_TTL_SEC = 60.0
STALE_MAX_SEC = 900.0

_LOCK_WAIT_SEC = 60.0


def _ttl_for(source: str) -> float:
    best, ttl = -1, 0.0
    for prefix, t in CACHE_TTL_SEC.items():
        if source.startswith(prefix) and len(prefix) > best:
            best, ttl = len(prefix), t
    return ttl


@contextmanager
def _file_lock(path: Path, wait_sec: float) -> Iterator[bool]:
    """Exclusive flock; yields False if it could not be taken within wait_sec."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+") as fh:
        deadline = time.time() + wait_sec
        got = False
        while True:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                got = True
                break
            except BlockingIOError:
                if time.time() >= deadline:
                    break
                time.sleep(0.1)
        try:
            yield got
        finally:
            if got:
                fcntl.flock(fh, fcntl.LOCK_UN)


def _read_fresh(path: Path, ttl: float) -> tuple[bool, Any]:
    try:
        if time.time() - path.stat().st_mtime > ttl:
            return False, None
        return True, json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False, None


def _write_atomic(path: Path, data: Any) -> None:
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, path)
    except (OSError, TypeError, ValueError):
        tmp.unlink(missing_ok=True)


def _prune_cache(max_age_sec: float = 3600.0) -> None:
    cutoff = time.time() - max_age_sec
    try:
        for p in CACHE_DIR.iterdir():
            if p.name.startswith("pace_"):
                continue
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink()
            except OSError:
                pass
    except OSError:
        pass


def _read_float(path: Path) -> float:
    try:
        return float(path.read_text())
    except (OSError, ValueError):
        return 0.0


def _is_paced(url: str) -> bool:
    return (urlparse(url).hostname or "") in HOST_MIN_GAP_SEC


def _take_host_slot(url: str) -> bool:
    """True when this process may call the host now; False means skip the call."""
    host = urlparse(url).hostname or ""
    gap = HOST_MIN_GAP_SEC.get(host)
    if not gap:
        return True
    if time.time() < _read_float(CACHE_DIR / f"pace_{host}.cool"):
        return False
    stamp = CACHE_DIR / f"pace_{host}.ts"
    with _file_lock(CACHE_DIR / f"pace_{host}.lock", wait_sec=_SLOT_WAIT_SEC) as got:
        if not got:
            return False
        wait = _read_float(stamp) + gap - time.time()
        if wait > 0:
            time.sleep(wait)
        try:
            stamp.write_text(str(time.time()))
        except OSError:
            pass
    return True


def _start_host_cooldown(url: str) -> None:
    host = urlparse(url).hostname or ""
    try:
        (CACHE_DIR / f"pace_{host}.cool").write_text(str(time.time() + HOST_COOLDOWN_SEC))
    except OSError:
        pass


class HttpClient:
    def __init__(self, timeout_sec: float = 12.0, max_retries: int = 3) -> None:
        self.timeout_sec = timeout_sec
        self.max_retries = max_retries
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "solana-desk-paper/1.0 (+paper-trading-only; no-wallet)",
                "Accept": "application/json",
            }
        )

    def get_json(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        source: str = "http",
    ) -> dict[str, Any] | list[Any] | None:
        """GET JSON. On failure returns None and logs; never raises."""
        ttl = _ttl_for(source)
        if ttl <= 0:
            return self._get_json_live(url, params=params, source=source)
        full = url + ("?" + urlencode(sorted(params.items())) if params else "")
        key = hashlib.sha1(full.encode("utf-8")).hexdigest()
        path = CACHE_DIR / f"{key}.json"
        hit, data = _read_fresh(path, ttl)
        if hit:
            return data
        with _file_lock(CACHE_DIR / f"{key}.lock", wait_sec=_LOCK_WAIT_SEC):
            # another desk may have filled it while we waited
            hit, data = _read_fresh(path, ttl)
            if hit:
                return data
            data = self._get_json_live(url, params=params, source=source)
            if data is not None:
                _write_atomic(path, data)
            elif ttl >= STALE_MIN_TTL_SEC:
                hit, data = _read_fresh(path, STALE_MAX_SEC)
        if random.random() < 0.002:
            _prune_cache()
        return data

    def _get_json_live(
        self,
        url: str,
        *,
        params: dict[str, Any] | None,
        source: str,
    ) -> dict[str, Any] | list[Any] | None:
        backoff = 1.0
        paced = _is_paced(url)
        for attempt in range(1, self.max_retries + 1):
            try:
                if not _take_host_slot(url):
                    return None
                resp = self.session.get(url, params=params, timeout=self.timeout_sec)
                if resp.status_code == 429 and paced:
                    log.warning("%s rate-limited 429; host cooldown %.0fs (%s)", source, HOST_COOLDOWN_SEC, url)
                    _start_host_cooldown(url)
                    return None
                if resp.status_code == 429:
                    sleep_for = backoff + random.uniform(0, 0.5)
                    log.warning("%s rate-limited 429; backoff %.1fs (%s)", source, sleep_for, url)
                    time.sleep(sleep_for)
                    backoff = min(backoff * 2, 30.0)
                    continue
                if resp.status_code >= 400:
                    log.warning(
                        "%s HTTP %s on attempt %s: %s",
                        source,
                        resp.status_code,
                        attempt,
                        url,
                    )
                    if attempt < self.max_retries:
                        time.sleep(backoff)
                        backoff = min(backoff * 2, 30.0)
                        continue
                    return None
                return resp.json()
            except requests.Timeout:
                log.warning("%s timeout attempt %s: %s", source, attempt, url)
            except requests.RequestException as exc:
                log.warning("%s error attempt %s: %s (%s)", source, attempt, url, exc)
            except ValueError as exc:
                log.warning("%s bad JSON attempt %s: %s (%s)", source, attempt, url, exc)
                return None
            if attempt < self.max_retries:
                time.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
        return None

    def post_json(
        self,
        url: str,
        payload: dict[str, Any],
        *,
        source: str = "http",
    ) -> dict[str, Any] | list[Any] | None:
        backoff = 1.0
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self.session.post(url, json=payload, timeout=self.timeout_sec)
                if resp.status_code == 429:
                    sleep_for = backoff + random.uniform(0, 0.5)
                    log.warning("%s rate-limited 429; backoff %.1fs", source, sleep_for)
                    time.sleep(sleep_for)
                    backoff = min(backoff * 2, 30.0)
                    continue
                if resp.status_code >= 400:
                    log.warning("%s HTTP %s: %s", source, resp.status_code, url)
                    if attempt < self.max_retries:
                        time.sleep(backoff)
                        backoff = min(backoff * 2, 30.0)
                        continue
                    return None
                return resp.json()
            except (requests.RequestException, ValueError) as exc:
                log.warning("%s error attempt %s: %s", source, attempt, exc)
                if attempt < self.max_retries:
                    time.sleep(backoff)
                    backoff = min(backoff * 2, 30.0)
        return None
