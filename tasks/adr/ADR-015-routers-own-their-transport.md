# ADR-015 — Routers own their transport; `app.py` is only a composition root

- Status: Accepted (candidate 03 complete: slices 03-1…03-3c)
- Date: 2026-08-25
- Decision makers: Product/Engineering team
- Relates to: `src/cowork_agent/app.py`, `src/cowork_agent/api/`; partners [ADR-013](ADR-013-composition-as-typed-value.md)

## Context

`app.py` was 1581 lines and the repository's hottest file (83 commits). It held
two unrelated jobs: composing the runtime, and serving 34 HTTP handlers
declared inline inside `create_app` as closures over that composition. Chat,
project, report and evaluation routes had already been extracted into
`api/`; everything else — knowledge, raw documents, digest runs, mailbox
OAuth, connections — had not.

Handlers-as-closures is what made the file hard to split: a closure can read
anything in `create_app`'s scope, so nothing recorded which state a handler
actually needed. ADR-013 removed the reason for that coupling by making the
composed runtime one typed value read through `runtime(request)`.

## Decision

**Every route lives in a router module under `api/`. `app.py` keeps only
`create_app`, its `lifespan`, `/health`, the router mounts, the chat
controller factory, and `main`.**

Four new modules, one per subject, each a `create_*_router()` factory
following the `api/reports.py` shape (module-level helpers, handlers as
closures of the factory):

| Module | Subject |
|---|---|
| `api/knowledge.py` | document-health, the knowledge corpus reads, raw-document CRUD |
| `api/digest_runs.py` | digest run creation, polling, results, tasks; the `/v1/conversations` legacy stub |
| `api/mailboxes.py` | Gmail/Outlook OAuth handshakes, connections CRUD |
| `api/dependencies.py` | the request-scoped seams more than one router needs |

`api/dependencies.py` has an admission rule, stated in its docstring, so it
does not become a junk drawer: a helper moves there when a **second** router
needs it, and stays in a router's own module until then. That is why
`_digest_worker`, `_mailbox`, `_gmail_settings` and the payload shapers are
still private to their routers.

Two prefix conventions coexist and both are correct. A router whose routes
share a prefix declares it once (`api/reports.py`, `api/chat.py`). A router
whose subject spans prefixes — `api/knowledge.py` covers
`/v1/cowork/chat/document-health`, `/v1/mail-todo/knowledge/*` and
`/api/v1/raw-documents/*` — declares none and each handler carries its full
path, as `api/evaluation_jobs.py` already did. Subject, not URL shape, decides
which module a route lives in.

## Rationale

- **The seam was already there.** After ADR-013 every handler reached its
  state through `runtime(request)`, so no handler still needed to be a closure
  over `create_app`. Moving them out was mechanical, and the route table
  proves it: byte-identical, 63 routes, at every one of the six commits.
- **Deletion test.** Each router module can be deleted and its subject
  disappears cleanly — no other module reaches into it. `api/dependencies.py`
  is the one module shared by several, and its interface is nine functions
  over a `Request`.
- **Locality.** A helper now sits next to its only caller. `_run_history_item`
  is in `digest_runs.py`, `_frontend_mail_redirect` in `mailboxes.py`,
  `_resolve_raw_document` in `knowledge.py`. Previously all three were in one
  600-line trailer of private functions at the bottom of `app.py`, and nothing
  said which routes used which.

## Consequences

- `app.py` 1581 → 507 lines; four modules totalling ~1230 lines carry the
  routes and the helpers that belong to them.
- **The last untyped `app.state` fallback is gone.** ADR-013 kept a self-heal
  memo in `_raw_document_repo` because a no-lifespan test could reach a
  handler with no runtime assembled. It now reads
  `control_plane_required(request).raw_document_repository` like every other
  group, which also makes its return type concrete instead of `Any` and
  retires the `hasattr(repo, "delete")` guard the `Any` had forced. A test
  that writes composes a runtime instead of setting an app-state key. The two
  remaining survivors are the ones ADR-013 sanctioned: the `chat_controllers`
  request-time cache and `chat_controller_factory`.
- **One session read per request, not three.** `owned_connection` resolves the
  caller and the handler it returns to resolved the caller again;
  `authenticated_principal` and `authenticated_chat_principal` now memoize the
  resolution on `request.state`, one key per session store. Only successes are
  cached — caching a miss would let a `required=False` probe poison a later
  required read.
- **A route-safety invariant was silently dead and is now live.**
  `test_no_route_accepts_caller_provided_identity` scanned `app.routes` for
  `APIRoute` instances, but this FastAPI leaves a lazy `_IncludedRouter` proxy
  there rather than copying an included router's routes onto the app. The scan
  therefore only ever saw handlers declared inline on `create_app` — 9 of 63 —
  so every chat, project, report and evaluation route had gone unchecked since
  its extraction. The test now flattens the proxies before filtering and
  asserts a floor on the count. Anything else that reasons about the app's
  routes must flatten the same way; `app.routes` alone is not the route table.

## Notes for the next reader

The route-table oracle used throughout candidate 03 is worth rebuilding if
`app.py` or `api/` is restructured again: dump `(path, methods)` for every
route reachable through `original_router`, before and after, and diff. It
catches a dropped decorator, a changed prefix and a lost HTTP method in one
check, which the test suite does not — most of these routes have no direct
test.
