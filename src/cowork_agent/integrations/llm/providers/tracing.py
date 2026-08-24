"""Langfuse helpers shared by Email Action Plan LLM providers."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping

from langfuse import get_client

_CLASSIFIER_LOGGER = logging.getLogger(__name__)


def _langfuse_configured() -> bool:
    tracing_enabled = os.getenv("LANGFUSE_TRACING_ENABLED", "true").strip().lower()
    return bool(
        tracing_enabled not in {"0", "false", "no", "off"}
        and os.getenv("LANGFUSE_PUBLIC_KEY", "").strip()
        and os.getenv("LANGFUSE_SECRET_KEY", "").strip()
    )


def _update_current_generation(
    *,
    input_data: Mapping[str, object] | None = None,
    output_data: Mapping[str, object] | None = None,
    metadata: Mapping[str, object] | None = None,
    model: str | None = None,
    usage_details: Mapping[str, int] | None = None,
) -> None:
    """Update Langfuse with metadata-only, non-email classifier telemetry."""
    if not _langfuse_configured():
        return
    try:
        get_client().update_current_generation(
            input=dict(input_data) if input_data is not None else None,
            output=dict(output_data) if output_data is not None else None,
            metadata=dict(metadata) if metadata is not None else None,
            model=model,
            usage_details=dict(usage_details) if usage_details is not None else None,
        )
    except Exception as exc:  # pragma: no cover - telemetry must never break routing
        _CLASSIFIER_LOGGER.debug("Langfuse generation update failed: %s", type(exc).__name__)


def _update_current_span(
    *,
    input_data: Mapping[str, object] | None = None,
    output_data: Mapping[str, object] | None = None,
    metadata: Mapping[str, object] | None = None,
) -> None:
    """Update the classifier span with metadata-only telemetry."""
    if not _langfuse_configured():
        return
    try:
        get_client().update_current_span(
            input=dict(input_data) if input_data is not None else None,
            output=dict(output_data) if output_data is not None else None,
            metadata=dict(metadata) if metadata is not None else None,
        )
    except Exception as exc:  # pragma: no cover - telemetry must never break routing
        _CLASSIFIER_LOGGER.debug("Langfuse span update failed: %s", type(exc).__name__)
