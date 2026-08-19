"""The parts of the pre-flight that decide, rather than the parts that call out.

The live calls are the point of the script and cannot be unit tested without
becoming a fake of the thing they exist to distrust. What is tested here is
everything that decides whether a run may proceed.
"""

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


def test_a_warning_does_not_stop_a_run_and_a_failure_does() -> None:
    assert exit_code([Check("a", OK, ""), Check("b", WARN, "")]) == 0
    assert exit_code([Check("a", OK, ""), Check("b", FAIL, "")]) == 1


def test_the_target_is_described_without_its_password() -> None:
    described = describe_target("postgresql://cowork:hunter2@127.0.0.1:5432/cowork_memeval")
    assert described == "127.0.0.1:5432/cowork_memeval"
    assert "hunter2" not in described


def test_a_database_named_like_a_throwaway_is_recognised() -> None:
    assert looks_throwaway("postgresql://u:p@127.0.0.1:5432/cowork_memeval")
    assert not looks_throwaway("postgresql://u:p@127.0.0.1:5432/cowork")


def test_the_cause_chain_names_what_the_adapter_hid() -> None:
    try:
        try:
            raise TimeoutError("read timed out")
        except TimeoutError as inner:
            raise RuntimeError("configured chat provider is unavailable") from inner
    except RuntimeError as error:
        chain = cause_chain(error)
    assert chain.startswith("RuntimeError: configured chat provider is unavailable")
    assert "TimeoutError: read timed out" in chain


def test_a_remote_target_fails_the_check_before_anything_is_seeded() -> None:
    check, url = check_target({"PG_TEST_URL": "postgresql://u:p@db.example.com:5432/prod"})
    assert check.status == FAIL
    assert "not local" in check.detail
    assert url.endswith("/prod")


def test_the_remote_override_being_set_is_itself_a_failure() -> None:
    # The guard exists so a remote target is refused. A run that has already
    # switched the guard off has nothing left to refuse it, so the pre-flight
    # refuses instead.
    check, _ = check_target(
        {
            "PG_TEST_URL": "postgresql://u:p@127.0.0.1:5432/cowork_memeval",
            "MEMEVAL_ALLOW_REMOTE_POSTGRES": "1",
        }
    )
    assert check.status == FAIL
    assert "MEMEVAL_ALLOW_REMOTE_POSTGRES" in check.detail


def test_a_local_database_not_named_like_a_throwaway_only_warns() -> None:
    check, _ = check_target({"PG_TEST_URL": "postgresql://u:p@127.0.0.1:5432/cowork"})
    assert check.status == WARN


def test_a_local_throwaway_passes() -> None:
    check, _ = check_target({"PG_TEST_URL": "postgresql://u:p@127.0.0.1:5432/cowork_memeval"})
    assert check.status == OK


def test_a_directory_without_the_harness_fails_the_checkout_check(tmp_path: Path) -> None:
    assert check_checkout(tmp_path).status == FAIL


def test_the_summary_line_names_what_failed() -> None:
    rendered = render([Check("chat", FAIL, "no key"), Check("target", OK, "local")])
    assert rendered.splitlines()[-1] == "NOT READY - chat"
