# ADR-013 — Composition is a typed value (`CoworkRuntime`), not `app.state`

- Status: Accepted (in progress — finalized at roadmap slice 02-8)
- Date: 2026-08-25
- Decision makers: Product/Engineering team
- Relates to: composition root in `app.py`; partners [ADR-001](ADR-001-async-pipeline-and-adapters.md)

## Context

Everything the application is made of is constructed inside one `lifespan`
closure nested in `create_app()` and published as ~60 untyped
`app.state.<key>` attributes. There is no interface describing what the app is
composed of: the contract is whatever attribute a consumer happens to ask for.
Every consumer defends at request time with
`getattr(request.app.state, "x", None)` plus `cast(Any, ...)`, so a missing or
renamed dependency surfaces as `None` at runtime instead of at boot or compile
time, and finding who depends on what means grepping `app.state` across the
whole tree.

## Decision

Composition becomes one typed value: a frozen
`@dataclass(frozen=True, slots=True) class CoworkRuntime` in
`src/cowork_agent/composition.py`, constructed once in `lifespan` and published
as the single `app.state.runtime` attribute. Consumers read it through a plain
accessor, `runtime(request) -> CoworkRuntime`.

1. **Accessor, not FastAPI `Depends`.** The SSE chat stream runs per-token and
   must pay zero dependency-injection overhead; a plain function call is one
   attribute read. It also keeps the seam deep: one module owns the entire
   `app.state.runtime` contract, so renaming or regrouping fields touches one
   place.
2. **WHERE-not-WHAT migration invariant.** Each slice (02-1…02-8) moves only
   *where* a consumer reads a dependency from — an `app.state` key becomes a
   typed runtime field — and never changes *what* is composed, in what order,
   or with what settings. Behavior-neutral by construction; ordering
   invariants (e.g. the report store composed before any credentialed
   settings read) survive untouched.
3. **Two request-time caches stay outside the frozen runtime.**
   `chat_controllers` (per-principal controller cache) and the raw-document
   SQLite fallback repository are created lazily on first request and written
   back to `app.state`; they are request-time growth, not boot-time
   composition, and a frozen value cannot hold them. They keep their
   `app.state` homes until a later decision says otherwise.
4. **Slice 02-1 ships the skeleton only**: `CoworkRuntime(reports=...)` and
   the accessor, with `app.state.report_store` kept as a thin forward. Later
   slices add the group fields `control_plane`, `mailbox`, `chat`,
   `email_rag`, and `evaluation: ... | None` (evaluation is optional by
   settings and is the one field allowed to be absent).

## Rationale

- **Deletion test.** Delete `CoworkRuntime` and ~60 optional-attribute
  contracts — each defended by `getattr(..., None)` at every caller — come
  back. The value earns its existence by being the one place those contracts
  live.
- **Leverage.** A missing or mis-typed dependency becomes a mypy error at the
  composition root, where the fix is one line, instead of a `None` discovered
  inside a request handler.
- **AI-navigability.** One dataclass enumerates the whole object graph a
  reader (human or agent) must understand; no more grepping `app.state` across
  files to reconstruct what the app is made of.
- **Partners ADR-001.** ADR-001 chose ports/adapters so Gmail and LLM
  adapters stay swappable; this decision gives the composition root the same
  typing discipline the ports already have.

## Consequences

- `composition.py` is the only module allowed to know that the runtime lives
  on `app.state`; consumers import `runtime(request)`.
- Old `app.state.<key>` attributes remain as thin forwards while their
  consumers migrate slice by slice; a forward is deleted when its last reader
  moves.
- Test doubles inject `app.state.runtime` directly instead of sprinkling
  individual keys.
- Final state, removal of the last forwards, and the doc updates land at slice
  02-8, when this ADR is finalized.
