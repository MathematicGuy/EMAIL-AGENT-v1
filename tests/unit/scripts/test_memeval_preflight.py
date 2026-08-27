from __future__ import annotations

from pathlib import Path

from scripts.memeval_preflight import (
    FAIL,
    OK,
    WARN,
    Check,
    cause_chain,
    check_checkout,
    check_target,
    describe_target,
    exit_code,
    looks_throwaway,
    render,
)


def test_target_checks_and_safety_guards() -> None:
    # Warning vs failure exit codes
    assert exit_code([Check("a", OK, ""), Check("b", WARN, "")]) == 0
    assert exit_code([Check("a", OK, ""), Check("b", FAIL, "")]) == 1

    # Describing target hides passwords
    assert (
        describe_target("postgresql://cowork:hunter2@127.0.0.1:5432/cowork_memeval")
        == "127.0.0.1:5432/cowork_memeval"
    )
    assert looks_throwaway("postgresql://u:p@127.0.0.1:5432/cowork_memeval")
    assert not looks_throwaway("postgresql://u:p@127.0.0.1:5432/cowork")

    # Remote target is FAIL
    check_remote, _ = check_target({"PG_TEST_URL": "postgresql://u:p@db.example.com:5432/prod"})
    assert check_remote.status == FAIL

    # MEMEVAL_ALLOW_REMOTE_POSTGRES override is itself FAIL in preflight
    check_override, _ = check_target(
        {
            "PG_TEST_URL": "postgresql://u:p@127.0.0.1:5432/cowork_memeval",
            "MEMEVAL_ALLOW_REMOTE_POSTGRES": "1",
        }
    )
    assert check_override.status == FAIL

    # Local non-throwaway warns; local throwaway passes
    assert check_target({"PG_TEST_URL": "postgresql://u:p@127.0.0.1:5432/cowork"})[0].status == WARN
    assert (
        check_target({"PG_TEST_URL": "postgresql://u:p@127.0.0.1:5432/cowork_memeval"})[0].status
        == OK
    )

    # POSTGRES_MODE=off targets SQLite and warns
    check_sqlite, url = check_target({"POSTGRES_MODE": "off"})
    assert check_sqlite.status == WARN and url == ""


def test_cause_chain_and_rendering() -> None:
    try:
        try:
            raise TimeoutError("read timed out")
        except TimeoutError as inner:
            raise RuntimeError("configured chat provider is unavailable") from inner
    except RuntimeError as error:
        chain = cause_chain(error)
    assert chain.startswith("RuntimeError: configured chat provider is unavailable")
    assert "TimeoutError: read timed out" in chain

    rendered = render([Check("chat", FAIL, "no key"), Check("target", OK, "local")])
    assert rendered.splitlines()[-1] == "NOT READY - chat"


def test_checkout_validation(tmp_path: Path) -> None:
    assert check_checkout(tmp_path).status == FAIL
