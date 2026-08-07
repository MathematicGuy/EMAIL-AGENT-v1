from datetime import UTC, datetime, timedelta

from cowork_agent.domain.target_contracts import (
    BodyFormat,
    EphemeralEmailEnvelope,
    FetchStatus,
)
from cowork_agent.features.email_action_plan.short_term import ShortTermStore

START = datetime(2026, 8, 3, 8, tzinfo=UTC)


class FakeClock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def envelope(message_id: str) -> EphemeralEmailEnvelope:
    return EphemeralEmailEnvelope(
        run_id="run_test",
        tenant_id="local",
        user_id="u1",
        gmail_message_id=message_id,
        gmail_thread_id=f"thread-{message_id}",
        gmail_url=f"https://mail.google.com/mail/u/0/#inbox/{message_id}",
        sender_name="Người Gửi",
        sender_email="sender@example.com",
        recipients=(),
        subject="Chủ đề",
        received_at=START,
        labels=(),
        normalized_body="Nội dung thô",
        body_format=BodyFormat.TEXT,
        attachments_present=False,
        fetch_status=FetchStatus.COMPLETE,
    )


def test_put_get_round_trip_returns_tuple_copy() -> None:
    store = ShortTermStore(ttl_seconds=60, clock=FakeClock(START))
    source = [envelope("m1"), envelope("m2")]

    store.put("run_1", source)
    stored = store.get("run_1")

    assert isinstance(stored, tuple)
    assert stored == tuple(source)
    source.append(envelope("m3"))  # mutating the input cannot change the stored copy
    assert store.get("run_1") == tuple(source[:2])


def test_get_unknown_run_returns_none() -> None:
    store = ShortTermStore(clock=FakeClock(START))
    assert store.get("run_missing") is None


def test_clear_is_idempotent_finalizer_safe_for_unknown_ids() -> None:
    store = ShortTermStore(ttl_seconds=60, clock=FakeClock(START))
    store.put("run_1", [envelope("m1")])

    store.clear("run_1")
    assert store.get("run_1") is None

    store.clear("run_1")  # idempotent
    store.clear("run_unknown")  # safe for unknown ids
    assert store.sweep() == 0


def test_get_after_ttl_expiry_returns_none_and_evicts_entry() -> None:
    clock = FakeClock(START)
    store = ShortTermStore(ttl_seconds=60, clock=clock)
    store.put("run_1", [envelope("m1")])
    assert store.get("run_1") is not None

    clock.advance(60)  # expires_at <= clock() means expired
    assert store.get("run_1") is None

    clock.now = START  # rewind: the entry was evicted, not merely expired
    assert store.get("run_1") is None


def test_default_ttl_is_1800_seconds() -> None:
    clock = FakeClock(START)
    store = ShortTermStore(clock=clock)
    store.put("run_1", [envelope("m1")])

    clock.advance(1799)
    assert store.get("run_1") is not None
    clock.advance(1)
    assert store.get("run_1") is None


def test_sweep_removes_only_expired_entries_and_returns_count() -> None:
    clock = FakeClock(START)
    store = ShortTermStore(ttl_seconds=60, clock=clock)
    store.put("run_old", [envelope("m1")])
    clock.advance(30)
    store.put("run_fresh", [envelope("m2")])
    clock.advance(31)  # run_old lived 61s (expired); run_fresh lived 31s (live)

    assert store.sweep() == 1
    assert store.get("run_old") is None
    assert store.get("run_fresh") is not None
    assert store.sweep() == 0


def test_put_sweeps_expired_entries_before_storing() -> None:
    clock = FakeClock(START)
    store = ShortTermStore(ttl_seconds=60, clock=clock)
    store.put("run_old", [envelope("m1")])
    clock.advance(61)

    store.put("run_fresh", [envelope("m2")])

    assert store.get("run_old") is None
    assert store.get("run_fresh") is not None
    assert store.sweep() == 0  # put() already swept the expired entry
