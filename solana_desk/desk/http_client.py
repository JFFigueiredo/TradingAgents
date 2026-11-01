"""HTTP helpers with timeout, retries, and 429 backoff. Never raises into the desk loop."""
from __future__ import annotations

import logging
import random
import time
from typing import Any

import requests

log = logging.getLogger("desk.http")


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
        backoff = 1.0
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout_sec)
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
