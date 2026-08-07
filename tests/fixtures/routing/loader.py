"""Typed loader for the labeled routing fixture set (PRD-v1 §14, §16 M2).

Loaded by the routing evaluation harness (V1-M2) and the baseline-capture
script (P0-C). Schema is documented in README.md next to this file.
"""

import json
from dataclasses import dataclass
from enum import Enum, StrEnum
from pathlib import Path
from typing import Any, TypeVar

TEnum = TypeVar("TEnum", bound=Enum)


class Actionability(StrEnum):
    ACTION_REQUIRED = "action_required"
    ACTION_SUGGESTED = "action_suggested"
    INFORMATIONAL = "informational"
    UNCLEAR = "unclear"
    IRRELEVANT = "irrelevant"


class ExpectedRoute(StrEnum):
    NO_ACTION = "NO_ACTION"
    DIRECT_PLAN = "DIRECT_PLAN"
    RETRIEVE_RAG = "RETRIEVE_RAG"


class ReasonCode(StrEnum):
    NO_ACTION = "no_action"
    EMAIL_SELF_CONTAINED = "email_self_contained"
    COMPANY_PROCEDURE_REQUIRED = "company_procedure_required"
    GOVERNANCE_REQUIRED = "governance_required"
    POLICY_REQUIRED = "policy_required"
    TEMPLATE_REQUIRED = "template_required"
    INTERNAL_TERM_UNRESOLVED = "internal_term_unresolved"
    DOMAIN_KNOWLEDGE_REQUIRED = "domain_knowledge_required"


class RoutingFixtureError(ValueError):
    """Raised when the fixture JSON violates the documented schema."""


@dataclass(frozen=True)
class RoutingLabels:
    actionability: Actionability
    email_is_sufficient: bool
    expected_route: ExpectedRoute
    reason_codes: tuple[ReasonCode, ...]


@dataclass(frozen=True)
class RoutingCase:
    id: str
    subject: str
    sender: str
    body: str
    thread_id: str | None
    labels: RoutingLabels


DEFAULT_FIXTURE_PATH = Path(__file__).with_name("routing_labels.json")


def load_routing_labels(path: Path | None = None) -> tuple[RoutingCase, ...]:
    fixture_path = path or DEFAULT_FIXTURE_PATH
    try:
        raw = json.loads(fixture_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RoutingFixtureError(f"{fixture_path}: invalid JSON: {exc}") from exc
    if not isinstance(raw, list):
        raise RoutingFixtureError(f"{fixture_path}: top level must be a JSON array")
    cases = [_parse_case(entry, index, fixture_path) for index, entry in enumerate(raw)]
    ids = [case.id for case in cases]
    duplicates = {case_id for case_id in ids if ids.count(case_id) > 1}
    if duplicates:
        raise RoutingFixtureError(f"{fixture_path}: duplicate case ids: {sorted(duplicates)}")
    return tuple(cases)


def _parse_case(entry: Any, index: int, source: Path) -> RoutingCase:
    where = f"{source}[{index}]"
    if not isinstance(entry, dict):
        raise RoutingFixtureError(f"{where}: case must be an object")
    case_id = _require_str(entry, "id", where)
    labels_raw = entry.get("labels")
    if not isinstance(labels_raw, dict):
        raise RoutingFixtureError(f"{where}: missing object field 'labels'")
    return RoutingCase(
        id=case_id,
        subject=_require_str(entry, "subject", where),
        sender=_require_str(entry, "sender", where),
        body=_require_str(entry, "body", where),
        thread_id=_optional_str(entry, "thread_id", where),
        labels=_parse_labels(labels_raw, where),
    )


def _parse_labels(entry: dict[str, Any], where: str) -> RoutingLabels:
    actionability = _enum(entry, "actionability", Actionability, where)
    sufficient = entry.get("email_is_sufficient")
    if not isinstance(sufficient, bool):
        raise RoutingFixtureError(f"{where}: 'labels.email_is_sufficient' must be a boolean")
    expected_route = _enum(entry, "expected_route", ExpectedRoute, where)
    codes_raw = entry.get("reason_codes")
    if not isinstance(codes_raw, list) or not codes_raw:
        raise RoutingFixtureError(f"{where}: 'labels.reason_codes' must be a non-empty array")
    codes = tuple(_enum_value(code, ReasonCode, f"{where}.reason_codes") for code in codes_raw)
    return RoutingLabels(actionability, sufficient, expected_route, codes)


def _require_str(entry: dict[str, Any], field: str, where: str) -> str:
    value = entry.get(field)
    if not isinstance(value, str) or not value:
        raise RoutingFixtureError(f"{where}: missing non-empty string field '{field}'")
    return value


def _optional_str(entry: dict[str, Any], field: str, where: str) -> str | None:
    if field not in entry:
        raise RoutingFixtureError(f"{where}: missing field '{field}'")
    value = entry[field]
    if value is not None and not isinstance(value, str):
        raise RoutingFixtureError(f"{where}: field '{field}' must be a string or null")
    return value


def _enum(entry: dict[str, Any], field: str, enum_type: type[TEnum], where: str) -> TEnum:
    return _enum_value(entry.get(field), enum_type, f"{where}.labels.{field}")


def _enum_value(value: Any, enum_type: type[TEnum], where: str) -> TEnum:
    try:
        return enum_type(value)
    except (ValueError, TypeError):
        allowed = ", ".join(item.value for item in enum_type)
        raise RoutingFixtureError(f"{where}: unknown value {value!r}; allowed: {allowed}") from None
