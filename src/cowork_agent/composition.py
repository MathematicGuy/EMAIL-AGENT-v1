"""Composition as a typed value (ADR-013, slice 02-1).

Everything the application is made of is constructed inside the ``lifespan``
closure in ``app.py`` and was published as ~60 untyped ``app.state`` attributes,
so every consumer defended with ``getattr(request.app.state, "x", None)`` plus a
``cast``. This module is the seam that replaces that publication model:
``CoworkRuntime`` is a frozen dataclass whose fields *are* the composed value,
published once as ``app.state.runtime`` and read back through ``runtime(request)``.

The accessor is a plain function, deliberately not a FastAPI ``Depends``: the
SSE chat path must pay zero dependency-injection overhead per request, and a
plain call keeps the depth here — one module owns the whole
``app.state.runtime`` contract.

This slice wires only the report store, the module that first needed a
composition root. Later slices grow the fields — ``control_plane``, ``mailbox``,
``chat``, ``email_rag``, ``evaluation`` — without changing this interface.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from starlette.requests import Request

from cowork_agent.domain.report_artifacts import ReportArtifactStore


@dataclass(frozen=True, slots=True)
class CoworkRuntime:
    """The composed value of the application: dependencies that outlive requests.

    Frozen so no code path can swap a field mid-flight, and typed so a missing
    dependency is a mypy error at the composition root instead of a ``None``
    found at request time. Later slices add the remaining groups
    (``control_plane``, ``mailbox``, ``chat``, ``email_rag``, ``evaluation``);
    each migration moves *where* a consumer reads from, never *what* is composed.
    """

    reports: ReportArtifactStore


def runtime(request: Request) -> CoworkRuntime:
    """The composed runtime behind a request: a typed read of ``app.state``."""
    return cast(CoworkRuntime, request.app.state.runtime)


__all__ = ["CoworkRuntime", "runtime"]
