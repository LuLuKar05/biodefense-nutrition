"""
circuit_breaker.py — Reusable three-state circuit breaker
=========================================================
States:
  CLOSED    → Normal operation, calls go through
  OPEN      → Tripped after N failures, skip calls, use fallback
  HALF_OPEN → Cooldown expired, try ONE call to test recovery

Used by: onboarding_agent (FLock calls), orchestrator (intent classifier).
"""
from __future__ import annotations

import logging
import time
from enum import Enum

log = logging.getLogger("circuit_breaker")


class CBState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """
    Three-state circuit breaker for external API calls.

    Args:
        name:          Friendly name for logging (e.g., "flock_onboarding")
        max_failures:  How many consecutive failures before tripping
        cooldown_secs: Seconds to wait in OPEN before trying HALF_OPEN
    """

    def __init__(
        self,
        name: str = "default",
        max_failures: int = 3,
        cooldown_secs: float = 60.0,
    ) -> None:
        self.name = name
        self.max_failures = max_failures
        self.cooldown_secs = cooldown_secs

        self._state: CBState = CBState.CLOSED
        self._fail_count: int = 0
        self._opened_at: float = 0.0  # timestamp when breaker tripped

    # ── Public API ──────────────────────────────────────────

    @property
    def state(self) -> CBState:
        """Current state (auto-transitions OPEN → HALF_OPEN on cooldown)."""
        if self._state is CBState.OPEN:
            elapsed = time.monotonic() - self._opened_at
            if elapsed >= self.cooldown_secs:
                log.info(f"[{self.name}] cooldown expired ({elapsed:.0f}s) → HALF_OPEN")
                self._state = CBState.HALF_OPEN
        return self._state

    def should_call(self) -> bool:
        """Return True if the external call should be attempted."""
        s = self.state  # triggers auto-transition
        if s is CBState.CLOSED:
            return True
        if s is CBState.HALF_OPEN:
            log.info(f"[{self.name}] HALF_OPEN: allowing one test call")
            return True
        # OPEN
        return False

    def record_success(self) -> None:
        """Call succeeded — reset breaker to CLOSED."""
        was_half_open = self._state is CBState.HALF_OPEN
        self._fail_count = 0
        self._state = CBState.CLOSED
        if was_half_open:
            log.info(f"[{self.name}] HALF_OPEN test succeeded → CLOSED (recovered!)")

    def record_failure(self) -> None:
        """Call failed — increment counter, maybe trip breaker."""
        self._fail_count += 1
        if self._state is CBState.HALF_OPEN:
            # Half-open test failed — go back to OPEN
            self._state = CBState.OPEN
            self._opened_at = time.monotonic()
            log.warning(f"[{self.name}] HALF_OPEN test failed → OPEN (wait {self.cooldown_secs}s)")
        elif self._fail_count >= self.max_failures:
            self._state = CBState.OPEN
            self._opened_at = time.monotonic()
            log.warning(
                f"[{self.name}] {self._fail_count} failures → OPEN "
                f"(cooldown {self.cooldown_secs}s)"
            )
        else:
            log.info(f"[{self.name}] failure {self._fail_count}/{self.max_failures}")

    def force_reset(self) -> None:
        """Manually reset to CLOSED (e.g., on /start or config change)."""
        self._fail_count = 0
        self._state = CBState.CLOSED
        self._opened_at = 0.0
        log.info(f"[{self.name}] manually reset → CLOSED")

    # ── Introspection ───────────────────────────────────────

    @property
    def is_open(self) -> bool:
        return self.state is CBState.OPEN

    @property
    def is_closed(self) -> bool:
        return self.state is CBState.CLOSED

    @property
    def is_half_open(self) -> bool:
        return self.state is CBState.HALF_OPEN

    @property
    def fail_count(self) -> int:
        return self._fail_count

    def __repr__(self) -> str:
        return (
            f"CircuitBreaker(name={self.name!r}, state={self.state.value}, "
            f"fails={self._fail_count}/{self.max_failures})"
        )
