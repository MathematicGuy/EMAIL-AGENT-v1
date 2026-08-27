# SPEC: Deepen LLM Provider Module Architecture

**Status:** Ready for Implementation  
**Author:** Antigravity (conversation `2f4ef23f-012e-4412-9967-d4b3594479b8`)  
**Date:** 2026-08-23  
**Skills:** `codebase-design` (deep modules), `code-simplifier`

---

## 1. Problem Statement

The Email Action Plan provider layer has **shallow modules** with high duplication. Each
of the 4 providers (Gemini, Vyce, Mistral, OpenRouter) independently implements:

- HTTP transport (`_post_json`) — 3 copies
- JSON extraction (`_completion_json`) — 3 copies
- Request body formatting (`_request_body`) — 3 copies
- Batch classification workflow (`classify` + `_classify_batch`) — 4 copies
- Action plan generation workflow (`generate`) — 4 copies
- Langfuse tracing helpers — 4 copies

Additionally, `vyce.py`, `mistral.py`, and `openrouter.py` import **14 private `_*`
symbols** from `gemini.py`, creating leaky coupling where `gemini.py` is simultaneously
a Gemini transport adapter AND a shared library.

The Chat layer already solved this with `_ConfiguredChatReply` and
`ConfiguredIntentClassifier` — deep base classes that accept a `Completion` closure.
Providers only supply transport.

---

## 2. Goal

Apply the same **deep module pattern** from the Chat layer to the Email Action Plan layer:

1. **Extract** shared prompts, schemas, and parsers from `gemini.py` into dedicated modules
2. **Introduce** `ConfiguredRouteClassifier` and `ConfiguredActionPlanGenerator` base classes
3. **Consolidate** duplicated HTTP transport into `openai_transport.py`
4. **Create** a provider factory to eliminate composition root duplication in `app.py` and `worker.py`
5. **Slim** each provider file to transport-only adapters implementing `_complete()`

---

## 3. Architecture Diagram

### Current (Shallow)

```
gemini.py (1261 lines) — Gemini adapter + ALL shared schemas/prompts/parsers
    ↑ 14 private imports each
vyce.py (422 lines) — duplicates batch loop, HTTP, JSON parsing
mistral.py (338 lines) — duplicates batch loop, HTTP, JSON parsing
openrouter.py (404 lines) — duplicates batch loop, HTTP, JSON parsing
```

### Target (Deep)

```
src/cowork_agent/integrations/llm/
├── providers/
│   ├── prompts.py          [NEW] Shared system instructions, schemas, formatters
│   ├── parsers.py          [NEW] Shared response parsing and validation
│   ├── openai_transport.py [NEW] Consolidated HTTP JSON transport
│   ├── base.py             [NEW] ConfiguredRouteClassifier, ConfiguredActionPlanGenerator
│   ├── tracing.py          [NEW] Langfuse helpers (_update_current_generation, etc.)
│   ├── gemini.py           [MODIFY] Gemini transport only (~500 lines, was 1261)
│   ├── vyce.py             [MODIFY] Vyce transport only (~120 lines, was 422)
│   ├── mistral.py          [MODIFY] Mistral transport only (~100 lines, was 338)
│   ├── openrouter.py       [MODIFY] OpenRouter transport only (~130 lines, was 404)
│   └── __init__.py         [MODIFY] Updated exports
├── provider_factory.py     [NEW] Unified provider resolution (replaces if/elif ladders)
├── chat_reply.py           [NO CHANGE] Already deep
├── chat_intent.py          [NO CHANGE] Already deep
└── __init__.py             [MODIFY] Updated exports
```

---

## 4. New Module Specifications

### 4.1 `providers/prompts.py`

**Responsibility:** All shared prompt templates, schemas, and formatters for the Email
Action Plan workflow. No provider-specific code. No HTTP. No transport.

**Symbols to extract from `gemini.py`:**

| Symbol | Current Location | Type |
|---|---|---|
| `EMAIL_INTENT_PROMPT_VERSION` | `gemini.py:62` | Constant |
| `CLASSIFIER_SYSTEM_INSTRUCTION` | `gemini.py:977` | Multi-line string constant |
| `GENERATOR_SYSTEM_INSTRUCTION` | `gemini.py:374` | Multi-line string constant |
| `CLASSIFICATION_SCHEMA` | `gemini.py:1015` | Dict constant |
| `GENERATION_SCHEMA` | `gemini.py:404` | Dict constant |
| `CLASSIFIER_REPAIR_INSTRUCTION` | `gemini.py:1086` | String constant |
| `GENERATOR_REPAIR_INSTRUCTION` | `gemini.py:1093` | String constant |
| `FALLBACK_ROUTE_DECISION` | `gemini.py:1122` | `EmailRouteDecision` instance |
| `QUERY_REWRITE_SYSTEM_INSTRUCTION` | `gemini.py:166` | String constant |
| `QUERY_REWRITE_SCHEMA` | `gemini.py:170` | Dict constant |
| `FILTERED_SUMMARY_SYSTEM_INSTRUCTION` | `gemini.py:1002` | String constant |
| `FILTERED_SUMMARY_SCHEMA` | `gemini.py:1007` | Dict constant |
| `_build_prompt()` | `gemini.py:516` | Function |
| `_build_generation_prompt()` | `gemini.py:548` | Function |
| `_format_datetime_tz()` | `gemini.py:499` | Function |

