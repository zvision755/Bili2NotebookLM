"""Bounded exponential backoff, only for errors that are safe to repeat."""

from __future__ import annotations

import time
from collections.abc import Callable

from bili2nb.errors import Bili2nbError

RETRYABLE = ("RATE_LIMITED", "NETWORK_ERROR")
MAX_ATTEMPTS = 5  # bounded: 1 try + 4 backoff retries, then fail with a resumable job


def with_backoff(
    fn: Callable[[], object],
    what: str,
    *,
    log: Callable[[str], None],
    sleep: Callable[[float], None] = time.sleep,
    base: float = 3.0,
):
    for i in range(MAX_ATTEMPTS):
        try:
            return fn()
        except Bili2nbError as e:
            if e.code not in RETRYABLE or i == MAX_ATTEMPTS - 1:
                raise
            delay = base * (3**i)
            log(f"  {what}: {e.code}，{delay:.0f}s 后重试 ({i + 1}/{MAX_ATTEMPTS - 1})…")
            sleep(delay)
    raise AssertionError("unreachable")
