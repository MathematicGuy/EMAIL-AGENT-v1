"""Unit tests for Circuit Breaker and Threat Intel Resilience (Task 3.3)."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from cowork_agent.domain.target_contracts import ThreatLevel
from cowork_agent.integrations.security.resilience import (
    CircuitBreaker,
    CircuitBreakerOpenError,
    CircuitBreakerState,
)
from cowork_agent.integrations.security.threat_intel import GoogleWebRiskThreatIntel


@pytest.mark.asyncio
async def test_circuit_breaker_normal_execution():
    cb = CircuitBreaker(name="test_cb", failure_threshold=3)

    async def normal_op() -> str:
        return "success_data"

    result, is_degraded = await cb.execute(normal_op, fallback="fallback_data")
    assert result == "success_data"
    assert is_degraded is False
    assert cb.state == CircuitBreakerState.CLOSED
    assert cb.consecutive_failures == 0


@pytest.mark.asyncio
async def test_circuit_breaker_trips_to_open_after_threshold():
    cb = CircuitBreaker(name="test_cb", failure_threshold=3, recovery_timeout_seconds=0.1)

    async def failing_op() -> str:
        raise ConnectionError("External API Down")

    # 1st failure
    res1, deg1 = await cb.execute(failing_op, fallback="fallback_1")
    assert res1 == "fallback_1"
    assert deg1 is True
    assert cb.state == CircuitBreakerState.CLOSED
    assert cb.consecutive_failures == 1

    # 2nd failure
    res2, deg2 = await cb.execute(failing_op, fallback="fallback_2")
    assert res2 == "fallback_2"
    assert deg2 is True
    assert cb.state == CircuitBreakerState.CLOSED
    assert cb.consecutive_failures == 2

    # 3rd failure -> Trips to OPEN
    res3, deg3 = await cb.execute(failing_op, fallback="fallback_3")
    assert res3 == "fallback_3"
    assert deg3 is True
    assert cb.state == CircuitBreakerState.OPEN
    assert cb.is_degraded is True

    # 4th call when OPEN -> Fast fails without calling failing_op
    op_called = False

    async def probe_op() -> str:
        nonlocal op_called
        op_called = True
        return "probe"

    res4, deg4 = await cb.execute(probe_op, fallback="fast_fallback")
    assert res4 == "fast_fallback"
    assert deg4 is True
    assert op_called is False  # Fast-fail bypassed the probe call


@pytest.mark.asyncio
async def test_circuit_breaker_recovery_to_half_open_and_closed():
    cb = CircuitBreaker(
        name="test_cb",
        failure_threshold=2,
        recovery_timeout_seconds=0.05,
        half_open_success_threshold=1,
    )

    async def failing_op() -> str:
        raise TimeoutError("Network timeout")

    # Trip to OPEN
    await cb.execute(failing_op, fallback="fb")
    await cb.execute(failing_op, fallback="fb")
    assert cb.state == CircuitBreakerState.OPEN

    # Wait for recovery timeout
    await asyncio.sleep(0.06)
    assert cb.state == CircuitBreakerState.HALF_OPEN

    # Successful call in HALF_OPEN recovers to CLOSED
    async def recovered_op() -> str:
        return "recovered_data"

    res, deg = await cb.execute(recovered_op, fallback="fb")
    assert res == "recovered_data"
    assert deg is False
    assert cb.state == CircuitBreakerState.CLOSED
    assert cb.consecutive_failures == 0


@pytest.mark.asyncio
async def test_circuit_breaker_raises_when_open_without_fallback():
    cb = CircuitBreaker(name="test_cb", failure_threshold=1)

    async def failing_op() -> str:
        raise RuntimeError("Error")

    with pytest.raises(RuntimeError):
        await cb.execute(failing_op)

    assert cb.state == CircuitBreakerState.OPEN

    with pytest.raises(CircuitBreakerOpenError) as exc_info:
        await cb.execute(failing_op)

    assert "is OPEN" in str(exc_info.value)


@pytest.mark.asyncio
async def test_webrisk_circuit_breaker_fallback_degraded_flag():
    cb = CircuitBreaker(name="WebRiskMock", failure_threshold=2, recovery_timeout_seconds=10.0)
    intel = GoogleWebRiskThreatIntel(
        api_key="fake-key-123",
        timeout_seconds=0.5,
        circuit_breaker=cb,
    )

    with patch(
        "httpx.AsyncClient.get",
        side_effect=TimeoutError("Web Risk API Connection Timeout"),
    ):
        # 1st call fails
        rep1 = await intel.check_url("https://example.com/login")
        assert rep1.threat_level == ThreatLevel.CLEAN
        assert "[SECURITY_SCAN_DEGRADED" in (rep1.details or "")

        # 2nd call fails -> trips circuit breaker
        rep2 = await intel.check_url("https://example.com/verify")
        assert rep2.threat_level == ThreatLevel.CLEAN
        assert cb.state == CircuitBreakerState.OPEN

        # 3rd call -> fast-fails immediately via open circuit
        rep3 = await intel.check_url("https://example.com/dashboard")
        assert rep3.threat_level == ThreatLevel.CLEAN
        assert "[SECURITY_SCAN_DEGRADED" in (rep3.details or "")
