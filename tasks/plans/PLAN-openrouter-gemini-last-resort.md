# Implementation Plan: OpenRouter → Google Gemini last-resort

Confirmed by interview. Runtime LLM stays on OpenRouter (`OPENROUTER_MODEL`, then native `models[]` from other `OPENROUTER_ALLOWED_MODELS` slugs). If that call is down or returns unusable/non-JSON, retry the same request on Google Gemini (`GEMINI_MODEL` + key rotation). Schema-invalid JSON after OpenRouter’s existing repair retry does not hop. Both-fail keeps today’s conservative errors. Eval scripts stay pinned.

See session plan for the full task graph. Wave 1: T1 config parse + T3 last_resort helper (file-disjoint). Wave 2: T2 `models[]`. Wave 3: T4 email hop + T5 chat hop. Wave 4: T6 composition. Wave 5: T7 ADR/docs.

Hop only on `OpenRouterAPIError`. Do not wrap ports. Always `uv run`. TDD. No migrations, corpus, or RAG bootstrap changes.
