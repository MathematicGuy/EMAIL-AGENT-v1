"""API contract tests for the internal evaluation job routes (no network)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI

import cowork_agent.api.evaluation_jobs as evaluation_jobs_api
from cowork_agent.api.evaluation_jobs import create_evaluation_router
from cowork_agent.app import create_app
from cowork_agent.config import GMAIL_READONLY_SCOPE
from cowork_agent.features.batch_evaluation.artifacts import FilesystemEvaluationArtifactStore
from cowork_agent.features.batch_evaluation.contracts import (
    ArtifactBundle,
    CleanupOutcome,
    EvaluationRequest,
    ExecutionMode,
    FailureClass,
    FailureClassification,
    PluginPlan,
    ProviderAttemptEvent,
    UnitState,
    WorkContext,
    WorkUnit,
    WorkUnitOutcome,
)
from cowork_agent.features.batch_evaluation.credentials import CredentialLeasingPool
from cowork_agent.features.batch_evaluation.registry import PluginRegistry
from cowork_agent.features.batch_evaluation.runner import EvaluationJobRunner
from cowork_agent.features.batch_evaluation.service import EvaluationJobService
from cowork_agent.features.batch_evaluation.supervisor import EvaluationSupervisor
from cowork_agent.persistence.repositories.evaluation_jobs import SQLiteEvaluationJobRepository

TOKEN = "test-evaluation-token-with-32-characters"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
TERMINAL_STATES = frozenset({"succeeded", "partially_succeeded", "failed", "cancelled"})
CANONICAL_ORDER = (
    "accepted",
    "validating",
    "queued",
    "running",
    "collecting",
    "succeeded",
)


class FakeReplyFactory:
    """Ordinary-completion stand-in that never touches a provider network."""

    max_output_tokens = 10

    def __init__(self) -> None:
        self.provider_calls = 0

    def bind(self, lease: object, model: str, attempt_sink: object) -> object:
        del model
        factory = self
        alias = lease.alias

        class Reply:
            def stream_reply(self, request: object, context: object) -> AsyncIterator[str]:
                del self, request, context

                async def stream() -> AsyncIterator[str]:
                    factory.provider_calls += 1
                    observed = attempt_sink(  # type: ignore[operator]
                        ProviderAttemptEvent(
                            credential_alias=alias,
                            request_attempt_id=f"provider-{factory.provider_calls}",
                            outcome="succeeded",
                            status_code=None,
                            retry_after_seconds=None,
                            latency_ms=0,
                        )
                    )
                    if observed is not None:
                        await observed
                    yield "private reply"

                return stream()

        return Reply()


class FakePlugin:
    evaluation_type = "fake-eval"
    version = "1"
    supported_modes = frozenset({ExecutionMode.REQUEST_BATCH})
    parameter_schema: Mapping[str, object] = {"type": "object"}

    def __init__(self, *, work_count: int = 2, block: asyncio.Event | None = None) -> None:
        self.work_count = work_count
        self.block = block
        self.provider_calls = 0

    async def preflight(self, request: EvaluationRequest) -> PluginPlan:
        if request.target_model != "small-model":
            raise ValueError("private preflight detail")
        return PluginPlan(request.dataset_ref, self.work_count, object())

    def build_work_units(self, plan: PluginPlan, lane_count: int) -> tuple[WorkUnit, ...]:
        del lane_count
        return tuple(
            WorkUnit(
                unit_id=f"unit-{ordinal}",
                ordinal=ordinal,
                payload={"case_id": f"case-{ordinal}"},
            )
            for ordinal in range(plan.ready_work)
        )

    async def execute_work(self, unit: WorkUnit, context: WorkContext) -> WorkUnitOutcome:
        self.provider_calls += 1
        chunks = [chunk async for chunk in context.provider_client.stream_reply(object(), object())]
        assert chunks == ["private reply"]
        if self.block is not None:
            await self.block.wait()
        return WorkUnitOutcome(
            unit_id=unit.unit_id,
            ordinal=unit.ordinal,
            state=UnitState.SUCCEEDED,
            provider_requests=1,
            total_tokens=1,
            private_result={"reply": "private reply"},
        )

    def aggregate(
        self, plan: PluginPlan, outcomes: Sequence[WorkUnitOutcome]
    ) -> ArtifactBundle:
        del plan
        return ArtifactBundle(
            public_result={"completed_units": len(outcomes)},
            private_artifact_ids=(),
        )

    async def cleanup(self, context: WorkContext) -> CleanupOutcome:
        del context
        return CleanupOutcome(removed_resources=0, warnings=())

    def classify_failure(self, error: BaseException) -> FailureClassification:
        del error
        return FailureClassification(
            failure_class=FailureClass.EVALUATION, retryable=False, credential_state=None
        )


@dataclass
class Harness:
    plugin: FakePlugin
    factory: FakeReplyFactory
    service: EvaluationJobService
    repository: SQLiteEvaluationJobRepository
    supervisor: EvaluationSupervisor


async def build_harness(
    tmp_path: Path,
    *,
    work_count: int = 2,
    key_count: int = 3,
    block: asyncio.Event | None = None,
) -> Harness:
    plugin = FakePlugin(work_count=work_count, block=block)
    factory = FakeReplyFactory()
    repository = SQLiteEvaluationJobRepository(tmp_path / "evaluation-jobs.db")
    await repository.initialize()
    registry = PluginRegistry()
    registry.register(plugin)
    keys = {"MISTRAL_API_KEY": "secret-one"}
    keys.update({f"MISTRAL_API_KEY{index}": f"secret-{index}" for index in range(2, key_count + 1)})
    pool = CredentialLeasingPool.from_env("MISTRAL_API_KEY", keys)
    artifacts = FilesystemEvaluationArtifactStore(tmp_path / "artifacts")
    runner = EvaluationJobRunner(
        registry=registry,
        repository=repository,
        credential_pool=pool,
        artifact_store=artifacts,
        scratch_root=tmp_path / "scratch",
        reply_factory=factory,
    )
    supervisor = EvaluationSupervisor(repository=repository, runner=runner)
    service = EvaluationJobService(
        registry=registry,
        repository=repository,
        credential_pool=pool,
        artifact_store=artifacts,
        supervisor=supervisor,
    )
    return Harness(plugin, factory, service, repository, supervisor)


def build_app(harness: Harness) -> FastAPI:
    app = FastAPI()
    app.include_router(create_evaluation_router())
    app.state.evaluation_service = harness.service
    app.state.evaluation_api_token = TOKEN
    return app


def submission_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "evaluation_type": "fake-eval",
        "provider": "mistral",
        "target_model": "small-model",
        "dataset_ref": "dataset-v1",
        "credential_pool": "mistral-eval",
        "execution_mode": "request_batch",
        "execution_options": {},
        "budget": {"max_provider_requests": 20, "max_total_tokens": 2000},
        "parameters": {},
    }
    body.update(overrides)
    return body


def assert_safe_error(response: httpx.Response) -> dict[str, object]:
    body = response.json()
    assert set(body) == {"error"}
    error = body["error"]
    assert isinstance(error, dict)
    assert isinstance(error.get("code"), str) and error["code"]
    assert isinstance(error.get("message"), str) and error["message"]
    if "details" in error:
        assert isinstance(error["details"], dict)
    return error


async def wait_until_terminal(
    client: httpx.AsyncClient, job_id: str, *, timeout: float = 10.0
) -> dict[str, object]:
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        response = await client.get(f"/v1/evaluation-jobs/{job_id}", headers=AUTH)
        assert response.status_code == 200
        status = response.json()
        if status["state"] in TERMINAL_STATES:
            return status
        assert asyncio.get_event_loop().time() < deadline, f"job stayed in {status['state']}"
        await asyncio.sleep(0.02)


def test_missing_and_wrong_bearer_tokens_use_safe_401_and_403(tmp_path: Path) -> None:
    async def scenario() -> None:
        harness = await build_harness(tmp_path)
        app = build_app(harness)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            no_header = await client.get("/v1/evaluation-types")
            wrong_scheme = await client.get(
                "/v1/evaluation-types", headers={"Authorization": f"Basic {TOKEN}"}
            )
            wrong_token = await client.get(
                "/v1/evaluation-types",
                headers={"Authorization": "Bearer wrong-token-value-with-32-characters"},
            )
            missing_on_every_route = [
                await client.get("/v1/evaluation-jobs/any-job"),
                await client.get("/v1/evaluation-jobs/any-job/result"),
                await client.post("/v1/evaluation-jobs/any-job/cancel"),
                await client.post("/v1/evaluation-jobs", json=submission_body()),
            ]

        for response in (no_header, wrong_scheme, *missing_on_every_route):
            assert response.status_code == 401
            assert assert_safe_error(response)["code"] == "unauthenticated"
        assert wrong_token.status_code == 403
        assert assert_safe_error(wrong_token)["code"] == "forbidden"
        assert TOKEN not in wrong_token.text
        await harness.supervisor.close()

    asyncio.run(scenario())


def test_bearer_token_is_compared_in_constant_time(tmp_path: Path) -> None:
    import hmac

    real_compare = hmac.compare_digest
    calls: list[tuple[bytes, bytes]] = []

    def spy(left: object, right: object) -> bool:
        calls.append((bytes(left), bytes(right)))  # type: ignore[arg-type]
        return real_compare(left, right)  # type: ignore[arg-type]

    async def scenario() -> None:
        harness = await build_harness(tmp_path)
        app = build_app(harness)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            wrong = await client.get(
                "/v1/evaluation-types",
                headers={"Authorization": "Bearer another-wrong-token-32-characters"},
            )
            right = await client.get("/v1/evaluation-types", headers=AUTH)

        assert wrong.status_code == 403
        assert right.status_code == 200
        await harness.supervisor.close()

    original = evaluation_jobs_api.compare_digest
    evaluation_jobs_api.compare_digest = spy  # type: ignore[assignment]
    try:
        asyncio.run(scenario())
    finally:
        evaluation_jobs_api.compare_digest = original  # type: ignore[assignment]
    assert calls, "token comparison must go through the constant-time primitive"
    # The constant-time primitive must be invoked with the configured secret as
    # an operand, proving the presented token is compared against the real token
    # (never a naive short-circuiting equality check).
    assert any(TOKEN.encode() in call for call in calls)


def test_submission_returns_202_urls_and_terminal_result(tmp_path: Path) -> None:
    async def scenario() -> None:
        harness = await build_harness(tmp_path)
        app = build_app(harness)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            submitted = await client.post(
                "/v1/evaluation-jobs",
                json=submission_body(),
                headers={**AUTH, "Idempotency-Key": "submit-1"},
            )
            assert submitted.status_code == 202
            payload = submitted.json()
            job_id = payload["job_id"]
            assert payload["status_url"] == f"/v1/evaluation-jobs/{job_id}"
            assert payload["result_url"] == f"/v1/evaluation-jobs/{job_id}/result"

            status = await wait_until_terminal(client, job_id)
            assert status["state"] == "succeeded"
            assert status["progress"]["succeeded"] == 2

            result = await client.get(payload["result_url"], headers=AUTH)
            assert result.status_code == 200
            manifest = result.json()
            assert manifest["completed_units"] == 2

        for response in (submitted, result):
            assert "private reply" not in response.text
        assert "secret-one" not in status and "secret-one" not in manifest
        assert str(tmp_path) not in submitted.text
        assert str(tmp_path) not in result.text
        await harness.supervisor.close()

    asyncio.run(scenario())


def test_idempotent_replay_and_request_hash_conflict(tmp_path: Path) -> None:
    async def scenario() -> None:
        harness = await build_harness(tmp_path)
        app = build_app(harness)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            first = await client.post(
                "/v1/evaluation-jobs",
                json=submission_body(),
                headers={**AUTH, "Idempotency-Key": "replay-key"},
            )
            replay = await client.post(
                "/v1/evaluation-jobs",
                json=submission_body(),
                headers={**AUTH, "Idempotency-Key": "replay-key"},
            )
            conflict = await client.post(
                "/v1/evaluation-jobs",
                json=submission_body(execution_options={"max_workers": 2}),
                headers={**AUTH, "Idempotency-Key": "replay-key"},
            )
            missing_key = await client.post(
                "/v1/evaluation-jobs", json=submission_body(), headers=AUTH
            )
            await wait_until_terminal(client, first.json()["job_id"])

        assert first.status_code == 202
        assert replay.status_code == 202
        assert replay.json()["job_id"] == first.json()["job_id"]
        assert conflict.status_code == 422
        assert assert_safe_error(conflict)["code"] == "idempotency_conflict"
        assert missing_key.status_code == 422
        assert assert_safe_error(missing_key)["code"] == "missing_idempotency_key"
        assert harness.plugin.provider_calls == 2
        await harness.supervisor.close()

    asyncio.run(scenario())


def test_validation_rejects_bad_requests_before_creation_or_spend(tmp_path: Path) -> None:
    async def scenario() -> None:
        harness = await build_harness(tmp_path)
        app = build_app(harness)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            zero_workers = await client.post(
                "/v1/evaluation-jobs",
                json=submission_body(execution_options={"max_workers": 0}),
                headers={**AUTH, "Idempotency-Key": "invalid-workers"},
            )
            unknown_type = await client.post(
                "/v1/evaluation-jobs",
                json=submission_body(evaluation_type="unknown-eval"),
                headers={**AUTH, "Idempotency-Key": "invalid-type"},
            )
            unsupported_mode = await client.post(
                "/v1/evaluation-jobs",
                json=submission_body(execution_mode="workflow_shards"),
                headers={**AUTH, "Idempotency-Key": "invalid-mode"},
            )
            extra_key = await client.post(
                "/v1/evaluation-jobs",
                json={**submission_body(), "unexpected": True},
                headers={**AUTH, "Idempotency-Key": "invalid-keys"},
            )
            secret_shaped = await client.post(
                "/v1/evaluation-jobs",
                json=submission_body(parameters={"api_key": "leaked-secret"}),
                headers={**AUTH, "Idempotency-Key": "invalid-parameters"},
            )
            malformed = await client.post(
                "/v1/evaluation-jobs",
                content=b"{not json",
                headers={**AUTH, "Idempotency-Key": "invalid-json"},
            )

        for response in (
            zero_workers,
            unknown_type,
            unsupported_mode,
            extra_key,
            secret_shaped,
            malformed,
        ):
            assert response.status_code == 422
            assert assert_safe_error(response)["code"] == "invalid_request"
            assert "leaked-secret" not in response.text
            assert "private preflight detail" not in response.text
        assert harness.plugin.provider_calls == 0
        assert await harness.repository.list_recoverable_jobs() == ()
        await harness.supervisor.close()

    asyncio.run(scenario())


def test_status_progression_follows_the_canonical_order(tmp_path: Path) -> None:
    async def scenario() -> None:
        harness = await build_harness(tmp_path, work_count=3)
        app = build_app(harness)
        transport = httpx.ASGITransport(app=app)
        observed: list[str] = []
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            submitted = await client.post(
                "/v1/evaluation-jobs",
                json=submission_body(),
                headers={**AUTH, "Idempotency-Key": "progression"},
            )
            job_id = submitted.json()["job_id"]
            deadline = asyncio.get_event_loop().time() + 10.0
            while True:
                response = await client.get(f"/v1/evaluation-jobs/{job_id}", headers=AUTH)
                assert response.status_code == 200
                state = response.json()["state"]
                if not observed or observed[-1] != state:
                    observed.append(state)
                if state in TERMINAL_STATES:
                    break
                assert asyncio.get_event_loop().time() < deadline
                await asyncio.sleep(0.01)

        positions = [CANONICAL_ORDER.index(state) for state in observed]
        assert positions == sorted(positions), observed
        assert observed[-1] == "succeeded"
        assert observed[0] in {"accepted", "validating", "queued", "running"}
        await harness.supervisor.close()

    asyncio.run(scenario())


def test_result_conflicts_while_running_and_recovers_after_release(tmp_path: Path) -> None:
    async def scenario() -> None:
        block = asyncio.Event()
        harness = await build_harness(tmp_path, work_count=1, block=block)
        app = build_app(harness)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            submitted = await client.post(
                "/v1/evaluation-jobs",
                json=submission_body(),
                headers={**AUTH, "Idempotency-Key": "conflict-key"},
            )
            payload = submitted.json()
            deadline = asyncio.get_event_loop().time() + 10.0
            while True:
                status = (
                    await client.get(payload["status_url"], headers=AUTH)
                ).json()
                if status["progress"]["running"] >= 1:
                    break
                assert asyncio.get_event_loop().time() < deadline
                await asyncio.sleep(0.01)
            conflict = await client.get(payload["result_url"], headers=AUTH)
            block.set()
            final = await wait_until_terminal(client, payload["job_id"])
            released = await client.get(payload["result_url"], headers=AUTH)

        assert conflict.status_code == 409
        assert assert_safe_error(conflict)["code"] == "result_not_available"
        assert final["state"] == "succeeded"
        assert released.status_code == 200
        await harness.supervisor.close()

    asyncio.run(scenario())


def test_unknown_jobs_return_safe_404_on_every_route(tmp_path: Path) -> None:
    async def scenario() -> None:
        harness = await build_harness(tmp_path)
        app = build_app(harness)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            status = await client.get("/v1/evaluation-jobs/no-such-job", headers=AUTH)
            result = await client.get(
                "/v1/evaluation-jobs/no-such-job/result", headers=AUTH
            )
            cancel = await client.post(
                "/v1/evaluation-jobs/no-such-job/cancel", headers=AUTH
            )

        for response in (status, result, cancel):
            assert response.status_code == 404
            assert assert_safe_error(response)["code"] == "not_found"
        await harness.supervisor.close()

    asyncio.run(scenario())


def test_cancellation_is_accepted_idempotently_and_is_terminal(tmp_path: Path) -> None:
    async def scenario() -> None:
        block = asyncio.Event()
        harness = await build_harness(tmp_path, work_count=1, block=block)
        app = build_app(harness)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            submitted = await client.post(
                "/v1/evaluation-jobs",
                json=submission_body(),
                headers={**AUTH, "Idempotency-Key": "cancel-key"},
            )
            payload = submitted.json()
            deadline = asyncio.get_event_loop().time() + 10.0
            while True:
                status = (
                    await client.get(payload["status_url"], headers=AUTH)
                ).json()
                if status["progress"]["running"] >= 1:
                    break
                assert asyncio.get_event_loop().time() < deadline
                await asyncio.sleep(0.01)
            first = await client.post(
                f"/v1/evaluation-jobs/{payload['job_id']}/cancel", headers=AUTH
            )
            replay = await client.post(
                f"/v1/evaluation-jobs/{payload['job_id']}/cancel", headers=AUTH
            )
            final = await wait_until_terminal(client, payload["job_id"])
            late = await client.post(
                f"/v1/evaluation-jobs/{payload['job_id']}/cancel", headers=AUTH
            )

        assert first.status_code == 202
        assert replay.status_code == 202
        assert late.status_code == 202
        assert final["state"] == "cancelled"
        assert final["cancel_requested"] is True
        assert final["progress"]["succeeded"] == 0
        block.set()
        await harness.supervisor.close()

    asyncio.run(scenario())


def test_type_listing_exposes_only_safe_static_metadata(tmp_path: Path) -> None:
    async def scenario() -> None:
        harness = await build_harness(tmp_path)
        app = build_app(harness)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/v1/evaluation-types", headers=AUTH)

        assert response.status_code == 200
        assert response.json() == {
            "types": [
                {
                    "type": "fake-eval",
                    "version": "1",
                    "modes": ["request_batch"],
                    "parameter_schema": {"type": "object"},
                }
            ]
        }
        await harness.supervisor.close()

    asyncio.run(scenario())


def test_max_worker_resolution_cases(tmp_path: Path) -> None:
    async def run_case(
        root: Path, key: str, max_workers: int | None
    ) -> dict[str, object]:
        # One fresh credential pool per case keeps healthy_count deterministic:
        # resolution reads currently-available credentials, and concurrently
        # executing jobs lease them, so sharing a pool across jobs races.
        harness = await build_harness(root, work_count=4)
        app = build_app(harness)
        transport = httpx.ASGITransport(app=app)
        execution_options = {} if max_workers is None else {"max_workers": max_workers}
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/v1/evaluation-jobs",
                json=submission_body(execution_options=execution_options),
                headers={**AUTH, "Idempotency-Key": key},
            )
            assert response.status_code == 202
            status = await wait_until_terminal(client, response.json()["job_id"])
        await harness.supervisor.close()
        return status

    async def scenario() -> None:
        default_status = await run_case(tmp_path / "default", "workers-default", None)
        under_status = await run_case(tmp_path / "under", "workers-two", 2)
        over_status = await run_case(tmp_path / "over", "workers-four", 4)

        assert default_status["requested_workers"] == 1
        assert default_status["effective_workers"] == 1
        assert default_status["warnings"] == []

        assert under_status["requested_workers"] == 2
        assert under_status["effective_workers"] == 2
        assert under_status["warnings"] == []

        assert over_status["requested_workers"] == 4
        assert over_status["effective_workers"] == 3
        assert over_status["warnings"] == [
            {
                "code": "WORKER_COUNT_REDUCED",
                "message": "Worker count was reduced because fewer credentials are healthy.",
                "details": {
                    "requested_workers": 4,
                    "effective_workers": 3,
                    "healthy_credentials": 3,
                },
            }
        ]

    asyncio.run(scenario())


def test_responses_never_leak_content_or_secrets_recursively(tmp_path: Path) -> None:
    nested = {
        "safe": 1,
        "outer_token": "secret-a",
        "nested": {
            "password": "secret-b",
            "deeper": [
                {"prompt": "private prompt", "list_reply": ["private reply"]},
                "/absolute/private/path.json",
            ],
        },
        "authorization": "secret-c",
    }

    redacted = evaluation_jobs_api._redact_private(nested)  # noqa: SLF001

    serialized = repr(redacted)
    for private in ("secret-a", "secret-b", "secret-c", "private prompt", "private reply"):
        assert private not in serialized
    assert "/absolute/private/path.json" not in serialized
    assert redacted["safe"] == 1

    async def scenario() -> None:
        harness = await build_harness(tmp_path)
        app = build_app(harness)
        transport = httpx.ASGITransport(app=app)
        responses: list[httpx.Response] = []
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            submitted = await client.post(
                "/v1/evaluation-jobs",
                json=submission_body(),
                headers={**AUTH, "Idempotency-Key": "redaction-key"},
            )
            responses.append(submitted)
            job_id = submitted.json()["job_id"]
            final = await wait_until_terminal(client, job_id)
            responses.append(await client.get(f"/v1/evaluation-jobs/{job_id}", headers=AUTH))
            responses.append(await client.get(f"/v1/evaluation-jobs/{job_id}/result", headers=AUTH))
            responses.append(await client.get("/v1/evaluation-types", headers=AUTH))
            assert final["state"] == "succeeded"

        for response in responses:
            assert "private reply" not in response.text
            assert "secret-one" not in response.text
            assert str(tmp_path) not in response.text
        await harness.supervisor.close()

    asyncio.run(scenario())


def _gmail_env(tmp_path: Path) -> dict[str, str]:
    return {
        "DATABASE_URL": "",
        "POSTGRES_MODE": "off",
        "GMAIL_CLIENT_ID": "test.apps.googleusercontent.com",
        "GMAIL_CLIENT_SECRET": "test-secret",
        "GMAIL_REDIRECT_URI": "http://localhost:8000/v1/mail-todo/oauth/gmail/callback",
        "GMAIL_SCOPES": GMAIL_READONLY_SCOPE,
        "GMAIL_CONNECTION_DB_PATH": str(tmp_path / "connections.db"),
        "TOKEN_ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "OAUTH_STATE_SECRET": "state-secret-that-is-at-least-32-characters",
        "MICROSOFT_CLIENT_ID": "",
        "MICROSOFT_CLIENT_SECRET": "",
    }


@pytest.fixture(autouse=True)
def isolate_evaluation_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "EVALUATION_API_ENABLED",
        "EVALUATION_API_TOKEN",
        "EVALUATION_JOB_DB_PATH",
        "EVALUATION_ARTIFACT_ROOT",
        "MICROSOFT_CLIENT_ID",
        "MICROSOFT_CLIENT_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)


def test_create_app_excludes_evaluation_routes_when_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name, value in _gmail_env(tmp_path).items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("EVALUATION_API_ENABLED", "0")

    async def scenario() -> None:
        app = create_app()
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                types = await client.get(
                    "/v1/evaluation-types", headers=AUTH
                )
                submit = await client.post(
                    "/v1/evaluation-jobs",
                    json=submission_body(),
                    headers={**AUTH, "Idempotency-Key": "disabled"},
                )
                health = await client.get("/health")

        assert types.status_code == 404
        assert submit.status_code == 404
        assert health.status_code == 200

    asyncio.run(scenario())


def test_create_app_wires_runtime_recovery_and_auth_when_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name, value in _gmail_env(tmp_path).items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("EVALUATION_API_ENABLED", "1")
    monkeypatch.setenv("EVALUATION_API_TOKEN", TOKEN)
    monkeypatch.setenv("EVALUATION_JOB_DB_PATH", str(tmp_path / "evaluation-jobs.db"))
    monkeypatch.setenv("EVALUATION_ARTIFACT_ROOT", str(tmp_path / "evaluation-jobs"))

    async def scenario() -> None:
        app = create_app()
        async with app.router.lifespan_context(app):
            assert app.state.evaluation_service is not None
            assert app.state.evaluation_supervisor is not None
            assert (tmp_path / "evaluation-jobs.db").exists()
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                unauthorized = await client.get("/v1/evaluation-types")
                authorized = await client.get("/v1/evaluation-types", headers=AUTH)

        assert unauthorized.status_code == 401
        assert authorized.status_code == 200
        types = authorized.json()["types"]
        assert [entry["type"] for entry in types] == ["memory-eval"]

    asyncio.run(scenario())


def test_create_app_fails_fast_when_enabled_without_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name, value in _gmail_env(tmp_path).items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("EVALUATION_API_ENABLED", "1")
    monkeypatch.setenv("EVALUATION_API_TOKEN", "")

    with pytest.raises(ValueError, match="EVALUATION_API_TOKEN"):
        create_app()
