"""Classifier-gated, framework-free routing for AI Chat turns."""

from .prompt import INTENT_PROMPT_VERSION, build_intent_prompt
from .resolver import finalize_route, resolve_route
from .service import (
    ChatRoutingService,
    EmptyReadyDocumentCatalog,
    IntentClassifierError,
    IntentClassifierInvalidOutput,
    IntentClassifierTimeout,
    IntentClassifierUnavailable,
)

__all__ = [
    "INTENT_PROMPT_VERSION",
    "ChatRoutingService",
    "EmptyReadyDocumentCatalog",
    "IntentClassifierError",
    "IntentClassifierInvalidOutput",
    "IntentClassifierTimeout",
    "IntentClassifierUnavailable",
    "build_intent_prompt",
    "finalize_route",
    "resolve_route",
]
