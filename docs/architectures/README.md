---
c4_level: index
view_key: null
diagram: null
owns: docs/architectures
status: implemented
last_verified: 2026-08-27
---

# Architecture Harness

This directory documents **Cowork Agent as it is implemented**. There is no target
or aspirational architecture here — proposals live in `tasks/prds/` and `tasks/specs/`,
and decisions live in `tasks/adr/`.

This file is the harness, not prose. Read it before writing or reviewing anything
under `docs/architectures/`.

---

## 1. The one rule

> **[`workspace.dsl`](workspace.dsl) is the only place an element or a relationship is
> defined.** Markdown narrates the model; it never re-draws it.

Everything in [`diagrams/`](diagrams) is **generated output**. Editing a `.puml` or a
`.png` by hand produces a change that the next regeneration silently destroys, and a
diagram that disagrees with the model it claims to render. Do not do it.

Concretely:

| You want to… | Do this |
|---|---|
| Add / rename / retire a system, container or component | Edit `workspace.dsl`, then regenerate (§4) |
| Change what two elements say to each other | Edit the relationship in `workspace.dsl`, then regenerate |
| Add a new diagram | Add a `view` to `workspace.dsl`, regenerate, then add the matching Markdown doc from [`TEMPLATE.md`](TEMPLATE.md) |
| Explain *why* something is shaped that way | Edit the Markdown doc — or write an ADR in `tasks/adr/` and link it |
| Record a decision | ADR in `tasks/adr/`. Never here |

Mermaid is not used in this directory. A hand-drawn diagram is a second source of
truth, and the reason the previous version of these docs drifted.

---

## 2. Document index

One Markdown document per view. The file name is the view key.

### Level 1 — System Context

| Document | What it answers |
|---|---|
| [c1-system-context.md](c1-system-context.md) | Who uses Cowork Agent, and which external systems it depends on |

### Level 2 — Containers

| Document | What it answers |
|---|---|
| [c2-containers.md](c2-containers.md) | What is deployed and what stores state, plus the two end-to-end product flows |
| [deployment.md](deployment.md) | Where those containers run in the `local` and `cloud` modes |

### Level 3 — Components

| Document | Container | What it answers |
|---|---|---|
| [c3-api-email-action-plan.md](c3-api-email-action-plan.md) | Control Plane API | The single-turn, memory-free mail pipeline |
| [c3-api-ai-chat.md](c3-api-ai-chat.md) | Control Plane API | The multi-turn chat turn, typed memory, and the tool axis |
| [c3-api-retrieval.md](c3-api-retrieval.md) | Control Plane API | Hybrid retrieval over the company corpus and per-project indexes |
| [c3-api-platform.md](c3-api-platform.md) | Control Plane API | Composition, configuration, identity, persistence, observability |
| [c3-worker.md](c3-worker.md) | Background Worker | Out-of-process pollers, recovery and retention |
| [c3-ingestion-cli.md](c3-ingestion-cli.md) | Knowledge Ingestion CLI | Offline conversion of source documents into the committed corpus |

Level 4 (code) is deliberately not documented. The source is the code-level model;
a Level 4 diagram would be stale the day it is written.

---

## 3. The document contract

Every Markdown file in this directory starts with this frontmatter block. Every field
is required, and the values are checked by §5.

```yaml
---
c4_level: 1 | 2 | 3 | index          # which C4 level this document narrates
view_key: c3-api-ai-chat             # must match a view key in workspace.dsl
diagram: diagrams/structurizr-c3-api-ai-chat.png
owns: src/cowork_agent/features/ai_chat   # the source path this document is accountable for
status: implemented                  # implemented | partial | deprecated
last_verified: 2026-08-27            # ISO date the prose was last checked against the code
---
```

`status` describes the **document**, not an ambition:

- `implemented` — every element in the view exists in `src/` or `frontend/` today.
- `partial` — the view is accurate but the prose knowingly omits a live area; the gap
  must be named in §6 *Known gaps* of that document.
- `deprecated` — the code is gone; the document is awaiting deletion.

`also_narrates` is an optional list of extra view keys the same document covers. Use it
only when a second view is meaningless on its own — the two deployment topologies, or a
dynamic flow through containers already described. Every view in `workspace.dsl` must be
claimed by exactly one document, through `view_key` or `also_narrates`.

Section order is fixed by [`TEMPLATE.md`](TEMPLATE.md). Do not invent section
numbering per document — that is what made the previous docs unreadable.

---

## 4. Regenerating the diagrams

Requires Docker. Run from **this directory** (`docs/architectures/`).

Validate the model:

```bash
docker run --rm -v "$PWD:/usr/local/structurizr" structurizr/structurizr validate -workspace /usr/local/structurizr/workspace.dsl
```

Export the C4-PlantUML sources:

```bash
docker run --rm -v "$PWD:/usr/local/structurizr" structurizr/structurizr export -workspace /usr/local/structurizr/workspace.dsl -format plantuml/c4plantuml -output /usr/local/structurizr/diagrams
```

Render the PNGs (the size limit matters — the container view exceeds PlantUML's 4096px default):

```bash
docker run --rm -e PLANTUML_LIMIT_SIZE=16384 -v "$PWD/diagrams:/data" plantuml/plantuml -tpng /data/*.puml
```

On Windows Git Bash, prefix each command with `MSYS_NO_PATHCONV=1` and use
`"$(pwd -W)"` instead of `"$PWD"` so the bind mount resolves.

Commit `workspace.dsl`, the `.puml` files and the `.png` files together. A commit that
changes the DSL without the regenerated output is incomplete.

---

## 5. Verification

Before opening a PR that touches `docs/architectures/`:

1. **The model parses.** `structurizr validate` exits `0`.
2. **The diagrams are current.** Re-export and re-render; `git status` shows no
   unexpected diff under `diagrams/`. A diff you did not intend means someone
   hand-edited generated output.
3. **Every `view_key` resolves.** Each document's `view_key` matches a view in
   `workspace.dsl`, and each view in `workspace.dsl` has exactly one document.
4. **Every link resolves.** No relative link in this directory 404s.
5. **Every `owns:` path exists.** The source path a document claims is still there.

Checks 3–5 are mechanical:

```bash
uv run python docs/architectures/check_docs.py
```

---

## 6. When code changes

Update this directory in the same PR as the code when a change:

- adds, removes or renames a **container** (a process, a store, a deployable);
- adds, removes or renames a **component** already named in a Level 3 view;
- changes **who talks to whom**, or over what protocol;
- adds or drops an **external dependency**;
- moves a **trust or privacy boundary** — what is persisted, what is sent off-box.

A change inside a component that alters none of the above needs no architecture edit.
Bump `last_verified` on the documents you touched.

---

## 7. Related

- Decisions: [`tasks/adr/`](../../tasks/adr)
- Product requirements: [`tasks/prds/`](../../tasks/prds)
- Specifications: [`tasks/specs/`](../../tasks/specs)
- Test harness: [`tests/README.md`](../../tests/README.md)
- Evaluation harness: [`evaluations/README.md`](../../evaluations/README.md)
