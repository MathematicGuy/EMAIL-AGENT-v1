import asyncio

import pytest

from cowork_agent.features.batch_evaluation.contracts import CredentialState
from cowork_agent.features.batch_evaluation.credentials import CredentialLeasingPool


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def test_credential_leasing_aliases_and_exclusivity() -> None:
    async def scenario() -> None:
        pool = CredentialLeasingPool.from_env(
            "MISTRAL_API_KEY",
            {
                "MISTRAL_API_KEY": "secret-a",
                "MISTRAL_API_KEY2": "secret-b",
                "MISTRAL_API_KEY3": "secret-c",
            },
            clock=FakeClock(),
        )

        leases = await asyncio.gather(pool.lease(), pool.lease(), pool.lease())
        assert {lease.alias for lease in leases} == {"mistral-1", "mistral-2", "mistral-3"}
        assert "secret-" not in repr(leases)

        # Exclusivity: pool is now empty
        with pytest.raises(RuntimeError, match="No healthy credential"):
            await pool.lease()

        # Releasing allows reuse
        await leases[0].release()
        assert (await pool.lease()).alias == "mistral-1"

    asyncio.run(scenario())


def test_credential_cooldown_lifecycle_and_recovery() -> None:
    async def scenario() -> None:
        clock = FakeClock()
        pool = CredentialLeasingPool.from_env(
            "MISTRAL_API_KEY", {"MISTRAL_API_KEY": "secret-a"}, clock=clock
        )
        lease = await pool.lease()
        await lease.cool_down(30)

        assert pool.state_for("mistral-1") is CredentialState.COOLING_DOWN
        with pytest.raises(RuntimeError, match="No healthy credential"):
            await pool.lease()

        # After delay expires -> recovered
        clock.now += 30
        recovered = await pool.lease()
        assert recovered.alias == "mistral-1"
        assert pool.state_for("mistral-1") is CredentialState.LEASED

        # Retained cooldown
        await recovered.hold_cooldown(30)
        await recovered.release()
        assert pool.state_for("mistral-1") is CredentialState.COOLING_DOWN
        with pytest.raises(RuntimeError, match="No healthy credential"):
            await pool.lease()

        clock.now += 30
        assert (await pool.lease()).alias == "mistral-1"

    asyncio.run(scenario())


def test_credential_disabling_cancellation_and_cross_pool_protection() -> None:
    async def scenario() -> None:
        # Disabling
        pool = CredentialLeasingPool.from_env(
            "MISTRAL_API_KEY",
            {"MISTRAL_API_KEY": "secret-a", "MISTRAL_API_KEY2": "secret-b"},
            clock=FakeClock(),
        )
        first = await pool.lease()
        await first.disable()
        assert pool.state_for("mistral-1") is CredentialState.DISABLED
        second = await pool.lease()
        assert second.alias == "mistral-2"
        await second.release()

        # Async context manager cancellation recovery
        pool_single = CredentialLeasingPool.from_env(
            "MISTRAL_API_KEY", {"MISTRAL_API_KEY": "secret-a"}, clock=FakeClock()
        )
        with pytest.raises(asyncio.CancelledError):
            async with await pool_single.lease():
                raise asyncio.CancelledError
        recovered_lease = await pool_single.lease()
        assert recovered_lease.alias == "mistral-1"
        await recovered_lease.release()

        # Cross pool protection
        pool_other = CredentialLeasingPool.from_env(
            "MISTRAL_API_KEY", {"MISTRAL_API_KEY": "secret-other"}, clock=FakeClock()
        )
        active_lease = await pool_single.lease()
        for op in ("release", "cool_down", "hold_cooldown", "disable"):
            with pytest.raises(RuntimeError, match="belongs to another pool"):
                if op == "release":
                    await pool_other.release(active_lease)
                elif op == "cool_down":
                    await pool_other.cool_down(active_lease, 30)
                elif op == "hold_cooldown":
                    await pool_other.hold_cooldown(active_lease, 30)
                else:
                    await pool_other.disable(active_lease)

    asyncio.run(scenario())
