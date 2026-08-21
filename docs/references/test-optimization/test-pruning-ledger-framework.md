# Test pruning ledger — framework re-tests (§4 condition 3)

Scratch ledger for Task 5. No tests deleted.

§4 condition 3: delete a test that re-tests framework behaviour (pydantic
validation, FastAPI routing) **with no app policy on top**. If the test also
pins an app error code / message / envelope, **keep** and consider a §3 row.

## Search performed

| Pattern | Result |
|---|---|
| `ValidationError` in `tests/**/*.py` | **none** |
| `pydantic` in `tests/**/*.py` | **none** |
| `status_code == 422` | 5 hits (4 in `test_chat_api.py`, 1 live e2e) |
| `status_code == 404` | ownership / missing-resource app policy, not FastAPI default routing |
| `pytest.raises(ValueError` | domain/config constructors (app bounds), not pydantic |

## Ledger

| id | nodeid | extra app invariant? | proposed action | notes |
|---|---|---|---|---|
| F-001 | `tests/integration/api/test_chat_api.py::test_message_endpoint_rejects_a_path_and_payload_session_mismatch` | yes — path `session-1` vs body `session-2` → 422 | **keep** + **add §3 row** | App session-binding policy, not generic FastAPI 422 |
| F-002 | `tests/integration/api/test_chat_api.py::test_message_endpoint_rejects_oversized_idempotency_key` | yes — idempotency key length 129 → 422 | **keep** + **add §3 row** | Same bound as domain `ChatMessageRequest`; HTTP envelope is the extra fact |
| F-003 | `tests/integration/api/test_chat_api.py::test_message_endpoint_rejects_retired_tool_choices_before_controller_dispatch` | yes — `tool_choices: ["@Email"]` rejected at HTTP before controller | **keep** + **add §3 row** | ADR-004; D-008 already keeps the domain twin |
| F-004 | `tests/integration/api/test_chat_api.py` profile oversized `language` (`"x"*201`) → 422 | yes — profile language max length | **keep** + **add §3 row** | Nested in profile CRUD scenario; not a standalone pydantic demo |
| F-005 | `tests/integration/api/test_e2e_frontend_api.py::test_create_run_without_idempotency_key_is_422` | yes — missing `Idempotency-Key` is a client-contract 422 | **keep** | Protected `live`; also R03-adjacent |
| F-006 | Remaining `status_code == 404` in chat/principal/e2e | yes — missing owned resource / foreign principal | **keep** | App ACL, not FastAPI default 404 page |
| F-007 | Domain `pytest.raises(ValueError, …)` in `test_chat_contracts.py` / `test_target_contracts.py` | yes — frozen contract bounds | **keep** | Not pydantic `ValidationError`. Owners already in §3 / domain routes |

## Drafted new §3 rows (not copied to README)

| Invariant | Owned by | Do not re-assert in |
|---|---|---|
| Chat message path `session_id` must match JSON `session_id` (HTTP 422) | `integration/api/test_chat_api.py` | domain request tests except one wire-up |
| Chat HTTP rejects idempotency keys above the contract length (422) | `integration/api/test_chat_api.py` | domain `ChatMessageRequest` except one wire-up |
| Chat HTTP rejects retired `tool_choices` before controller dispatch (422) | `integration/api/test_chat_api.py` | domain retired-tool tests except one wire-up |
| Chat profile language above max length is HTTP 422 | `integration/api/test_chat_api.py` | — |

## Counts

- Proposed **delete**: **0**
- Proposed **keep**: 7 rows (4 with new §3 drafts)
- pydantic/FastAPI-only tests: **none found**
