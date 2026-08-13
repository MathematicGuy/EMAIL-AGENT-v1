E:\VIN-INTERNSHIP\Other-Coding-Project\waku-agent\Waku-Agent-System-Design.md# Agent Experience Registry

This document stores durable reasoning lessons, not session history. It is grounded in the **MindSeeds Epistemic Compression Protocol**: distilling operational wisdom into high-density, load-bearing **Seeds (<12 words)** that give agents a coherent epistemic identity rather than relying on brittle, verbose rulebooks.

It was relocated from `AGENTS.md` so the root guide carries only always-needed operating constraints.

---

## Epistemic Compression & Admission Rules

Add an experience seed only when all of these invariants are satisfied:

1. **Real:** Derived from an observed event in this repository or workflow, not speculation.
2. **Evidenced:** Supported by a command result, test failure, diff, commit, review finding, or explicit user correction.
3. **Compressed (<12 words):** High-density, load-bearing phrasing stripped of fluff, decorative adjectives, and performative commentary.
4. **Generative:** Dictates reusable structural or reasoning relationships across multiple domains.
5. **Falsifiable:** Explicitly defines the concrete failure mode caused when the seed is ignored.
6. **Decompressible:** An agent can recover the intended reasoning chain unprompted without needing the original session context.
7. **The Aha Invariant:** Contact with a problem produces an immediate expansion of clarity.
8. **Novel:** Does not duplicate an existing rule, seed, or repository authority.

Before adding any seed, compare it with the lessons observed in the current session and ask: **“If I could save only three experiences from this session, would this be one, and why?”** Admit no more than the session's top three; admitting fewer or none is expected.

### Seed Schema

```markdown
#### "Seed under 12 words"
- **Version:** v1 (YYYY-MM-DD)
- **Pattern:** Reusable reasoning or architectural rule.
- **Evidence:** Durable proof or concise description of the verified event.
- **Failure state:** Specific breakage caused by ignoring the rule.
- **Deploy when:** Situations where the rule should activate.
```

### Registry Maintenance & Pruning Rules

The registry is append-only except when removing an exact duplicate, merging lessons with the same underlying invariant, or applying pressure pruning. If a later seed expresses a distinct improvement, preserve the older entry and mark it `Superseded by: "new seed"` or remove the stale entry.

#### Active Pruning & Pressure Survival Protocol

A seed is not permanent; it must continuously earn its place. A seed dies and is pruned if any of the following occur:

1. **Pressure Failure:** The seed fails to guide an agent correctly during an active task, or leads to erroneous decisions under real workflow stress (*"Say only what survives pressure"*).
2. **Evidence Invalidation:** Subsequent commits, architectural refactors, or test outcomes contradict or invalidate the seed's original evidence.
3. **Generative Decay:** The seed requires continuous explanation or verbose qualifiers to remain applicable (failing the *Aha Invariant*).
4. **Seismic Eviction:** During major system evolutions, seeds that are not true load-bearing invariants fail under pressure and are evicted rather than preserved as dead weight.


---

## Distilled Experience Registry

### 1. CogniSeeds (Epistemic Architecture & Reasoning)

#### "Test narrow, prove broad"
- **Version:** v1 (2026-08-07)
- **Pattern:** Match review and test scope to risk: use quick supervisor checks and the smallest deterministic test first; expand only when a failure, relevant change, or coupled contract leaves risk unproven.
- **Evidence:** Repeated broad reviews delayed the plan, while focused assertions found defects quickly and risk-directed regression checks supplied confidence without rerunning every related test.
- **Failure state:** Low-risk work stalls behind redundant gates, high-risk integration defects receive shallow review, or verification expands without a concrete risk.
- **Deploy when:** Choosing review depth, handling feedback, or planning the focused-to-broader verification sequence.

