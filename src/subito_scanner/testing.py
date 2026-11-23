"""Test helpers: a fake HTTP session and clock, so tests never touch the network or really sleep.

    time = FakeTime()
    session = FakeSession(time, [response(200, payload)])
    scanner = SubitoScanner(session=session, clock=time.clock, sleep=time.sleep, wall_clock=time.wall)

Adapted from `pch_marketplace_kit.testing` (MIT, Copyright (c) 2026 Tdavide04).
"""
import json
from datetime import UTC, datetime
from typing import Any

import requests

WALL_START = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)


def response(status: int, body: object = None, headers: dict[str, str] | None = None, raw: bytes | None = None,
             url: str = "https://hades.subito.it/v1/search/items") -> requests.Response:
    """A real `requests.Response` with a JSON body (or `raw` bytes)."""
    r = requests.Response()
    r.status_code = status
    r._content = raw if raw is not None else json.dumps(body if body is not None else {}).encode()
    r.headers.update({"Content-Type": "application/json", **(headers or {})})
    r.url = url
    return r


class FakeTime:
    """Monotonic clock that only moves when the code under test sleeps."""

    def __init__(self, start: float = 1000.0):
        self.start = start
        self.now = start
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def wall(self) -> datetime:
        return datetime.fromtimestamp(WALL_START.timestamp() + (self.now - self.start), UTC)


class FakeSession:
    """Plays back scripted outcomes (responses or exceptions) and records each call with its time."""

    def __init__(self, time: FakeTime, outcomes: list[Any]):
        self.time = time
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []

    def request(self, method: str, url: str, params: Any = None, headers: Any = None, timeout: Any = None, **kwargs: Any) -> Any:
        self.calls.append({"method": method, "url": url, "params": params, "headers": headers, "timeout": timeout, "at": self.time.now, **kwargs})
        if not self.outcomes:
            raise AssertionError(f"unexpected request: {method} {url}")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome
