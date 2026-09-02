# ADR-012 — OpenRouter native `models[]`, then Google Gemini last-resort

- Status: Accepted
- Date: 2026-08-20
- Decision makers: Product/Engineering team
- Relates to: runtime LLM composition in `app.py` / `orchestration/worker.py`

## Context

`LLM_PROVIDER` is exclusive (`gemini | groq | faucet | openrouter`). OpenRouter
sent a single `model`. `OPENROUTER_ALLOWED_MODELS` existed in `.env` but was
never read. Classifier transport failures became conservative `RETRIEVE_RAG`;
chat failures became `ChatReplyUnavailable`. There was no hop to the already
configured Gemini Google API + key rotation.

## Decision

When `LLM_PROVIDER=openrouter`:

1. One OpenRouter HTTP call uses `model=OPENROUTER_MODEL` and, when present,
   OpenRouter’s native `models[]` from the other `OPENROUTER_ALLOWED_MODELS`
   slugs (primary de-duplicated, list order preserved).
   Source: https://openrouter.ai/docs/guides/routing/model-fallbacks
2. If that call raises `OpenRouterAPIError` (timeout, connect, HTTP 429/5xx,
   empty/non-JSON), retry the same prompt/schema on Google Gemini
   (`GEMINI_MODEL` + numbered key rotation).
3. Well-formed JSON that fails our schema after the existing OpenRouter repair
   retry does **not** hop.
4. If Gemini keys are absent, OpenRouter-only. If keys are present but invalid,
   boot fails. Both-fail keeps today’s conservative errors.
5. Surfaces: email classify, action-plan generate, chat reply, chat intent.
   Eval scripts stay pinned.

Hop lives inside each OpenRouter `_complete` / `complete()` callback, not at
the port. Wrapping `classify` / `stream_reply` would hop on schema-invalid
output.

## Consequences

- `OpenRouterSettings.allowed_models` / `fallback_models()` and optional
  `last_resort: GeminiSettings | None` on the four OpenRouter adapters.
- Do not implement sequential per-model HTTP retries in-app; OpenRouter owns
  that hop. Do not put a Gemini slug in `OPENROUTER_ALLOWED_MODELS`.
