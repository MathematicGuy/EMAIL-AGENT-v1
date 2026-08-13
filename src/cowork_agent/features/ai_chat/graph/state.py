"""Lean graph state: identifiers and routing scalars only."""

from __future__ import annotations

from typing import TypedDict


class ChatGraphState(TypedDict, total=False):
    tenant_id: str
    user_id: str
    project_id: str
    session_id: str
    turn_id: str
    user_message: str
    document_ids: list[str]
    route: str
    retrieval_query: str
    citation_ids: list[str]
    degraded: bool
    completed: bool
