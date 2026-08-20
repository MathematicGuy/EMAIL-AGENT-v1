import importlib.util
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.extended

_LOADER_PATH = Path(__file__).resolve().parents[2] / "fixtures" / "routing" / "loader.py"
_spec = importlib.util.spec_from_file_location("routing_fixture_loader", _LOADER_PATH)
assert _spec is not None and _spec.loader is not None
loader = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(loader)

RoutingFixtureError = loader.RoutingFixtureError


def _write(tmp_path: Path, payload: object) -> Path:
    target = tmp_path / "routing_labels.json"
    target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return target


def _valid_case(**overrides: object) -> dict[str, object]:
    case: dict[str, object] = {
        "id": "r-001",
        "subject": "Subject",
        "sender": "sender@example.com",
        "body": "Body",
        "thread_id": None,
        "labels": {
            "actionability": "action_required",
            "email_is_sufficient": True,
            "expected_route": "DIRECT_PLAN",
            "reason_codes": ["email_self_contained"],
        },
    }
    case.update(overrides)
    return case


def test_real_fixture_loads_with_required_coverage() -> None:
    cases = loader.load_routing_labels()
    assert len(cases) >= 25

    actionabilities = {case.labels.actionability for case in cases}
    assert actionabilities == set(loader.Actionability)
    routes = {case.labels.expected_route for case in cases}
    assert routes == set(loader.ExpectedRoute)

    thread_groups: dict[str, int] = {}
    for case in cases:
        if case.thread_id:
            thread_groups[case.thread_id] = thread_groups.get(case.thread_id, 0) + 1
    assert sum(count >= 2 for count in thread_groups.values()) >= 2

    false_negative = [
        case
        for case in cases
        if case.labels.email_is_sufficient is False
        and case.labels.expected_route is loader.ExpectedRoute.RETRIEVE_RAG
        and case.labels.actionability is loader.Actionability.ACTION_REQUIRED
        and loader.ReasonCode.POLICY_REQUIRED in case.labels.reason_codes
    ]
    assert false_negative, "false-negative-retrieval risk case (PRD-v1 §14) missing"


def test_loader_rejects_missing_field(tmp_path: Path) -> None:
    case = _valid_case()
    del case["subject"]
    with pytest.raises(RoutingFixtureError, match="subject"):
        loader.load_routing_labels(_write(tmp_path, [case]))


def test_loader_rejects_unknown_enum_value(tmp_path: Path) -> None:
    case = _valid_case()
    labels = dict(case["labels"])  # type: ignore[arg-type]
    labels["expected_route"] = "SUMMARIZE"
    with pytest.raises(RoutingFixtureError, match="expected_route"):
        loader.load_routing_labels(_write(tmp_path, [dict(case, labels=labels)]))


def test_loader_rejects_empty_reason_codes(tmp_path: Path) -> None:
    case = _valid_case()
    labels = dict(case["labels"])  # type: ignore[arg-type]
    labels["reason_codes"] = []
    with pytest.raises(RoutingFixtureError, match="reason_codes"):
        loader.load_routing_labels(_write(tmp_path, [dict(case, labels=labels)]))


def test_loader_rejects_duplicate_ids(tmp_path: Path) -> None:
    with pytest.raises(RoutingFixtureError, match="duplicate"):
        loader.load_routing_labels(_write(tmp_path, [_valid_case(), _valid_case()]))


def test_loader_rejects_non_array_root(tmp_path: Path) -> None:
    with pytest.raises(RoutingFixtureError, match="JSON array"):
        loader.load_routing_labels(_write(tmp_path, {"id": "r-001"}))
