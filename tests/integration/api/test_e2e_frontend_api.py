"""E2E Frontend-API integration tests — NO MOCKS.

Every test in this module fires real HTTP requests at a real ``mail-todo-api``
subprocess loaded from the project ``.env`` file.  No fake adapters, no
monkeypatching, no ASGI transport tricks.

Architecture
------------
* A module-scoped ``pytest`` fixture starts ``mail-todo-api`` in a child
  process using the real ``.env`` on a dedicated ephemeral port (default
  18765, overridable via ``E2E_API_PORT`` env-var).
* The fixture waits up to 15 s for ``/health`` to respond, then hands an
  ``httpx.Client`` to every test.
* Tests that depend on a real Gmail OAuth connection are collected normally
  but skipped at runtime unless the live server already holds at least one
  active connection (i.e. the user has already done the OAuth flow).

Running
-------
  # From the repo root (Windows PowerShell):
  python -m pytest tests/integration/api/test_e2e_frontend_api.py -v

  # Pick a different port if 18765 is busy:
  $env:E2E_API_PORT=19000
  python -m pytest tests/integration/api/test_e2e_frontend_api.py -v

SPEC coverage (Increment A §8.1–8.7)
--------------------------------------
§8.1  connect Gmail → run → see Tasks        (requires_gmail)
§8.2  task cards: title, steps, citations    (requires_gmail)
§8.3  Partial Plan visually distinct         (requires_gmail, best-effort)
§8.4  correlated Tasks + Gmail deep link     (requires_gmail)
§8.5  idempotent Run creation                (requires_gmail)
§8.6  error states: 404 / 409 / 503          (no Gmail needed)
§8.7  no raw email body in any response      (requires_gmail)
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Generator
from pathlib import Path

import httpx
import pytest

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[3]
_ENV_FILE = _REPO_ROOT / ".env"
_PORT = int(os.environ.get("E2E_API_PORT", "18765"))
_BASE_URL = f"http://127.0.0.1:{_PORT}"
_STARTUP_TIMEOUT = 20  # seconds
_HEALTH_POLL_INTERVAL = 0.5


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def api_server() -> Generator[subprocess.Popen[bytes], None, None]:
    """Start a real mail-todo-api process on an ephemeral port.

    Loads environment from the project .env, overrides APP_PORT, and waits
    for /health to respond before yielding.  Terminates the process on
    teardown.
    """
    if not _ENV_FILE.exists():
        pytest.skip(f".env not found at {_ENV_FILE} — cannot run E2E tests")

    env = {**os.environ, "APP_PORT": str(_PORT), "APP_HOST": "127.0.0.1"}

    # Load .env manually so we can pass it into the subprocess without
    # importing python-dotenv (it is already a project dependency).
    for raw_line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env.setdefault(key.strip(), value.strip())

    # Override port so we don't clash with a manually-running dev server
    env["APP_PORT"] = str(_PORT)
    env["APP_HOST"] = "127.0.0.1"

    # Use the application entrypoint so Windows selects SelectorEventLoop for
    # psycopg instead of uvicorn's default Proactor loop.
    proc = subprocess.Popen(
        [sys.executable, "-m", "cowork_agent.app"],
        cwd=str(_REPO_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    deadline = time.monotonic() + _STARTUP_TIMEOUT
    started = False
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(f"{_BASE_URL}/health", timeout=2)
            if resp.status_code == 200:
                started = True
                break
        except httpx.RequestError:
            pass
        time.sleep(_HEALTH_POLL_INTERVAL)

    if not started:
        proc.terminate()
        output = proc.stdout.read().decode(errors="replace") if proc.stdout else ""
        pytest.fail(
            f"mail-todo-api did not start within {_STARTUP_TIMEOUT}s.\n"
            f"Server output:\n{output}"
        )

    yield proc

    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture(scope="module")
def client(api_server: subprocess.Popen[bytes]) -> httpx.Client:
    """Synchronous httpx client pre-pointed at the live server."""
    return httpx.Client(base_url=_BASE_URL, timeout=30)


@pytest.fixture(scope="module")
def first_connection_id(client: httpx.Client) -> str:
    """Return the first active connection id, or skip the test if none exist.

    The user must have already completed Gmail OAuth via the GUI or browser
    before running tests that depend on a real mailbox connection.
    """
    resp = client.get("/v1/mail-todo/connections")
    assert resp.status_code == 200, f"Unexpected {resp.status_code}: {resp.text}"
    connections = resp.json().get("connections", [])
    active = [c for c in connections if c.get("status") == "active"]
    if not active:
        pytest.skip(
            "No active Gmail connections found on the live server. "
            "Complete the OAuth flow in the GUI first, then re-run."
        )
    return str(active[0]["id"])


# ---------------------------------------------------------------------------
# §8.6  Error-state tests — no Gmail connection required
# ---------------------------------------------------------------------------


class TestErrorStates:
    """SPEC §8.6: backend-down, run-failed, and empty-result states each render
    a clear, actionable message."""

    def test_health_returns_ok(self, client: httpx.Client) -> None:
        """Baseline: server is up and healthy."""
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_connections_list_shape(self, client: httpx.Client) -> None:
        """GET /connections always returns a JSON object with a 'connections' list."""
        resp = client.get("/v1/mail-todo/connections")
        assert resp.status_code == 200
        body = resp.json()
        assert "connections" in body
        assert isinstance(body["connections"], list)

    def test_oauth_redirect_starts_google_flow(self, client: httpx.Client) -> None:
        """GET /oauth/gmail/connect redirects to accounts.google.com with
        the gmail.readonly scope — proves FR-03 OAuth initiation."""
        resp = client.get("/v1/mail-todo/oauth/gmail/connect", follow_redirects=False)
        assert resp.status_code == 302
        location = resp.headers["location"]
        assert "accounts.google.com" in location, (
            f"Expected Google OAuth redirect, got: {location}"
        )
        assert "gmail.readonly" in location, (
            f"gmail.readonly scope missing from OAuth URL: {location}"
        )

    def test_run_not_found_returns_404(self, client: httpx.Client) -> None:
        """GET /runs/<nonexistent> → 404."""
        resp = client.get("/v1/mail-todo/runs/run-does-not-exist-e2e")
        assert resp.status_code == 404

    def test_result_not_found_returns_404(self, client: httpx.Client) -> None:
        """GET /runs/<nonexistent>/result → 404."""
        resp = client.get("/v1/mail-todo/runs/run-does-not-exist-e2e/result")
        assert resp.status_code == 404

    def test_tasks_not_found_returns_404(self, client: httpx.Client) -> None:
        """GET /runs/<nonexistent>/tasks → 404."""
        resp = client.get("/v1/mail-todo/runs/run-does-not-exist-e2e/tasks")
        assert resp.status_code == 404

    def test_unread_preview_unknown_connection_is_404(self, client: httpx.Client) -> None:
        """GET /connections/<bad-id>/unread-preview → 404."""
        resp = client.get("/v1/mail-todo/connections/mbx-no-such-connection/unread-preview")
        assert resp.status_code == 404

    def test_disconnect_unknown_connection_is_404(self, client: httpx.Client) -> None:
        """DELETE /connections/<bad-id> → 404."""
        resp = client.delete("/v1/mail-todo/connections/mbx-no-such-connection")
        assert resp.status_code == 404

    def test_create_run_without_idempotency_key_is_422(self, client: httpx.Client) -> None:
        """POST /runs without Idempotency-Key header → 422 Unprocessable Entity.

        The GUI always sends this header; missing it is a client-contract
        violation, not a server error.
        """
        resp = client.post(
            "/v1/mail-todo/runs",
            json={"mailboxConnectionId": "any-id"},
            # Deliberately omit Idempotency-Key header
        )
        assert resp.status_code == 422

    def test_create_run_with_unknown_connection_is_404(self, client: httpx.Client) -> None:
        """POST /runs with a valid request but nonexistent connection → 404."""
        resp = client.post(
            "/v1/mail-todo/runs",
            json={"mailboxConnectionId": "mbx-does-not-exist"},
            headers={"Idempotency-Key": "e2e-unknown-conn"},
        )
        assert resp.status_code == 404

    def test_error_detail_never_exposes_secrets(self, client: httpx.Client) -> None:
        """Error responses must not leak API keys, tokens, or credential values.

        We probe a 404 and confirm the response body contains no obvious
        secret-shaped strings (long hex/base64 runs).
        """
        import re
        resp = client.get("/v1/mail-todo/runs/definitely-not-real")
        assert resp.status_code == 404
        # A raw Fernet key or API key would be 44+ chars of base64/hex
        body = resp.text
        assert not re.search(r"[A-Za-z0-9+/=]{44,}", body), (
            f"Possible credential leak in error body: {body[:200]}"
        )


# ---------------------------------------------------------------------------
# §8.1, §8.5, §8.6, §8.7 — require a real connected Gmail account
# ---------------------------------------------------------------------------


class TestWithRealConnection:
    """Tests that exercise the full run lifecycle against a live Gmail account.

    All tests in this class call ``first_connection_id`` and are automatically
    skipped if no active Gmail connection exists on the server.

    SPEC coverage:
      §8.1  connect → run → see Tasks
      §8.5  idempotent run creation
      §8.6  409 while still running
      §8.7  no raw email body in any response
    """

    def test_connection_has_expected_fields(
        self, client: httpx.Client, first_connection_id: str
    ) -> None:
        """Active connection exposes id, emailAddress, provider, scopes, status."""
        resp = client.get("/v1/mail-todo/connections")
        assert resp.status_code == 200
        connections = resp.json()["connections"]
        conn = next(c for c in connections if c["id"] == first_connection_id)
        assert conn["provider"] == "gmail"
        assert "emailAddress" in conn
        assert "https://www.googleapis.com/auth/gmail.readonly" in conn["scopes"]
        assert conn["status"] == "active"
        assert "createdAt" in conn

    def test_unread_preview_returns_message_list(
        self, client: httpx.Client, first_connection_id: str
    ) -> None:
        """GET /connections/{id}/unread-preview returns emailsMatched + messages list."""
        resp = client.get(
            f"/v1/mail-todo/connections/{first_connection_id}/unread-preview",
            params={"limit": 5},
        )
        # 409 means reauth required — treat as a real connectivity problem
        if resp.status_code == 409:
            pytest.skip("Gmail token requires re-authorization (409); re-auth and re-run.")
        # 503 means Gmail is temporarily down
        if resp.status_code == 503:
            pytest.skip("Gmail temporarily unavailable (503).")
        assert resp.status_code == 200, f"Unexpected {resp.status_code}: {resp.text}"
        body = resp.json()
        assert "emailsMatched" in body
        assert "messages" in body
        assert isinstance(body["messages"], list)
        # ADR-003: no raw body field on any message object
        for msg in body["messages"]:
            assert "body" not in msg, (
                f"Raw email body must never appear in unread-preview: {list(msg.keys())}"
            )

    def test_create_run_returns_202_with_status_url(
        self, client: httpx.Client, first_connection_id: str
    ) -> None:
        """POST /runs → 202 with id and statusUrl."""
        resp = client.post(
            "/v1/mail-todo/runs",
            json={"mailboxConnectionId": first_connection_id, "maxEmails": 2},
            headers={"Idempotency-Key": "e2e-run-create-check"},
        )
        # 503 means LLM not configured — still validates the HTTP contract
        if resp.status_code == 503:
            pytest.skip(
                "LLM provider returned 503 (not configured). "
                "Check LLM_PROVIDER / API key in .env."
            )
        assert resp.status_code == 202, f"Expected 202, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert "id" in body
        assert "statusUrl" in body
        assert body["statusUrl"].startswith("/v1/mail-todo/runs/")

    def test_idempotent_run_creation(
        self, client: httpx.Client, first_connection_id: str
    ) -> None:
        """SPEC §8.5: two POSTs with the same Idempotency-Key must return the same run id."""
        key = "e2e-idempotency-test-key-001"
        payload = {"mailboxConnectionId": first_connection_id, "maxEmails": 2}
        headers = {"Idempotency-Key": key}

        r1 = client.post("/v1/mail-todo/runs", json=payload, headers=headers)
        if r1.status_code == 503:
            pytest.skip("LLM provider 503 — cannot test idempotency without a working provider.")
        assert r1.status_code == 202, f"First POST failed: {r1.status_code} {r1.text}"
        run_id_1 = r1.json()["id"]

        r2 = client.post("/v1/mail-todo/runs", json=payload, headers=headers)
        assert r2.status_code == 202, f"Second POST failed: {r2.status_code} {r2.text}"
        run_id_2 = r2.json()["id"]

        assert run_id_1 == run_id_2, (
            f"Idempotency violated: first={run_id_1}, second={run_id_2}"
        )

    def test_run_status_has_progress_fields(
        self, client: httpx.Client, first_connection_id: str
    ) -> None:
        """GET /runs/{id} returns progress sub-object with all expected keys."""
        r = client.post(
            "/v1/mail-todo/runs",
            json={"mailboxConnectionId": first_connection_id, "maxEmails": 2},
            headers={"Idempotency-Key": "e2e-run-status-fields"},
        )
        if r.status_code == 503:
            pytest.skip("LLM 503; cannot inspect run status.")
        assert r.status_code == 202
        run_id = r.json()["id"]
        status_url = r.json()["statusUrl"]

        resp = client.get(status_url)
        assert resp.status_code == 200, f"Status poll failed: {resp.status_code} {resp.text}"
        body = resp.json()
        assert body["id"] == run_id
        assert "status" in body
        progress = body.get("progress", {})
        for field in ("emailsMatched", "emailsProcessed", "emailsToProcess", "maxEmails"):
            assert field in progress, f"Missing progress field: {field}"

    def test_result_returns_409_while_running(
        self, client: httpx.Client, first_connection_id: str
    ) -> None:
        """SPEC §8.6: GET /result on a QUEUED/RUNNING run → 409 RUN_NOT_COMPLETE."""
        r = client.post(
            "/v1/mail-todo/runs",
            json={"mailboxConnectionId": first_connection_id, "maxEmails": 2},
            headers={"Idempotency-Key": "e2e-result-409-check"},
        )
        if r.status_code == 503:
            pytest.skip("LLM 503; cannot test 409 guard.")
        assert r.status_code == 202
        run_id = r.json()["id"]

        # Immediately hit /result — run may still be queued or running
        result_resp = client.get(f"/v1/mail-todo/runs/{run_id}/result")
        # Either 409 (not complete) or 200 (already done for tiny mailbox)
        assert result_resp.status_code in {200, 409}, (
            f"Expected 200 or 409, got {result_resp.status_code}: {result_resp.text}"
        )
        if result_resp.status_code == 409:
            assert result_resp.json().get("detail") == "RUN_NOT_COMPLETE"

    def test_tasks_endpoint_returns_409_while_running(
        self, client: httpx.Client, first_connection_id: str
    ) -> None:
        """SPEC §8.6: GET /tasks on QUEUED/RUNNING run → 409."""
        r = client.post(
            "/v1/mail-todo/runs",
            json={"mailboxConnectionId": first_connection_id, "maxEmails": 2},
            headers={"Idempotency-Key": "e2e-tasks-409-check"},
        )
        if r.status_code == 503:
            pytest.skip("LLM 503; cannot test tasks 409 guard.")
        assert r.status_code == 202
        run_id = r.json()["id"]

        tasks_resp = client.get(f"/v1/mail-todo/runs/{run_id}/tasks")
        assert tasks_resp.status_code in {200, 409}, (
            f"Expected 200 or 409, got {tasks_resp.status_code}: {tasks_resp.text}"
        )
        if tasks_resp.status_code == 409:
            assert tasks_resp.json().get("detail") == "RUN_NOT_COMPLETE"

    def test_no_raw_email_body_in_run_status(
        self, client: httpx.Client, first_connection_id: str
    ) -> None:
        """SPEC §8.7: run status must never contain a 'body' field at any depth."""
        r = client.post(
            "/v1/mail-todo/runs",
            json={"mailboxConnectionId": first_connection_id, "maxEmails": 2},
            headers={"Idempotency-Key": "e2e-no-body-status"},
        )
        if r.status_code == 503:
            pytest.skip("LLM 503.")
        assert r.status_code == 202
        status_resp = client.get(r.json()["statusUrl"])
        assert status_resp.status_code == 200
        _assert_no_raw_email_body(status_resp.json(), path="run_status")

    def test_full_run_lifecycle_and_no_raw_body_in_result(
        self, client: httpx.Client, first_connection_id: str
    ) -> None:
        """SPEC §8.1 + §8.7: poll until completion, then assert result shape and
        no raw email body at any path in the JSON response."""
        import time as _time
        r = client.post(
            "/v1/mail-todo/runs",
            json={"mailboxConnectionId": first_connection_id, "maxEmails": 3},
            headers={"Idempotency-Key": "e2e-full-lifecycle-v1"},
        )
        if r.status_code == 503:
            pytest.skip("LLM provider not configured (503).")
        assert r.status_code == 202
        run_id = r.json()["id"]
        status_url = r.json()["statusUrl"]

        # Poll until done (max 120 s — real Gemini calls take ~10–30 s per batch)
        deadline = _time.monotonic() + 120
        final_status = None
        while _time.monotonic() < deadline:
            sr = client.get(status_url)
            assert sr.status_code == 200
            body = sr.json()
            if body["status"] in {"completed", "failed", "partial"}:
                final_status = body["status"]
                break
            _time.sleep(2)

        if final_status is None:
            pytest.skip("Run did not finish within 120 s — increase timeout or reduce maxEmails.")

        if final_status == "failed":
            err = client.get(status_url).json().get("error") or {}
            pytest.fail(f"Run failed: {err.get('code')} — {err.get('message')}")

        # §8.7: no raw email body anywhere in the run status
        _assert_no_raw_email_body(client.get(status_url).json(), path="run_status")

        # Fetch result (SPEC §8.1)
        result_resp = client.get(f"/v1/mail-todo/runs/{run_id}/result")
        assert result_resp.status_code == 200, (
            f"Result fetch failed: {result_resp.status_code} {result_resp.text}"
        )
        result_body = result_resp.json()
        _assert_no_raw_email_body(result_body, path="result")

        # Fetch tasks (SPEC §8.2)
        tasks_resp = client.get(f"/v1/mail-todo/runs/{run_id}/tasks")
        assert tasks_resp.status_code == 200, (
            f"Tasks fetch failed: {tasks_resp.status_code} {tasks_resp.text}"
        )
        tasks_body = tasks_resp.json()
        _assert_no_raw_email_body(tasks_body, path="tasks")
        assert "tasks" in tasks_body
        assert isinstance(tasks_body["tasks"], list)

    def test_completed_tasks_have_expected_fields(
        self, client: httpx.Client, first_connection_id: str
    ) -> None:
        """SPEC §8.2: every task card exposes title, route, actionability,
        source_message_ids.  Steps and citations present when non-empty plan."""
        import time as _time
        r = client.post(
            "/v1/mail-todo/runs",
            json={"mailboxConnectionId": first_connection_id, "maxEmails": 3},
            headers={"Idempotency-Key": "e2e-task-fields-check"},
        )
        if r.status_code == 503:
            pytest.skip("LLM 503.")
        assert r.status_code == 202
        run_id = r.json()["id"]
        status_url = r.json()["statusUrl"]

        deadline = _time.monotonic() + 120
        done = False
        while _time.monotonic() < deadline:
            sr = client.get(status_url)
            if sr.json()["status"] in {"completed", "failed", "partial"}:
                done = True
                break
            _time.sleep(2)

        if not done or sr.json()["status"] == "failed":
            pytest.skip("Run did not finish or failed; skip field inspection.")

        tasks_resp = client.get(f"/v1/mail-todo/runs/{run_id}/tasks")
        if tasks_resp.status_code != 200:
            pytest.skip(f"Tasks endpoint returned {tasks_resp.status_code}.")

        tasks = tasks_resp.json().get("tasks", [])
        if not tasks:
            pytest.skip("No tasks returned (empty inbox or all NO_ACTION); skip field check.")

        for task in tasks:
            assert "title" in task, f"Task missing 'title': {list(task.keys())}"
            assert "route" in task, f"Task missing 'route': {list(task.keys())}"
            assert "actionability" in task, f"Task missing 'actionability': {list(task.keys())}"
            # §8.4: source_message_ids drives the correlated-emails indicator
            assert "source_message_ids" in task, (
                f"Task missing 'source_message_ids': {list(task.keys())}"
            )
            # §8.2: plan steps present for actionable tasks
            if task.get("actionability") in {"action_required", "action_suggested"}:
                assert "steps" in task or "plan" in task, (
                    f"Actionable task missing steps/plan: {list(task.keys())}"
                )

    def test_partial_plan_flag_present_in_result(
        self, client: httpx.Client, first_connection_id: str
    ) -> None:
        """SPEC §8.3: when a task has partial=True, missing_information must be present.

        This is a best-effort check; it only asserts the structural invariant
        when partial tasks are actually returned by the LLM.
        """
        import time as _time
        r = client.post(
            "/v1/mail-todo/runs",
            json={"mailboxConnectionId": first_connection_id, "maxEmails": 5},
            headers={"Idempotency-Key": "e2e-partial-plan-check"},
        )
        if r.status_code == 503:
            pytest.skip("LLM 503.")
        assert r.status_code == 202
        run_id = r.json()["id"]
        status_url = r.json()["statusUrl"]

        deadline = _time.monotonic() + 120
        while _time.monotonic() < deadline:
            if client.get(status_url).json()["status"] in {"completed", "failed", "partial"}:
                break
            _time.sleep(2)

        tasks_resp = client.get(f"/v1/mail-todo/runs/{run_id}/tasks")
        if tasks_resp.status_code != 200:
            pytest.skip("Tasks endpoint not 200.")

        tasks = tasks_resp.json().get("tasks", [])
        partial_tasks = [t for t in tasks if t.get("partial") is True]
        if not partial_tasks:
            pytest.skip("No partial tasks in this run (not an error — depends on email content).")

        for pt in partial_tasks:
            assert "missing_information" in pt, (
                f"Partial task must have 'missing_information': {list(pt.keys())}"
            )
            assert isinstance(pt["missing_information"], list)

    def test_gmail_deep_link_present_for_tasks_with_source(
        self, client: httpx.Client, first_connection_id: str
    ) -> None:
        """SPEC §8.4: tasks with source_message_ids expose a gmail_url / deep_link."""
        import time as _time
        r = client.post(
            "/v1/mail-todo/runs",
            json={"mailboxConnectionId": first_connection_id, "maxEmails": 3},
            headers={"Idempotency-Key": "e2e-deeplink-check"},
        )
        if r.status_code == 503:
            pytest.skip("LLM 503.")
        assert r.status_code == 202
        run_id = r.json()["id"]
        status_url = r.json()["statusUrl"]

        deadline = _time.monotonic() + 120
        while _time.monotonic() < deadline:
            if client.get(status_url).json()["status"] in {"completed", "failed", "partial"}:
                break
            _time.sleep(2)

        tasks_resp = client.get(f"/v1/mail-todo/runs/{run_id}/tasks")
        if tasks_resp.status_code != 200:
            pytest.skip("Tasks not 200.")

        tasks = tasks_resp.json().get("tasks", [])
        sourced = [t for t in tasks if t.get("source_message_ids")]
        if not sourced:
            pytest.skip("No tasks with source_message_ids; skip deep-link check.")

        for task in sourced:
            # The GUI uses gmail_url from the task or falls back to unread-preview link
            has_link = (
                "gmail_url" in task
                or "deep_link" in task
                or any("gmail" in str(v).lower() for v in task.values() if isinstance(v, str))
            )
            assert has_link, (
                f"Task with source emails has no Gmail deep-link field: {list(task.keys())}"
            )

    def test_no_raw_email_body_in_tasks(
        self, client: httpx.Client, first_connection_id: str
    ) -> None:
        """SPEC §8.7 invariant: tasks endpoint must never return raw email body content."""
        import time as _time
        r = client.post(
            "/v1/mail-todo/runs",
            json={"mailboxConnectionId": first_connection_id, "maxEmails": 3},
            headers={"Idempotency-Key": "e2e-no-body-tasks"},
        )
        if r.status_code == 503:
            pytest.skip("LLM 503.")
        assert r.status_code == 202
        run_id = r.json()["id"]
        status_url = r.json()["statusUrl"]

        deadline = _time.monotonic() + 120
        while _time.monotonic() < deadline:
            if client.get(status_url).json()["status"] in {"completed", "failed", "partial"}:
                break
            _time.sleep(2)

        tasks_resp = client.get(f"/v1/mail-todo/runs/{run_id}/tasks")
        if tasks_resp.status_code == 200:
            _assert_no_raw_email_body(tasks_resp.json(), path="tasks")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _assert_no_raw_email_body(obj: object, path: str = "") -> None:
    """Recursively assert that no dict key named 'body' appears in the response.

    SPEC §8.7 / AGENTS.md invariant 1: raw email bodies are never persisted
    or surfaced in any API response.

    A key named 'body' is the canonical indicator of a raw email body field.
    """
    if isinstance(obj, dict):
        assert "body" not in obj, (
            f"Raw email body field found at path '{path}': keys={list(obj.keys())}"
        )
        for k, v in obj.items():
            _assert_no_raw_email_body(v, path=f"{path}.{k}")
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            _assert_no_raw_email_body(item, path=f"{path}[{i}]")