### 4.2 `providers/parsers.py`

**Responsibility:** All shared response parsing and validation logic. No transport.

**Symbols to extract from `gemini.py`:**

| Symbol | Current Location | Type |
|---|---|---|
| `_validated_decisions()` | `gemini.py:1135` | Function |
| `_parse_classification_payload()` | `gemini.py:1149` | Function |
| `_parse_route_decision()` | `gemini.py:1182` | Function |
| `_classified_messages_for()` | `gemini.py:1211` | Function |
| `_parse_action_plan_output()` | `gemini.py:613` | Function |
| `_generate_with_schema_repair()` | gemini.py | Function |
| `_task_source_links()` | `gemini.py:673` | Function |
| `_parse_plan_steps()` | `gemini.py:698` | Function |
| `_parse_supporting_documents()` | `gemini.py:719` | Function |
| `_require_str()` | `gemini.py:1226` | Function |
| `_require_optional_str()` | `gemini.py:1232` | Function |
| `_require_bool()` | `gemini.py:1238` | Function |
| `_require_sequence()` | `gemini.py:1244` | Function |
| `_require_str_tuple()` | `gemini.py:1250` | Function |
| `_require_confidence()` | `gemini.py:1254` | Function |

### 4.3 `providers/openai_transport.py`

**Responsibility:** Consolidated HTTP JSON transport for OpenAI-compatible APIs.
Used by Vyce, Mistral, and OpenRouter. Gemini uses the Google GenAI SDK.

**Public API:**

```python
def openai_request_body(
    model: str,
    system_instruction: str,
    user_prompt: str,
    schema: Mapping[str, object],
    max_output_tokens: int,
) -> dict[str, object]: ...


def openai_completion_json(
    response: Mapping[str, Any],
) -> Mapping[str, Any]: ...


def post_json(
    url: str,
    api_key: str,
    body: dict[str, object],
    timeout_seconds: int,
    *,
    extra_headers: Mapping[str, str] | None = None,
) -> Mapping[str, Any]: ...
```

**Consolidates from:**
- `vyce.py:_post_json` (L367), `vyce.py:_completion_json` (L326), `vyce.py:_request_body` (L281)
- `mistral.py:_post_json` (L302), `mistral.py:_completion_json` (L288), `mistral.py:_request_body` (L252)
- `openrouter.py:_post_json` (L370), `openrouter.py:_completion_json` (L356), `openrouter.py:_request_body` (L325)

**Provider-specific differences to handle:**
- **Vyce**: Instructor-style plain-text coercion in `_completion_json` when JSON fails — keep as an optional flag or let Vyce override
- **OpenRouter**: Extra headers (`HTTP-Referer`, `X-Title`) — use `extra_headers` parameter
- **OpenRouter**: Body includes `models` array for fallback — provider builds body, transport just posts

### 4.4 `providers/base.py`

**Responsibility:** Deep base classes that own all shared workflow logic.

```python
from abc import abstractmethod

Completion = Callable[[str], Awaitable[Mapping[str, Any] | None]]

class ConfiguredRouteClassifier:
    """Deep base: batch classification with repair retry.

    Subclasses implement _complete(prompt) -> payload | None.
    Base owns: batch loop, Langfuse tracing, repair retry, fallback filling.
    """

    def __init__(self, *, provider_name: str, max_emails_per_batch: int) -> None: ...

    @observe(as_type="span", name="classify-email-intent", ...)
    async def classify(self, user_timezone, current_time, messages) -> ClassificationResult:
        # ONE copy of the batch loop that's currently in 4 providers
        ...

    async def _classify_batch(self, user_timezone, current_time, threads, batch_ids):
        # ONE copy of the repair-retry logic
        ...

    @abstractmethod
    async def _complete(self, prompt: str) -> Mapping[str, Any] | None:
        """Provider-specific transport. Return parsed JSON or None on error."""
        ...


class ConfiguredActionPlanGenerator:
    """Deep base: action plan generation with schema repair.

    Subclasses implement _complete(prompt) -> payload.
    Base owns: prompt building, schema repair, output parsing, error wrapping.
    """

    def __init__(self, *, provider_name: str) -> None: ...

    async def generate(self, ...) -> ActionPlanOutput:
        # ONE copy of: build prompt → _generate_with_schema_repair → parse
        ...

    @abstractmethod
    async def _complete(self, prompt: str) -> Mapping[str, Any]:
        """Provider-specific transport. Return parsed JSON."""
        ...
```

