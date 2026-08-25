"""Unit tests for the composition seam (ADR-013, slice 02-1).

The invariant owned here: ``runtime(request)`` is a typed read of
``app.state.runtime`` — it returns the exact composed value, nothing else.
The report filename rule and store containment are owned by
``unit/domain/test_report_artifacts.py`` and
``unit/persistence/test_report_artifact_store.py`` and are not re-asserted.
"""

from fastapi import FastAPI
from starlette.requests import Request

from cowork_agent.composition import CoworkRuntime, runtime
from cowork_agent.persistence.report_artifacts import InMemoryReportArtifactStore


def _request_with_runtime(composed: CoworkRuntime) -> Request:
    app = FastAPI()
    app.state.runtime = composed
    return Request({"type": "http", "app": app, "headers": []})


def test_runtime_returns_the_exact_composed_value_behind_the_request() -> None:
    composed = CoworkRuntime(reports=InMemoryReportArtifactStore())

    assert runtime(_request_with_runtime(composed)) is composed


def test_runtime_exposes_the_composed_report_store() -> None:
    store = InMemoryReportArtifactStore()
    composed = CoworkRuntime(reports=store)

    assert runtime(_request_with_runtime(composed)).reports is store
