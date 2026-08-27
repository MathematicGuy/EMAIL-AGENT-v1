# SPEC: Replace Vyce AI Provider with Xiaomi MiMo

**Status:** Ready for Implementation  
**Date:** 2026-08-23  
**Skills:** `spec-driven-development`, `source-driven-development`, `api-and-interface-design`  

---

## 1. Context & Motivation

The system currently includes a provider adapter for **Vyce** (and legacy alias `vyne`) in the Email Action Plan and AI Chat workflows. Vyce is being sunsetted/replaced across the platform with **Xiaomi MiMo** (`mimo-v2.5-pro` and `mimo-v2.5`), an OpenAI-compatible large language model family developed for advanced reasoning, agentic tool workflows, and long context.

This specification defines the complete replacement of Vyce/Vyne with Xiaomi MiMo across configuration, runtime adapters, provider factories, evaluation scripts, and test suites.

---

## 2. Requirements & Key Conventions

### 2.1 Configuration & Environment Keys
- **Provider Identifier:** `mimo` (in `LLM_PROVIDER` env variable and runtime flags: `openrouter | gemini | mimo | mistral`).
- **Key Convention in `.env`:** Key rotation prefix `MIMO_API_KEY` supporting standard single and numbered multi-key format:
  - `MIMO_API_KEY`, `MIMO_API_KEY2`, `MIMO_API_KEY_1`, `MIMO_API_KEY_2`, etc.
  - Handled cleanly via `APIKeyRotator.from_env("MIMO_API_KEY", environ=environ, provider_name="Mimo")`.
- **Default Models:**
  - `mimo-v2.5-pro` (Primary default for reasoning & action planning)
  - `mimo-v2.5` (Multimodal and general conversational fallback/option)
- **Base URL:**
  - `MIMO_BASE_URL` with default `https://api.xiaomimimo.com/v1`.
- **Other Settings:**
  - `MIMO_MAX_EMAILS_PER_BATCH`: default `5`
  - `MIMO_MAX_OUTPUT_TOKENS`: default `4096` (bounded maximum `8192`)
  - `MIMO_TIMEOUT_SECONDS`: default `60` (bounded maximum `120`)
  - `MIMO_ROTATE_ON_RATE_LIMIT`: default `True`

### 2.2 Removal of Vyce/Vyne
- Completely remove all `VyceSettings`, `VyneSettings`, `VYCE_*`, and `VYNE_*` variables from `config.py`, root `config` file, and `.env.example`.
- Remove `vyce.py` provider file and rename/replace it with `mimo.py`.
- Remove legacy aliases `vyne` and `vyce` from provider factory validation.

---

## 3. Architecture & Target Design

```text
src/cowork_agent/
├── config.py                               # MimoSettings with APIKeyRotator("MIMO_API_KEY")
├── integrations/llm/
│   ├── providers/
│   │   ├── mimo.py                         # [NEW] MimoRouteClassifier, MimoActionPlanGenerator, MimoAPIError
│   │   ├── openai_transport.py             # Shared OpenAI-compatible HTTP JSON transport
│   │   └── __init__.py                     # Exports Mimo adapters & exceptions
│   ├── chat_intent.py                      # MimoIntentClassifier
│   ├── chat_reply.py                       # MimoChatReply
│   ├── provider_factory.py                 # "mimo" provider factory registration
│   └── __init__.py                         # Exports Mimo adapters
└── app.py                                  # Label "mimo": "Mimo"
```

### 3.1 Transport & Authentication
Xiaomi MiMo uses OpenAI-compatible `/chat/completions` API:
- **Endpoint:** `POST {MIMO_BASE_URL}/chat/completions`
- **Headers:** `Authorization: Bearer <key>` (also accepts `api-key: <key>`), `Content-Type: application/json`, `User-Agent: module-mail/0.1.0`.
- **JSON Schema Output:** Utilizes `response_format: {"type": "json_object"}` and system instructions embedding the target JSON schema.
- **Immediate Key Rotation:** On HTTP 429 (Rate Limit) or HTTP 500/502/503/504 (Gateway/Server Error), sleeps 0.5s and rotates to the next candidate key in `MimoSettings.rotator`.

### 3.2 Error Hierarchy
```python
class MimoAPIError(RuntimeError):
    error_code = "MIMO_API_ERROR"
    safe_message = "Mimo không thể phân tích email. Vui lòng kiểm tra cấu hình model và thử lại."


class MimoRateLimitError(MimoAPIError):
    error_code = "MIMO_RATE_LIMIT_ERROR"


class MimoGatewayError(MimoAPIError):
    error_code = "MIMO_GATEWAY_ERROR"
```

---

## 4. Scope of Changes

| Component | Changes |
| :--- | :--- |
| `config` | Replace `VYNE_*` with `MIMO_*`, update provider choices `(openrouter \| gemini \| mimo \| mistral)` |
| `.env.example` | Replace `VYCE_*` with `MIMO_API_KEY`, `MIMO_MODEL`, `MIMO_BASE_URL` |
| `src/cowork_agent/config.py` | Add `MimoSettings`, remove `VyceSettings` and `VyneSettings` |
| `src/cowork_agent/integrations/llm/providers/mimo.py` | Create Mimo adapter (replacing `vyce.py`) |
| `src/cowork_agent/integrations/llm/providers/__init__.py` | Export Mimo symbols, remove Vyce |
| `src/cowork_agent/integrations/llm/chat_intent.py` | Add `MimoIntentClassifier`, remove `VyceIntentClassifier` |
| `src/cowork_agent/integrations/llm/chat_reply.py` | Add `MimoChatReply`, remove `VyceChatReply` |
| `src/cowork_agent/integrations/llm/provider_factory.py` | Register `"mimo"`, drop `"vyce"`/`"vyne"` |
| `src/cowork_agent/integrations/llm/__init__.py` | Export `MimoIntentClassifier`, `MimoChatReply` |
| `src/cowork_agent/app.py` | Map `"mimo": "Mimo"` |
| `scripts/` | Update memory evaluation and routing scripts to support `"mimo"` |
| `tests/` | Migrate `test_vyce.py` -> `test_mimo.py`, update `test_classifiers.py`, `test_generators.py`, `test_chat_reply.py`, `test_provider_factory.py`, `test_evaluate_memory_provider.py`, `test_workflow.py` |

---

## 5. Acceptance Criteria

1. `uv run ruff check .` passes with zero lint errors.
2. `uv run mypy src` passes strict type checking with zero errors.
3. `uv run pytest -q -k "mimo or provider_factory or chat_reply or classifiers or generators"` passes 100%.
4. Setting `LLM_PROVIDER=mimo` in `config` properly loads `MimoSettings` and constructs `MimoRouteClassifier`, `MimoActionPlanGenerator`, `MimoIntentClassifier`, and `MimoChatReply`.
5. Key rotation correctly cycles through multiple `MIMO_API_KEY*` entries upon rate limiting.