### 4.5 `providers/tracing.py`

**Responsibility:** Langfuse tracing helpers used by all providers.

**Symbols to extract from `gemini.py`:**

| Symbol | Current Location |
|---|---|
| `_langfuse_configured()` | `gemini.py:67` |
| `_update_current_generation()` | `gemini.py:76` |
| `_update_current_span()` | `gemini.py:99` |

### 4.6 `provider_factory.py`

**Responsibility:** Unified provider resolution replacing duplicated `if/elif` ladders.

```python
@dataclass(frozen=True)
class EmailProviderBundle:
    classifier: RouteClassifierPort
    generator: ActionPlanGeneratorPort
    semantic_memory: SemanticMemoryPort
    query_rewriter: object | None  # GeminiRetrievalQueryRewriter or None
    generation_concurrency: int


@dataclass(frozen=True)
class ChatProviderBundle:
    intent_classifier: IntentClassifierPort
    chat_reply: ChatReplyPort


async def resolve_email_providers(provider: str) -> EmailProviderBundle: ...
def resolve_chat_providers(
    provider: str, intent_settings: ChatIntentSettings
) -> ChatProviderBundle: ...
```

**Replaces:**
- `app.py` lines 674–744 (80-line if/elif ladder)
- `worker.py` lines 134–176 (40-line if/elif ladder)

---

## 5. Provider File Changes (After Refactor)

### 5.1 `gemini.py` — Gemini Transport Only

**Keep:**
- `GeminiTransport` (Protocol)
- `GoogleGenAITransport` (Google GenAI SDK client)
- `GeminiKeyRotator` (key rotation wrapper)
- `GeminiRetrievalQueryRewriter`
- `GeminiRateLimitError`, `GenerationSchemaError`
- `GeminiRouteClassifier` → extends `ConfiguredRouteClassifier`, implements `_complete()`
- `GeminiActionPlanGenerator` → extends `ConfiguredActionPlanGenerator`, implements `_complete()`
- `_gemini_usage_details()`, `_mask_api_key()` (Gemini-specific helpers)

**Remove (moved to shared modules):**
- All `CLASSIFIER_*`, `GENERATOR_*`, `CLASSIFICATION_*`, `GENERATION_*` constants → `prompts.py`
- All `_build_*_prompt()`, `_format_datetime_tz()` → `prompts.py`
- All `_parse_*`, `_validated_*`, `_classified_*`, `_require_*` → `parsers.py`
- All `_update_current_*`, `_langfuse_configured` → `tracing.py`
- Batch loop in `classify()` → inherited from `ConfiguredRouteClassifier`
- Generation workflow in `generate()` → inherited from `ConfiguredActionPlanGenerator`

**Expected:** ~1261 → ~500 lines

### 5.2 `vyce.py` — Vyce Transport Only

**Keep:**
- `VyceAPIError`, `VyceRateLimitError`, `VyceGatewayError`
- `execute_chat_completion()` (key rotation logic)
- Vyne backward-compatibility aliases
- `VyceRouteClassifier` → extends `ConfiguredRouteClassifier`, implements `_complete()`
- `VyceActionPlanGenerator` → extends `ConfiguredActionPlanGenerator`, implements `_complete()`

**Remove:**
- `_post_json`, `_completion_json`, `_request_body` → `openai_transport.py`
- `classify()` batch loop → inherited
- `generate()` workflow → inherited
- All 14 `from .gemini import` private symbols → import from `prompts.py`, `parsers.py`

**Expected:** ~422 → ~120 lines

### 5.3 `mistral.py` — Mistral Transport Only

Same pattern. **Expected:** ~338 → ~100 lines

### 5.4 `openrouter.py` — OpenRouter Transport Only

Same pattern, keeps `complete_with_gemini_last_resort` integration.
**Expected:** ~404 → ~130 lines

---

## 6. Files Requiring Update (Complete Inventory)

### Source Code — Direct Changes

