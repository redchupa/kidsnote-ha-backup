"""Pace all outgoing Kidsnote and attachment requests in a backup process."""
from __future__ import annotations

import math
import random
import time
from datetime import timezone
from email.utils import parsedate_to_datetime
from threading import Event

API_INTERVAL = 1
MEDIA_INTERVAL = 2
COOLDOWN_SECONDS = 60 * 60
COOLDOWN_SPREAD_SECONDS = 5 * 60
TRANSIENT_DELAYS = (5, 15)


class Stopping(Exception):
    """The supervisor has requested shutdown."""


class RequestDeferred(Exception):
    """A server requested a break from network traffic."""

    def __init__(self, status: int):
        self.status = status
        super().__init__(f"HTTP {status}")


def retry_after_seconds(header: str | None, now: float) -> float:
    if not header:
        return 0
    value = header.strip()
    if value.isascii() and value.isdecimal():
        seconds = float(value)
        return seconds if math.isfinite(seconds) else 0
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0, parsed.timestamp() - now)
    except (TypeError, ValueError, OSError, OverflowError):
        return 0


class RequestPacer:
    def __init__(self, stop: Event | None = None, *, api_interval: float = API_INTERVAL, media_interval: float = MEDIA_INTERVAL):
        self.stop = stop if stop is not None else Event()
        self.api_interval = api_interval
        self.media_interval = media_interval
        self.cooldown_until = 0.0
        self._next_request = 0.0

    def pause(self, delay: float):
        self._next_request = max(self._next_request, time.monotonic() + delay)

    def wait_until(self, deadline: float, wake: Event | None = None):
        while True:
            now = time.time()
            if wake is not None and wake.is_set() and now >= self.cooldown_until:
                wake.clear()
                if self.stop.wait(0):
                    raise Stopping
                return
            remaining = deadline - now
            slice_limit = 0.5 if wake is not None else 3600
            if self.stop.wait(min(max(0, remaining), slice_limit)):
                raise Stopping
            if remaining <= 0:
                return

    def wait(self, *, media: bool = False):
        self.wait_until(self.cooldown_until)
        if self.stop.wait(max(0, self._next_request - time.monotonic())):
            raise Stopping
        interval = self.media_interval if media else self.api_interval
        jitter = random.uniform(0, 0.5) if interval >= 1 else 0.0
        self._next_request = time.monotonic() + interval + jitter

    def check_response(self, status: int, retry_after: str | None):
        if status not in (429, 503):
            return
        now = time.time()
        delay = max(COOLDOWN_SECONDS, retry_after_seconds(retry_after, now))
        spread = random.uniform(0, COOLDOWN_SPREAD_SECONDS)
        self.cooldown_until = max(self.cooldown_until, now + delay + spread)
        raise RequestDeferred(status)
