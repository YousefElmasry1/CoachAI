"""
CoachAI - Minimal per-user rate limiter for user-triggered AI calls.

Guards planner_service.py, recommendation_service.py, and
chatbot_service.py against a single user firing unlimited rapid
requests at the Gemini API (accidental double-clicks, a scripted
loop, or deliberate abuse) — none of which had any protection before
this module existed.

Design constraints (per audit scope):
    - Streamlit-compatible: no FastAPI, no Redis, no Celery, no
      distributed architecture.
    - State lives in a plain module-level dict guarded by a lock, NOT
      in st.session_state. st.session_state is per-browser-tab, so a
      limiter keyed off it could be trivially bypassed by opening a
      second tab; keying off user_id in process-wide memory closes
      that gap.
    - Reusable: one small class, one instance per call-site with its
      own (max_requests, window_seconds), so callers don't share
      buckets by accident.

Known limitation (documented, not silently hidden):
    This is a single-process, in-memory limiter. It resets on process
    restart and does NOT share state across multiple Streamlit worker
    processes/replicas behind a load balancer. For the single-process
    Streamlit deployment this app targets, that's sufficient. A
    multi-replica deployment would need a shared store (e.g. Redis),
    which is explicitly out of scope here.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Deque, Dict, Optional


class RateLimitExceeded(RuntimeError):
    """
    Raised when a user has exceeded their allotted request rate.

    Subclasses RuntimeError so existing ``except RuntimeError`` UI
    error handling (e.g. pages/1_AI_Coach.py's "couldn't reach the
    model / rate-limited" message) already displays this sensibly
    without any UI changes.
    """

    def __init__(self, retry_after_seconds: float, message: Optional[str] = None) -> None:
        self.retry_after_seconds = max(0.0, retry_after_seconds)
        super().__init__(
            message
            or (
                "You're sending requests too quickly. Please wait "
                f"{self.retry_after_seconds:.0f} seconds and try again."
            )
        )


class RateLimiter:
    """
    A minimal per-key sliding-window rate limiter.

    Thread-safe. Memory use is bounded by (distinct keys seen) x
    (max_requests), since each key only ever keeps up to
    ``max_requests`` timestamps.
    """

    def __init__(self, max_requests: int, window_seconds: float) -> None:
        if max_requests <= 0:
            raise ValueError("max_requests must be positive.")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive.")
        self.max_requests = max_requests
        self.window_seconds = float(window_seconds)
        self._lock = threading.Lock()
        self._hits: Dict[str, Deque[float]] = {}

    def check(self, key: Optional[str]) -> None:
        """
        Record a request attempt for ``key`` and raise
        RateLimitExceeded if it would put them over the limit.

        Call this BEFORE doing the actual (expensive/paid) work, so a
        blocked request never reaches the LLM call.

        Args:
            key: Identity to rate-limit on (e.g. user_id). A missing
                identity is a distinct failure mode from "over limit"
                -- callers must supply a real key rather than silently
                falling back to a shared bucket that would let one
                user's traffic throttle every other user.

        Raises:
            ValueError: If key is empty/None.
            RateLimitExceeded: If key has hit max_requests within the
                current window.
        """
        if not key:
            raise ValueError(
                "RateLimiter.check() requires a non-empty identity key; "
                "refusing to fall back to a shared/anonymous bucket."
            )

        now = time.monotonic()
        with self._lock:
            hits = self._hits.setdefault(key, deque())

            # Drop timestamps that have aged out of the window.
            while hits and now - hits[0] > self.window_seconds:
                hits.popleft()

            if len(hits) >= self.max_requests:
                retry_after = self.window_seconds - (now - hits[0])
                raise RateLimitExceeded(retry_after_seconds=retry_after)

            hits.append(now)

    def reset(self, key: str) -> None:
        """Clear one key's history. Mainly useful for tests."""
        with self._lock:
            self._hits.pop(key, None)

    def reset_all(self) -> None:
        """Clear all history. Mainly useful for tests."""
        with self._lock:
            self._hits.clear()