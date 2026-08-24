import asyncio

import pytest

from cowork_agent.features.batch_evaluation.contracts import CredentialState
from cowork_agent.features.batch_evaluation.credentials import CredentialLeasingPool


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def test_three_keys_can_be_leased_once_each_without_secret_repr() -> None:
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

    asyncio.run(scenario())


def test_duplicate_keys_are_deduplicated_and_one_key_reuses_after_release() -> None:
    async def scenario() -> None:
        pool = CredentialLeasingPool.from_env(
            "MISTRAL_API_KEY",
            {"MISTRAL_API_KEY": "secret-a", "MISTRAL_API_KEY2": "secret-a"},
            clock=FakeClock(),
        )

        first = await pool.lease()
        await first.release()
        second = await pool.lease()

        assert first.alias == second.alias == "mistral-1"

    asyncio.run(scenario())


def test_exclusive_leases_reject_a_second_lease_until_the_first_is_released() -> None:
    async def scenario() -> None:
        pool = CredentialLeasingPool.from_env(
            "MISTRAL_API_KEY", {"MISTRAL_API_KEY": "secret-a"}, clock=FakeClock()
        )
        lease = await pool.lease()

        with pytest.raises(RuntimeError, match="No healthy credential"):
            await pool.lease()

        await lease.release()
        assert (await pool.lease()).alias == "mistral-1"

    asyncio.run(scenario())


def test_cooldown_recovers_after_the_provider_delay() -> None:
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

        clock.now += 30
        recovered = await pool.lease()
        assert recovered.alias == "mistral-1"
        assert pool.state_for("mistral-1") is CredentialState.LEASED

    asyncio.run(scenario())


def test_retained_cooldown_keeps_the_owning_lease_exclusive_past_its_deadline() -> None:
    async def scenario() -> None:
        clock = FakeClock()
        pool = CredentialLeasingPool.from_env(
            "MISTRAL_API_KEY", {"MISTRAL_API_KEY": "secret-a"}, clock=clock
        )
        owner = await pool.lease()
        await owner.hold_cooldown(30)

        assert pool.state_for(owner.alias) is CredentialState.COOLING_DOWN
        with pytest.raises(RuntimeError, match="No healthy credential"):
            await pool.lease()

        clock.now += 30

        assert pool.state_for(owner.alias) is CredentialState.LEASED
        with pytest.raises(RuntimeError, match="No healthy credential"):
            await pool.lease()

        await owner.release()
        assert (await pool.lease()).alias == owner.alias

    asyncio.run(scenario())


def test_releasing_a_retained_cooldown_leaves_the_alias_unavailable_until_deadline() -> None:
    async def scenario() -> None:
        clock = FakeClock()
        pool = CredentialLeasingPool.from_env(
            "MISTRAL_API_KEY", {"MISTRAL_API_KEY": "secret-a"}, clock=clock
        )
        owner = await pool.lease()
        await owner.hold_cooldown(30)
        await owner.release()

        assert pool.state_for(owner.alias) is CredentialState.COOLING_DOWN
        with pytest.raises(RuntimeError, match="No healthy credential"):
            await pool.lease()

        clock.now += 30

        recovered = await pool.lease()
        assert recovered.alias == owner.alias
        assert pool.state_for(owner.alias) is CredentialState.LEASED

    asyncio.run(scenario())


def test_disabled_credential_is_never_leased_again() -> None:
    async def scenario() -> None:
        pool = CredentialLeasingPool.from_env(
            "MISTRAL_API_KEY",
            {"MISTRAL_API_KEY": "secret-a", "MISTRAL_API_KEY2": "secret-b"},
            clock=FakeClock(),
        )
        first = await pool.lease()
        await first.disable()

        second = await pool.lease()
        await second.release()

        assert first.alias == "mistral-1"
        assert second.alias == "mistral-2"
        assert pool.state_for("mistral-1") is CredentialState.DISABLED

    asyncio.run(scenario())


def test_async_context_manager_releases_a_cancelled_lease() -> None:
    async def scenario() -> None:
        pool = CredentialLeasingPool.from_env(
            "MISTRAL_API_KEY", {"MISTRAL_API_KEY": "secret-a"}, clock=FakeClock()
        )

        async def cancelled_lane() -> None:
            async with await pool.lease():
                raise asyncio.CancelledError

        with pytest.raises(asyncio.CancelledError):
            await cancelled_lane()

        assert (await pool.lease()).alias == "mistral-1"

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["release", "cool_down", "hold_cooldown", "disable"])
def test_foreign_pool_cannot_settle_another_pools_active_lease(operation: str) -> None:
    async def scenario() -> None:
        pool_a = CredentialLeasingPool.from_env(
            "MISTRAL_API_KEY", {"MISTRAL_API_KEY": "secret-a"}, clock=FakeClock()
        )
        pool_b = CredentialLeasingPool.from_env(
            "MISTRAL_API_KEY", {"MISTRAL_API_KEY": "secret-b"}, clock=FakeClock()
        )
        lease_a = await pool_a.lease()

        with pytest.raises(RuntimeError, match="belongs to another pool"):
            if operation == "release":
                await pool_b.release(lease_a)
            elif operation == "cool_down":
                await pool_b.cool_down(lease_a, 30)
            elif operation == "hold_cooldown":
                await pool_b.hold_cooldown(lease_a, 30)
            else:
                await pool_b.disable(lease_a)

        assert pool_a.state_for(lease_a.alias) is CredentialState.LEASED
        with pytest.raises(RuntimeError, match="No healthy credential"):
            await pool_a.lease()

    asyncio.run(scenario())
