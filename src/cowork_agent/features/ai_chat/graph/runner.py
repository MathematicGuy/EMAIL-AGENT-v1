"""The only module allowed to import LangGraph."""

from __future__ import annotations

from typing import Any, cast

from langgraph.graph import END, START, StateGraph

from .nodes import Node, mark_completed, retrieval_branch
from .state import ChatGraphState


def build_chat_graph(
    *,
    classify: Node,
    retrieve: Node,
    assemble: Node,
    generate_or_clarify: Node,
    persist: Node | None = None,
) -> object:
    graph = StateGraph(ChatGraphState)
    graph.add_node("classify", cast(Any, classify))
    graph.add_node("retrieve", cast(Any, retrieve))
    graph.add_node("assemble", cast(Any, assemble))
    graph.add_node("generate_or_clarify", cast(Any, generate_or_clarify))
    graph.add_node("persist", cast(Any, persist or mark_completed))
    graph.add_edge(START, "classify")
    graph.add_conditional_edges(
        "classify", retrieval_branch, {"retrieve": "retrieve", "assemble": "assemble"}
    )
    graph.add_edge("retrieve", "assemble")
    graph.add_edge("assemble", "generate_or_clarify")
    graph.add_edge("generate_or_clarify", "persist")
    graph.add_edge("persist", END)
    return graph.compile()
