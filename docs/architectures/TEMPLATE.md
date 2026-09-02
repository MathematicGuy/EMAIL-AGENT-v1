---
c4_level: index
view_key: null
diagram: null
owns: docs/architectures
status: implemented
last_verified: 2026-08-27
---

# Architecture Document Template

Copy everything below the rule into a new file named after its view key
(`c1-*.md`, `c2-*.md`, `c3-*.md`). Fill every section. Delete a section only when the
"Omit when" note applies — never renumber the ones that remain.

The rules that govern this template live in [README.md](README.md). The short version:
the diagram comes from [`workspace.dsl`](workspace.dsl), the prose comes from you, and
they are never allowed to disagree.

---

```markdown
---
c4_level: 3
view_key: c3-example
diagram: diagrams/structurizr-c3-example.png
owns: src/cowork_agent/features/example
status: implemented
last_verified: YYYY-MM-DD
---

# <Element name> — <C4 level name>

<!--
One paragraph, 2-4 sentences. What this element is for, and the single most
important constraint on it. No history, no roadmap, no "we plan to".
-->

![<view description>](diagrams/structurizr-c3-example.png)

> Generated from [`workspace.dsl`](workspace.dsl), view `c3-example`.
> Do not edit the image or its `.puml`; see [README §4](README.md#4-regenerating-the-diagrams).

---

## 1. Responsibilities

<!--
What this element is accountable for, as a short list. Each line is a capability,
not an implementation note. 3-7 lines. If you need more, the element is too big
and probably wants splitting in the model.
-->

- …

## 2. Elements

<!--
One row per element in the diagram. "Source of truth" is a link to the code that
owns the behaviour — the reader must be able to go from the box to the file.
Every element in the view appears here; every row appears in the view.
-->

| Element | Responsibility | Source of truth |
|---|---|---|
| **…** | … | [`path.py`](../../src/…) |

## 3. Interfaces

<!--
The contract other elements depend on: HTTP routes, ports/protocols, CLI flags,
or the typed port set. Omit when the element exposes no interface of its own
(rare — a store still has a schema).
-->

| Interface | Shape | Notes |
|---|---|---|
| `…` | … | … |

## 4. Invariants

<!--
The rules that must not be broken, each with the thing that enforces it. This is
the section a reviewer reads. An invariant with no enforcement point is a wish —
either find the enforcement or delete the line.
-->

| Invariant | Enforced by |
|---|---|
| … | [`path.py`](../../src/…) / [ADR-0NN](../../tasks/adr/…) |

## 5. Failure and degradation

<!--
What happens when a dependency is unavailable. One row per failure, with the
observable behaviour — not "handles errors gracefully". Omit when the element has
no external dependency and cannot degrade.
-->

| Failure | Behaviour |
|---|---|
| … | … |

## 6. Known gaps

<!--
Required when status is `partial`; otherwise state "None." explicitly rather than
deleting the section, so a reader can tell the difference between "nothing to
report" and "nobody checked". Describe live code that this document does not
cover, or a place where the model is coarser than reality. Not a roadmap.
-->

None.

## 7. Related

<!-- Adjacent views first, then ADRs, then specs. -->

- …
```

---

## Writing rules

**Say what is, in the present tense.** "The classifier resolves five routes." Not
"will resolve", not "was changed to resolve". If it is not running on `dev`, it does
not belong in this directory.

**Every claim is traceable.** A statement about behaviour carries a link to the file
that implements it or the ADR that decided it. A claim with no anchor is the first
thing to rot.

**Name the flag.** Anything conditional states its environment variable and its
default: `` `CHAT_COMPANY_RAG_ENABLED` (default `false`) ``. A reader must be able to
tell whether the path is live without opening `config.py`.

**No status theatre.** No "✅ Fully Aligned", no "0 Diff", no per-document alignment
matrix. Those tracked a target document that no longer exists, and they aged badly
because nothing forced them to be true.

**Prefer a table to a paragraph** for anything enumerable, and a link to a summary for
anything the code already states precisely.

**Keep a Level 3 document under ~150 lines.** Past that, the container is doing too
much or the document is duplicating the code.
