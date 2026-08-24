# PLAN: Replace Vyce AI Provider with Xiaomi MiMo

**Status:** Ready for Execution  
**Date:** 2026-08-23  
**Spec:** `tasks/specs/SPEC-replace-vyce-with-xiaomi-mimo.md`  
**Skills:** `incremental-implementation`, `test-driven-development`  

---

## 1. Phases Overview

- **Phase 1:** Configuration & Core Settings (`config.py`, `config`, `.env.example`).
- **Phase 2:** Transport Adapter & Providers (`providers/mimo.py`, `providers/__init__.py`, removal of `vyce.py`).
- **Phase 3:** Chat Runtime Integration (`chat_intent.py`, `chat_reply.py`, `provider_factory.py`, `app.py`).
- **Phase 4:** Evaluation Scripts (`scripts/evaluate_*.py`, `scripts/memeval_latency_gate.py`).
- **Phase 5:** Test Migration & Verification (`tests/unit/integrations/llm/test_mimo.py`, updating existing tests).
- **Phase 6:** Quality Gate Verification (`ruff check`, `mypy src`, `pytest`).

---

## 2. Detailed Task Breakdown

### Phase 1: Configuration & Settings
- [ ] Task 1.1: Add `MimoSettings` to `src/cowork_agent/config.py`:
  - `rotator = APIKeyRotator.from_env("MIMO_API_KEY", environ=environ, provider_name="Mimo")`
  - `model = environ.get("MIMO_MODEL") or "mimo-v2.5-pro"`
  - `base_url = environ.get("MIMO_BASE_URL") or "https://api.xiaomimimo.com/v1"`
  - Max emails, max tokens, timeout, rotation flags.
  - Remove `VyceSettings` and `VyneSettings`.
- [ ] Task 1.2: Update root `config`:
  - Replace `LLM_PROVIDER` comment list with `(openrouter | gemini | mimo | mistral)`.
  - Replace `# Vyne Configuration` section with `# Xiaomi Mimo Configuration` (`MIMO_MODEL=mimo-v2.5-pro`, `MIMO_BASE_URL=https://api.xiaomimimo.com/v1`, etc.).
- [ ] Task 1.3: Update `.env.example`:
  - Replace `VYCE_*` section with `MIMO_API_KEY`, `MIMO_MODEL`, `MIMO_BASE_URL`.

### Phase 2: Transport Adapter & Providers
- [ ] Task 2.1: Create `src/cowork_agent/integrations/llm/providers/mimo.py`:
  - `MimoAPIError`, `MimoRateLimitError`, `MimoGatewayError`.
  - `MimoActionPlanGenerator(ConfiguredActionPlanGenerator)`.
  - `MimoRouteClassifier(ConfiguredRouteClassifier)` with `provider_name="mimo"`.
  - `execute_chat_completion(settings: MimoSettings, ...)`.
- [ ] Task 2.2: Remove `src/cowork_agent/integrations/llm/providers/vyce.py`.
- [ ] Task 2.3: Update `src/cowork_agent/integrations/llm/providers/__init__.py` to export `Mimo*` symbols.

### Phase 3: Chat Runtime Integration
- [ ] Task 3.1: In `src/cowork_agent/integrations/llm/chat_intent.py`:
  - Define `MimoIntentClassifier(ConfiguredIntentClassifier)`.
  - Remove `VyceIntentClassifier` / `VyneIntentClassifier`.
- [ ] Task 3.2: In `src/cowork_agent/integrations/llm/chat_reply.py`:
  - Define `MimoChatReply(_ConfiguredChatReply)`.
  - Remove `VyceChatReply` / `VyneChatReply`.
- [ ] Task 3.3: In `src/cowork_agent/integrations/llm/provider_factory.py`:
  - Register `"mimo"` in `_SUPPORTED`.
  - Update `normalize_llm_provider`, `create_email_action_plan_bundle`, `create_chat_runtime_bundle`.
  - Remove `"vyce"` and `"vyne"`.
- [ ] Task 3.4: In `src/cowork_agent/integrations/llm/__init__.py`:
  - Update exports for `MimoIntentClassifier`, `MimoChatReply`, etc.
- [ ] Task 3.5: In `src/cowork_agent/app.py`:
  - Update provider map to `"mimo": "Mimo"`.

### Phase 4: Evaluation Scripts
- [ ] Task 4.1: Update `scripts/evaluate_chat_routing.py` (replace vyce/vyne with mimo).
- [ ] Task 4.2: Update `scripts/evaluate_email_golden.py` (replace vyce/vyne with mimo).
- [ ] Task 4.3: Update `scripts/evaluate_memory.py` (replace vyce/vyne with mimo).
- [ ] Task 4.4: Update `scripts/evaluate_routing.py` (replace vyce/vyne with mimo).
- [ ] Task 4.5: Update `scripts/memeval_latency_gate.py` (replace vyce/vyne with mimo).

### Phase 5: Test Migration
- [ ] Task 5.1: Create `tests/unit/integrations/llm/test_mimo.py` and delete `test_vyce.py`.
- [ ] Task 5.2: Update `tests/unit/integrations/llm/test_provider_factory.py`.
- [ ] Task 5.3: Update `tests/unit/integrations/llm/test_classifiers.py`.
- [ ] Task 5.4: Update `tests/unit/integrations/llm/test_generators.py`.
- [ ] Task 5.5: Update `tests/unit/integrations/llm/test_chat_reply.py`.
- [ ] Task 5.6: Update `tests/unit/scripts/test_evaluate_memory_provider.py`.
- [ ] Task 5.7: Update `tests/integration/email_action_plan/test_workflow.py`.

### Phase 6: Verification & Sign-off
- [ ] Task 6.1: Run `uv run ruff check .`
- [ ] Task 6.2: Run `uv run mypy src`
- [ ] Task 6.3: Run `uv run pytest -q -n 4 --dist loadfile`
