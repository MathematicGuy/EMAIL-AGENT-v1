"""Strict loader for the metadata-safe V3-M4 chat routing fixture set."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from cowork_agent.domain.chat_contracts import ChatRoute


class ChatRoutingGroup(StrEnum):
    OBVIOUS_RAG = "obvious_rag"
    OBVIOUS_CHAT = "obvious_chat"
    AMBIGUOUS = "ambiguous"
    DISTRACTOR = "distractor"


@dataclass(frozen=True, slots=True)
class FixtureTurn:
    user: str
    assistant: str


@dataclass(frozen=True, slots=True)
class ChatRoutingLabels:
    expected_needs_rag: bool
    expected_needs_tool: bool
    expected_needs_clarification: bool
    expected_route: ChatRoute


@dataclass(frozen=True, slots=True)
class ChatRoutingCase:
    id: str
    group: ChatRoutingGroup
    current_message: str
    recent_turns: tuple[FixtureTurn, ...]
    ready_document_titles: tuple[str, ...]
    labels: ChatRoutingLabels


DEFAULT_FIXTURE_PATH = Path(__file__).with_name("chat_routing_labels.json")


def load_chat_routing_cases(
    path: Path | None = None,
) -> tuple[ChatRoutingCase, ...]:
    fixture_path = path or DEFAULT_FIXTURE_PATH
    try:
        raw = json.loads(fixture_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid chat routing fixture JSON: {error}") from error
    if not isinstance(raw, list):
        raise ValueError("chat routing fixture must be an array")
    cases = tuple(_parse_case(item, index) for index, item in enumerate(raw))
    if len(cases) < 60:
        raise ValueError("chat routing fixture requires at least 60 cases")
    ids = tuple(case.id for case in cases)
    if len(set(ids)) != len(ids):
        raise ValueError("chat routing fixture ids must be unique")
    counts = {group: sum(case.group is group for case in cases) for group in ChatRoutingGroup}
    if len(set(counts.values())) != 1:
        raise ValueError("chat routing fixture groups must be evenly balanced")
    return cases


def _parse_case(value: Any, index: int) -> ChatRoutingCase:
    where = f"case[{index}]"
    if not isinstance(value, dict):
        raise TypeError(f"{where} must be an object")
    expected = {
        "id",
        "group",
        "current_message",
        "recent_turns",
        "ready_document_titles",
        "labels",
    }
    if set(value) != expected:
        raise ValueError(f"{where} fields do not match the fixture schema")
    turns = value["recent_turns"]
    titles = value["ready_document_titles"]
    labels = value["labels"]
    if not isinstance(turns, list) or not isinstance(titles, list):
        raise TypeError(f"{where} turns and titles must be arrays")
    if not isinstance(labels, dict):
        raise TypeError(f"{where}.labels must be an object")
    return ChatRoutingCase(
        id=_text(value["id"], f"{where}.id"),
        group=ChatRoutingGroup(_text(value["group"], f"{where}.group")),
        current_message=_text(value["current_message"], f"{where}.current_message"),
        recent_turns=tuple(_parse_turn(turn, where) for turn in turns),
        ready_document_titles=tuple(_text(title, f"{where}.title") for title in titles),
        labels=_parse_labels(labels, where),
    )


def _parse_turn(value: Any, where: str) -> FixtureTurn:
    if not isinstance(value, dict) or set(value) != {"user", "assistant"}:
        raise ValueError(f"{where}.recent_turns item is invalid")
    return FixtureTurn(
        _text(value["user"], f"{where}.turn.user"),
        _text(value["assistant"], f"{where}.turn.assistant"),
    )


def _parse_labels(value: dict[str, Any], where: str) -> ChatRoutingLabels:
    expected = {
        "expected_needs_rag",
        "expected_needs_tool",
        "expected_needs_clarification",
        "expected_route",
    }
    if set(value) != expected:
        raise ValueError(f"{where}.labels fields do not match the schema")
    return ChatRoutingLabels(
        expected_needs_rag=_boolean(value["expected_needs_rag"], where),
        expected_needs_tool=_boolean(value["expected_needs_tool"], where),
        expected_needs_clarification=_boolean(value["expected_needs_clarification"], where),
        expected_route=ChatRoute(_text(value["expected_route"], where)),
    )


def _text(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where} must be a nonempty string")
    return value


def _boolean(value: Any, where: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{where} label must be a boolean")
    return value
