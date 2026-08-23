# PLAN: Deepen LLM Provider Module Architecture

**Status:** Approved for Implementation  
**Spec:** [`SPEC-deepen-llm-provider-architecture.md`](../specs/SPEC-deepen-llm-provider-architecture.md)  
**Author:** Antigravity (conversation `2f4ef23f-012e-4412-9967-d4b3594479b8`)  
**Date:** 2026-08-23  
**Skills:** `codebase-design` (deep modules), `code-simplifier`

---

## Overview

Restructure the Email Action Plan provider layer from **shallow, duplicated modules** to
**deep modules** following the proven pattern already used in the Chat layer
(`_ConfiguredChatReply` / `ConfiguredIntentClassifier`).

See the full spec for architecture diagrams, symbol inventories, and line-by-line
extraction lists.

---

## Implementation Order

Execute in dependency order. Each step should leave tests green.

### Step 1: Extract Shared Modules (no behavior change)

1. **`providers/tracing.py`** — Move `_langfuse_configured`, `_update_current_generation`,
   `_update_current_span` from `gemini.py`. Update all importers.
2. **`providers/prompts.py`** — Move all `*_SYSTEM_INSTRUCTION`, `*_SCHEMA`, `*_REPAIR_INSTRUCTION`,
   `FALLBACK_ROUTE_DECISION`, `_build_prompt`, `_build_generation_prompt`, `_format_datetime_tz`,
   `EMAIL_INTENT_PROMPT_VERSION` from `gemini.py`. Update all importers.
3. **`providers/parsers.py`** — Move all `_validated_decisions`, `_parse_*`, `_classified_messages_for`,
   `_generate_with_schema_repair`, `_task_source_links`, `_require_*` from `gemini.py`. Update all importers.

**Gate:** `uv run ruff check src tests scripts && uv run mypy src && uv run pytest -q`

### Step 2: Extract Shared HTTP Transport (no behavior change)

4. **`providers/openai_transport.py`** — Consolidate `_post_json`, `_completion_json`, `_request_body`
   from vyce.py, mistral.py, openrouter.py into one module. Update all three providers to import.

**Gate:** Same as Step 1.

### Step 3: Introduce Deep Base Classes

5. **`providers/base.py`** — Create `ConfiguredRouteClassifier` and `ConfiguredActionPlanGenerator`.
   The batch loop, repair retry, and generation workflow move here.
6. **Refactor each provider** to extend the base classes:
   - `gemini.py` → `GeminiRouteClassifier(ConfiguredRouteClassifier)`, implement `_complete()`
   - `vyce.py` → `VyceRouteClassifier(ConfiguredRouteClassifier)`, implement `_complete()`
   - `mistral.py` → `MistralRouteClassifier(ConfiguredRouteClassifier)`, implement `_complete()`
   - `openrouter.py` → `OpenRouterRouteClassifier(ConfiguredRouteClassifier)`, implement `_complete()`
   - Same for `*ActionPlanGenerator`.

**Gate:** Same.

### Step 4: Provider Factory

7. **`provider_factory.py`** — Create `resolve_email_providers()` and `resolve_chat_providers()`.
8. **`app.py`** — Replace 80-line if/elif ladder with factory call.
9. **`worker.py`** — Replace 40-line if/elif ladder with factory call.

**Gate:** Same.

### Step 5: Documentation & Cleanup

10. Update `AGENTS.md`, `01-email-action-plan-and-rag.md`, `04-overall-architecture.md`, `LANGFUSE.md`.
11. Update `providers/__init__.py` exports.
12. Verify no `from .gemini import _*` remain in vyce/mistral/openrouter.
13. Run evaluation scripts to verify provider wiring.

**Final Gate:**
```bash
uv run ruff check src tests scripts
uv run mypy src
uv run pytest -q
```

---

## Files to Change (Complete List)

### New Files (6)
- `src/cowork_agent/integrations/llm/providers/prompts.py`
- `src/cowork_agent/integrations/llm/providers/parsers.py`
- `src/cowork_agent/integrations/llm/providers/openai_transport.py`
- `src/cowork_agent/integrations/llm/providers/base.py`
- `src/cowork_agent/integrations/llm/providers/tracing.py`
- `src/cowork_agent/integrations/llm/provider_factory.py`

### Modified Source Files (8)
- `src/cowork_agent/integrations/llm/providers/gemini.py`
- `src/cowork_agent/integrations/llm/providers/vyce.py`
- `src/cowork_agent/integrations/llm/providers/mistral.py`
- `src/cowork_agent/integrations/llm/providers/openrouter.py`
- `src/cowork_agent/integrations/llm/providers/__init__.py`
- `src/cowork_agent/integrations/llm/__init__.py`
- `src/cowork_agent/app.py`
- `src/cowork_agent/orchestration/worker.py`

### Test Files
- `tests/unit/integrations/llm/test_vyce.py` — update imports
- `tests/unit/integrations/llm/test_classifiers.py` — update imports
- `tests/unit/integrations/llm/test_generators.py` — update imports
- New: `tests/unit/integrations/llm/test_openai_transport.py`
- New: `tests/unit/integrations/llm/test_parsers.py`

### Documentation
- `AGENTS.md` — Layout section
- `docs/architectures/current-architectures/01-email-action-plan-and-rag.md`
- `docs/architectures/current-architectures/04-overall-architecture.md`
- `docs/observability/LANGFUSE.md`
