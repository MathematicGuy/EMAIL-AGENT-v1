"""Transport-only routes for the internal evaluation control plane.

This router owns the SPEC error envelope ``{"error": {"code", "message",
"details"?}}`` and parses submission bodies explicitly through
``EvaluationRequest.from_dict()`` so unrelated APIs keep their own validation
responses. Validation, scheduling, and business rules live in the
``batch_evaluation`` feature layer; these routes only translate between HTTP
and that service. Responses are recursively redacted and never embed private
artifacts, secrets, or absolute filesystem paths.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping, Sequence
from enum import Enum
from hmac import compare_digest
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from cowork_agent.composition import runtime
from cowork_agent.features.batch_evaluation.contracts import EvaluationRequest
from cowork_agent.features.batch_evaluation.service import (
    EvaluationConflict,
    EvaluationJobService,
    EvaluationResultConflict,
    EvaluationValidationError,
)

logger = logging.getLogger(__name__)

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_WINDOWS_PATH = re.compile(r"^[A-Za-z]:[\\/]")
_REDACTED = "[redacted]"
# Normalized key markers whose values must never reach an API response.
_PRIVATE_KEY_MARKERS = frozenset(
    {
        "authorization",
        "credential",
        "password",
        "secret",
        "token",
        "content",
        "prompt",
        "question",
        "reply",
        "answer",
        "transcript",
    }
)
_PRIVATE_KEY_COMPACTS = frozenset({"apikey", "accesstoken"})


def create_evaluation_router() -> APIRouter:
    """Build the bearer-protected evaluation routes over the composed runtime."""

    router = APIRouter(tags=["evaluation"])

    @router.post("/v1/evaluation-jobs")
    async def submit_evaluation_job(request: Request) -> JSONResponse:
        auth_error = _authorization_error(request)
        if auth_error is not None:
            return auth_error
        idempotency_key = request.headers.get("idempotency-key", "").strip()
        if not idempotency_key:
            return _error_response(
                422, "missing_idempotency_key", "Idempotency-Key header is required"
            )
        try:
            payload = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            return _error_response(422, "invalid_request", "request body must be valid JSON")
        try:
            evaluation_request = EvaluationRequest.from_dict(payload)
        except (KeyError, TypeError, ValueError):
            return _error_response(422, "invalid_request", "evaluation request failed validation")
        service = _evaluation_service(request)
        try:
            job = await service.submit(evaluation_request, idempotency_key=idempotency_key)
        except EvaluationValidationError as error:
            return _error_response(422, "invalid_request", str(error))
        except EvaluationConflict as error:
            return _error_response(422, "idempotency_conflict", str(error))
        except Exception:
            logger.warning("Evaluation job submission failed unexpectedly")
            return _error_response(500, "internal_error", "evaluation submission failed")
        return JSONResponse(
            status_code=202,
            content=_public_payload(
                {
                    "job_id": job.job_id,
                    "state": job.state.value,
                    "status_url": f"/v1/evaluation-jobs/{job.job_id}",
                    "result_url": f"/v1/evaluation-jobs/{job.job_id}/result",
                }
            ),
        )

    @router.get("/v1/evaluation-jobs/{job_id}")
    async def get_evaluation_job(request: Request, job_id: str) -> JSONResponse:
        auth_error = _authorization_error(request)
        if auth_error is not None:
            return auth_error
        if not _IDENTIFIER.fullmatch(job_id):
            return _unknown_job()
        service = _evaluation_service(request)
        try:
            status = await service.get_status(job_id)
        except KeyError:
            return _unknown_job()
        except Exception:
            logger.warning("Evaluation job status read failed unexpectedly")
            return _error_response(500, "internal_error", "evaluation status read failed")
        return JSONResponse(content=_public_payload(status))

    @router.get("/v1/evaluation-jobs/{job_id}/result")
    async def get_evaluation_result(request: Request, job_id: str) -> JSONResponse:
        auth_error = _authorization_error(request)
        if auth_error is not None:
            return auth_error
        if not _IDENTIFIER.fullmatch(job_id):
            return _unknown_job()
        service = _evaluation_service(request)
        try:
            manifest = await service.get_result(job_id)
        except EvaluationResultConflict as error:
            return _error_response(409, "result_not_available", str(error))
        except EvaluationConflict as error:
            return _error_response(409, "conflict", str(error))
        except KeyError:
            return _unknown_job()
        except Exception:
            logger.warning("Evaluation job result read failed unexpectedly")
            return _error_response(500, "internal_error", "evaluation result read failed")
        return JSONResponse(content=_public_payload(manifest))

    @router.post("/v1/evaluation-jobs/{job_id}/cancel")
    async def cancel_evaluation_job(request: Request, job_id: str) -> JSONResponse:
        auth_error = _authorization_error(request)
        if auth_error is not None:
            return auth_error
        if not _IDENTIFIER.fullmatch(job_id):
            return _unknown_job()
        service = _evaluation_service(request)
        try:
            status = await service.request_cancel(job_id)
        except EvaluationConflict as error:
            return _error_response(409, "conflict", str(error))
        except KeyError:
            return _unknown_job()
        except Exception:
            logger.warning("Evaluation job cancellation failed unexpectedly")
            return _error_response(500, "internal_error", "evaluation cancellation failed")
        return JSONResponse(status_code=202, content=_public_payload(status))

    @router.get("/v1/evaluation-types")
    async def list_evaluation_types(request: Request) -> JSONResponse:
        auth_error = _authorization_error(request)
        if auth_error is not None:
            return auth_error
        service = _evaluation_service(request)
        try:
            types = await service.list_types()
        except Exception:
            logger.warning("Evaluation type listing failed unexpectedly")
            return _error_response(500, "internal_error", "evaluation type listing failed")
        return JSONResponse(content=_public_payload({"types": list(types)}))

    return router


def _evaluation_service(request: Request) -> EvaluationJobService:
    # Typed read through the runtime seam (ADR-013): the router is mounted
    # only when the evaluation group is composed, but defend the interface
    # with an explicit failure instead of an attribute crash deep in a route.
    bundle = runtime(request).evaluation
    if bundle is None:
        raise RuntimeError("the evaluation group is not composed")
    return bundle.service


def _authorization_error(request: Request) -> JSONResponse | None:
    """Return the SPEC auth errors without ever echoing the configured token."""

    # An absent evaluation group degrades to an empty expected token exactly
    # as the old missing-key getattr did: the comparison fails closed (403).
    bundle = runtime(request).evaluation
    expected = bundle.api_token if bundle is not None else ""
    header = request.headers.get("authorization", "")
    scheme, _, presented = header.partition(" ")
    presented = presented.strip()
    if not header or scheme.lower() != "bearer" or not presented:
        return _error_response(401, "unauthenticated", "a bearer token is required")
    if not compare_digest(presented.encode("utf-8"), expected.encode("utf-8")):
        return _error_response(403, "forbidden", "the provided bearer token is not valid")
    return None


def _unknown_job() -> JSONResponse:
    return _error_response(404, "not_found", "evaluation job is unknown")


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    """Emit the one safe error envelope every evaluation route shares."""

    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )


def _public_payload(value: object) -> Any:
    """Convert one service view into response-ready, recursively redacted JSON."""

    return _redact_private(_jsonable(value))


def _jsonable(value: object) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, str | bytes | bytearray) or not isinstance(value, Sequence):
        return value
    return [_jsonable(item) for item in value]


def _redact_private(value: Any) -> Any:
    """Recursively strip private keys, values, and absolute paths from responses."""

    if isinstance(value, dict):
        return {
            key: _REDACTED if _is_private_key(key) else _redact_private(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_private(item) for item in value]
    if isinstance(value, str) and _looks_like_absolute_path(value):
        return _REDACTED
    return value


def _looks_like_absolute_path(value: str) -> bool:
    """Redact every absolute path except the public /v1/ API URLs.

    An allowlist is used because operator-configured artifact roots can
    live under any filesystem root, so no root-segment denylist is safe.
    """

    if value.startswith("\\\\") or _WINDOWS_PATH.match(value):
        return True
    return value.startswith("/") and not value.startswith("/v1/")


def _is_private_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")
    parts = frozenset(part for part in normalized.split("_") if part)
    compact = normalized.replace("_", "")
    return any(marker in compact for marker in _PRIVATE_KEY_COMPACTS) or bool(
        parts & _PRIVATE_KEY_MARKERS
    )