#### "Fresh passing evidence needs no echo"
- **Version:** v1 (2026-08-07)
- **Pattern:** Track successful test commands and their covered surfaces. While those surfaces remain unchanged, run only tests implicated by new edits; do not repeat a passing command merely as another gate. If later work fails, rerun the last passing scope after the fix to confirm recovery.
- **Evidence:** Agents reran tests that had passed one or two minutes earlier without intervening changes, adding delay but no new confidence.
- **Failure state:** Redundant test runs slow implementation, obscure which change introduced a failure, and consume review time without increasing evidence.
- **Deploy when:** Iterating through red-green-refactor cycles, applying review feedback, or selecting the next verification command within one session.

#### "Narrow once, reuse the narrowed name"
- **Version:** v1 (2026-08-08)
- **Pattern:** Under mypy strict, `isinstance(x.get(k), dict)` narrows nothing for a *second* `x.get(k)` call; assign `raw = x.get(k)` once, narrow the variable, then reuse it.
- **Evidence:** DEMO-A GUI: six `union-attr` errors on repeated `s_res.get("progress"/"error")` calls all cleared by introducing `raw_*` locals; `Sequence` extraction also needed a `not isinstance(str)` guard.
- **Failure state:** Strict type checks fail on dict-of-Any API payloads, or worse, a bare `str` payload is iterated character-by-character at runtime.
- **Deploy when:** Consuming loosely-typed JSON payloads (dict[str, Any]) in strict-typed code.

---

### 2. ArchSeeds (Structural Integrity & System Invariants)

#### "An ignore rule is only real when git confirms it"
- **Version:** v1 (2026-08-07)
- **Pattern:** After adding or inheriting a gitignore rule for generated artifacts, verify it with `git check-ignore <path>` and a clean `git status` before trusting it; treat artifact files appearing in commits as proof the rule never worked.
- **Evidence:** `.pytest-tmp/` was assumed ignored (handoff and intent), but the checked-in entry was a stale typo (`.pytest-adr003.pytest-tmp/`); dozens of temp test databases and a baseline JSON had been committed until discovered via `git status` during review.
- **Failure state:** Generated artifacts (temp DBs, caches, reports) silently enter version control, bloat history, and surface as confusing `D`/untracked noise in every later status check.
- **Deploy when:** Adding ignore rules, onboarding to a repo's ignore assumptions, or when temp artifacts appear in `git status` unexpectedly.

#### "Upsert links belong on the stable key, not the mutable id"
- **Version:** v2 (2026-08-07)
- **Pattern:** When a row is upserted on a business key but carries a producer-minted id that changes every write, key every association table on the stable business key; the mutable id can only decorate the current content.
- **Evidence:** V1-M4 T4.2: `task_run_links` first referenced `tasks.task_id`, but the generator mints a new `task_id` each run and the upsert overwrites it — earlier runs' links would dangle. Re-keying links on `task_key` (tenant:user:message:pipeline) fixed per-run result views (commit 50d9dfd).
- **Failure state:** After any upsert from a later run, earlier runs' result reads return empty or partial views even though their rows still exist.
- **Deploy when:** Designing idempotent persistence with per-producer links, run/task association tables, or any ON CONFLICT DO UPDATE schema.

#### "Read-time mappers must inherit the producer's deterministic order"
- **Version:** v2 (2026-08-07)
- **Pattern:** When mapping logic relocates from write time to read time, preserve the original input ordering (insertion order / link rowid) as the tie-break; never re-derive order from producer-minted random ids.
- **Evidence:** V1-M4 T4.2 review: ordering ties (equal priority+deadline) sorted by random `task_id` uuids, so `nextActions[:3]` membership could flip between reads; fixed with `ORDER BY task_run_links.rowid` and dict insertion order (commit 50d9dfd).
- **Failure state:** Frozen ordering contracts pass most runs yet intermittently flip on tie cases, producing non-reproducible legacy-shape diffs.
- **Deploy when:** Relocating serialization/mapping from write to read path, or adding ORDER BY over rows with random producer ids.

