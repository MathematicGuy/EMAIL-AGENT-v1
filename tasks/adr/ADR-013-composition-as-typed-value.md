# ADR-013 — Composition is a typed value (`CoworkRuntime`), not `app.state`

- Status: Accepted (candidate 02 complete: slices 02-1…02-8)
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
3. **Request-time writes stay outside the frozen runtime.** The
   `chat_controllers` per-session controller cache and the raw-document
   SQLite fallback repository memo are created lazily on first request and
   written back to `app.state`; they are request-time growth, not boot-time
   composition, and a frozen value cannot hold them. They keep their
   `app.state` homes as documented survivors.
4. **Slice 02-1 ships the skeleton only**: `CoworkRuntime(reports=...)` and
   the accessor, with `app.state.report_store` kept as a thin forward. Later
   slices add the group fields `control_plane`, `mailbox`, `chat`,
   `email_rag`, and `evaluation: ... | None` (evaluation is optional by
   settings and is the one field allowed to be absent).

## Final shape (slice 02-8)

Candidate 02 is complete. `composition.py` is the composition module:
`CoworkRuntime` plus the groups `ControlPlane`, `MailboxRuntime`,
`ChatRuntime`, `EmailRagRuntime`, and `EvaluationBundle`, built by
`build_control_plane`, `build_mailbox`, `build_chat`, `build_email_rag`
(with `upgrade_email_rag_providers` / `degrade_email_rag` for the coupled
provider half) and `build_evaluation`. `lifespan` in `app.py` now shrinks
to the corpus-skill startup side-effect, the settings reads, the group
builders, the provider upgrade sequence through plain locals, one
`app.state.runtime = CoworkRuntime(...)` assembly, and a teardown that
reads its handles back from the assembled runtime — evaluation runtime
first, then the control-plane pool, then the storage client, in exactly
the old order.

Slice 02-8 grep-proved every legacy forward dead and deleted it. The only
`app.state` keys that survive, all documented:

- `runtime` — the one assignment; the whole contract of this ADR.
- `chat_controllers` — the request-time controller cache (point 3).
- `raw_document_repository` — the self-heal memo fallback for the
  no-lifespan path (point 3); request-time readers go through the
  control-plane group first.
- `chat_controller_factory` — published once after the single assembly;
  the chat router's request-time cache reads it there, and the factory
  itself reads the assembled runtime at controller-creation time.

Two last strays also died here: `gmail_settings` gained a home on the
mailbox group so its forward could be deleted, and the always-`None`
`project_document_store` / `project_document_queue` keys were deleted
outright — the deletion test showed nothing read them, so removing them
concentrated meaning rather than relocating it. `redis_client` and
`run_queue` stay `| None` fields on `ControlPlane`: always `None` in this
process, but read through the group by the document-health and digest-run
routes to choose their degrade behavior.

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
- The old `app.state.<key>` forwards are gone: each was deleted when its
  last reader moved behind `runtime(request)` (slice 02-8 removed the
  remainder), leaving only the documented survivors above.
- Test doubles inject `app.state.runtime` directly instead of sprinkling
  individual keys; the integration tests that seeded data through forwards
  now read the same handles through the composed groups.