| File | Action | Summary |
|---|---|---|
| `src/cowork_agent/integrations/llm/providers/prompts.py` | **NEW** | Shared prompts, schemas, formatters |
| `src/cowork_agent/integrations/llm/providers/parsers.py` | **NEW** | Shared response parsing |
| `src/cowork_agent/integrations/llm/providers/openai_transport.py` | **NEW** | Consolidated HTTP transport |
| `src/cowork_agent/integrations/llm/providers/base.py` | **NEW** | Deep base classes |
| `src/cowork_agent/integrations/llm/providers/tracing.py` | **NEW** | Langfuse helpers |
| `src/cowork_agent/integrations/llm/provider_factory.py` | **NEW** | Unified provider resolution |
| `src/cowork_agent/integrations/llm/providers/gemini.py` | **MODIFY** | Remove shared code, extend base classes |
| `src/cowork_agent/integrations/llm/providers/vyce.py` | **MODIFY** | Remove duplicates, extend base classes |
| `src/cowork_agent/integrations/llm/providers/mistral.py` | **MODIFY** | Remove duplicates, extend base classes |
| `src/cowork_agent/integrations/llm/providers/openrouter.py` | **MODIFY** | Remove duplicates, extend base classes |
| `src/cowork_agent/integrations/llm/providers/__init__.py` | **MODIFY** | Update exports |
| `src/cowork_agent/integrations/llm/__init__.py` | **MODIFY** | Update exports |
| `src/cowork_agent/app.py` | **MODIFY** | Use provider_factory, remove if/elif ladder |
| `src/cowork_agent/orchestration/worker.py` | **MODIFY** | Use provider_factory, remove if/elif ladder |

### Test Files — Required Updates

| File | Action | Summary |
|---|---|---|
| `tests/unit/integrations/llm/test_vyce.py` | **MODIFY** | Update imports from new modules |
| `tests/unit/integrations/llm/test_classifiers.py` | **MODIFY** | Update imports, test shared base |
| `tests/unit/integrations/llm/test_generators.py` | **MODIFY** | Update imports, test shared base |
| `tests/unit/integrations/llm/test_chat_reply.py` | **NO CHANGE** | Chat layer unchanged |
| `tests/integration/email_action_plan/test_workflow.py` | **VERIFY** | Run to confirm wiring still works |
| Tests for new modules | **NEW** | `test_prompts.py`, `test_parsers.py`, `test_openai_transport.py`, `test_base.py` |

### Documentation — Required Updates

| File | What Changes |
|---|---|
| `AGENTS.md` | Update Layout section: add new files to tree |
| `docs/architectures/c3-api-email-action-plan.md` | Update Route Classifier & Action Plan Generation with new module structure |
| `docs/architectures/c2-containers.md` | Update External Providers table |
| `docs/observability/LANGFUSE.md` | Update provider file references (tracing.py, base.py) |
| `CONTEXT.md` | Update if LLM provider module layout is mentioned |

### Evaluation Scripts — Verify Only (no changes expected)

| File | Action |
|---|---|
| `scripts/evaluate_email_golden.py` | **VERIFY** — uses provider imports |
| `scripts/evaluate_routing.py` | **VERIFY** — uses provider imports |
| `scripts/evaluate_memory.py` | **VERIFY** — uses provider imports |
| `scripts/memeval_latency_gate.py` | **VERIFY** — uses provider imports |
| `scripts/evaluate_chat_routing.py` | **VERIFY** — uses provider imports |

---

## 7. Constraints & Rules

1. **Preserve all behavior** — this is a structural refactor only
2. **No new dependencies** — all code uses stdlib + existing deps
3. **Backward compatibility** — keep all existing public class names and aliases (Vyne*)
4. **Dependency direction**: `domain` ← `features` ← `integrations` ← `app`
5. **Always `uv run`** — never plain `python`
6. **Run full gate before completion:**
   ```bash
   uv run ruff check src tests scripts
   uv run mypy src
   uv run pytest -q
   ```

---

## 8. Verification Commands

```bash
# 1. Lint
uv run ruff check src tests scripts

# 2. Type check
uv run mypy src

# 3. Full test suite
uv run pytest -q

# 4. Verify no remaining private cross-imports from gemini
# (should return 0 results after refactor)
grep -rn "from .gemini import _" src/cowork_agent/integrations/llm/providers/

# 5. Verify provider wiring still works
# (manually test with LLM_PROVIDER=gemini, vyce, mistral, openrouter)
```

---

## 9. Expected Impact

| Metric | Before | After |
|---|---|---|
| Total provider lines | ~2425 | ~1150 |
| `_post_json` copies | 3 | 1 |
| `_completion_json` copies | 3 | 1 |
| `_request_body` copies | 3 | 1 |
| `classify()` batch loop copies | 4 | 1 |
| `generate()` workflow copies | 4 | 1 |
| Private cross-imports from gemini | 14 × 3 files | 0 |
| Composition root if/elif ladders | 2 | 1 |
| Lines to add a new provider | ~300 | ~50 |
