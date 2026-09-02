"""Circuit Breaker & Fallback Resilience Engine for External Threat Intelligence."""

from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable
from enum import StrEnum
from time import monotonic
from typing import Final, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

DEFAULT_FAILURE_THRESHOLD: Final[int] = 3
DEFAULT_RECOVERY_TIMEOUT_SECONDS: Final[float] = 30.0
DEFAULT_HALF_OPEN_SUCCESS_THRESHOLD: Final[int] = 1


class CircuitBreakerState(StrEnum):
    """Operational state of the Circuit Breaker."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerOpenError(Exception):
    """Raised when a call is rejected because the circuit is OPEN with no fallback."""

    def __init__(self, name: str, recovery_seconds_left: float) -> None:
        super().__init__(
            f"CircuitBreaker '{name}' is OPEN. Re-testing available in {recovery_seconds_left:.1f}s"
        )
        self.name = name
        self.recovery_seconds_left = recovery_seconds_left


class CircuitBreaker:
    """State machine preventing cascading failures when external security APIs degrade."""

    def __init__(
        self,
        name: str = "default",
        *,
        failure_threshold: int = DEFAULT_FAILURE_THRESHOLD,
        recovery_timeout_seconds: float = DEFAULT_RECOVERY_TIMEOUT_SECONDS,
        half_open_success_threshold: int = DEFAULT_HALF_OPEN_SUCCESS_THRESHOLD,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout_seconds = recovery_timeout_seconds
        self.half_open_success_threshold = half_open_success_threshold

        self._state: CircuitBreakerState = CircuitBreakerState.CLOSED
        self._consecutive_failures: int = 0
        self._consecutive_successes: int = 0
        self._last_state_change: float = monotonic()
        self._last_failure_time: float | None = None

    @property
    def state(self) -> CircuitBreakerState:
        """Get current state with automatic transition to HALF_OPEN upon recovery timeout."""
        if self._state == CircuitBreakerState.OPEN:
            if self._last_failure_time is not None:
                elapsed = monotonic() - self._last_failure_time
                if elapsed >= self.recovery_timeout_seconds:
                    logger.info(
                        "CircuitBreaker '%s' transitioning OPEN -> HALF_OPEN (%.1fs elapsed)",
                        self.name,
                        elapsed,
                    )
                    self._state = CircuitBreakerState.HALF_OPEN
                    self._consecutive_successes = 0
                    self._last_state_change = monotonic()
        return self._state

    @property
    def is_degraded(self) -> bool:
        """True if the circuit is non-closed (either OPEN or evaluating HALF_OPEN)."""
        return self.state != CircuitBreakerState.CLOSED

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    def record_success(self) -> None:
        """Record a successful call to the external service."""
        current = self.state
        if current == CircuitBreakerState.HALF_OPEN:
            self._consecutive_successes += 1
            if self._consecutive_successes >= self.half_open_success_threshold:
                logger.info(
                    "CircuitBreaker '%s' transitioning HALF_OPEN -> CLOSED (recovered)",
                    self.name,
                )
                self._state = CircuitBreakerState.CLOSED
                self._consecutive_failures = 0
                self._consecutive_successes = 0
                self._last_state_change = monotonic()
        elif current == CircuitBreakerState.CLOSED:
            self._consecutive_failures = 0

    def record_failure(self, exc: BaseException | None = None) -> None:
        """Record a failed call or timeout to the external service."""
        now = monotonic()
        self._last_failure_time = now
        self._consecutive_failures += 1

        if self._state == CircuitBreakerState.HALF_OPEN:
            logger.warning(
                "CircuitBreaker '%s' probe failed in HALF_OPEN -> reopening to OPEN: %s",
                self.name,
                exc,
            )
            self._state = CircuitBreakerState.OPEN
            self._last_state_change = now
        elif self._state == CircuitBreakerState.CLOSED:
            if self._consecutive_failures >= self.failure_threshold:
                logger.warning(
                    "CircuitBreaker '%s' threshold reached (%d failures) -> tripping OPEN: %s",
                    self.name,
                    self._consecutive_failures,
                    exc,
                )
                self._state = CircuitBreakerState.OPEN
                self._last_state_change = now

    def reset(self) -> None:
        """Manually reset circuit breaker to closed state."""
        self._state = CircuitBreakerState.CLOSED
        self._consecutive_failures = 0
        self._consecutive_successes = 0
        self._last_failure_time = None
        self._last_state_change = monotonic()

    async def execute(
        self,
        func: Callable[[], Awaitable[T]],
        *,
        fallback: Callable[[], Awaitable[T] | T] | T | None = None,
    ) -> tuple[T, bool]:
        """Execute an async operation with circuit breaking protection and fallback.

        Returns (result, is_degraded)
        """
        current = self.state

        if current == CircuitBreakerState.OPEN:
            recovery_left = max(
                0.0,
                self.recovery_timeout_seconds - (monotonic() - (self._last_failure_time or 0.0)),
            )
            logger.debug(
                "CircuitBreaker '%s' is OPEN. Fast-failing to fallback (%.1fs left)",
                self.name,
                recovery_left,
            )
            if fallback is not None:
                res = await self._resolve_fallback(fallback)
                return res, True
            raise CircuitBreakerOpenError(self.name, recovery_left)

        # CLOSED or HALF_OPEN -> attempt execution
        try:
            result = await func()
            self.record_success()
            return result, False
        except Exception as exc:
            self.record_failure(exc)
            if fallback is not None:
                logger.info(
                    "CircuitBreaker '%s' caught error, falling back: %s",
                    self.name,
                    exc,
                )
                res = await self._resolve_fallback(fallback)
                return res, True
            raise

    async def _resolve_fallback(
        self,
        fallback: Callable[[], Awaitable[T] | T] | T,
    ) -> T:
        if callable(fallback):
            res = fallback()
            if inspect.isawaitable(res):
                return await res
            return res
        return fallback
