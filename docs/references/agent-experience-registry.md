# Agent Experience Registry

This document stores durable reasoning lessons, not session history. It was
relocated from `AGENTS.md` so the root guide carries only always-needed
operating constraints.

## Admission Rules

Add an experience only when all of these are true:

1. **Real:** It comes from an observed event in this repository or workflow, not speculation.
2. **Evidenced:** A command result, test, diff, commit, review finding, or explicit user correction supports it.
3. **Compressed:** Its seed is fewer than 12 words and cannot be shortened without losing the lesson.
4. **Generative:** It guides more than the incident that produced it.
5. **Falsifiable:** The entry names what concretely fails when the lesson is ignored.
6. **Decompressible:** Another agent can recover the intended reasoning without reading the original session.
7. **Novel:** It does not duplicate an existing rule, seed, or repository authority.

Before adding any seed, compare it with the lessons observed in the current session and ask: **“If I could save only three experiences from this session, would this be one, and why?”** Admit no more than the session's top three; admitting fewer or none is expected.

Use this schema:

```markdown
#### "Seed under 12 words"
- **Pattern:** Reusable reasoning rule.
- **Evidence:** Durable proof or a concise description of the verified event.
- **Failure state:** Specific breakage caused by ignoring the rule.
- **Deploy when:** Situations where the rule should activate.
```

Do not save:

- status updates, task summaries, plans, or handoff content;
- guesses, unverified impressions, or lessons inferred only from an agent's claim;
- user preferences that belong in explicit project rules;
- secrets, credentials, personal data, transient process IDs, or disposable paths;
- verbose incident narratives, generic advice, or tool-specific trivia with no reusable pattern.

The registry may grow across sessions. Keep it append-only except when removing an exact duplicate or an entry whose evidence is proven false. Before adding anything, search the registry and merge lessons with the same underlying invariant. If a later seed expresses a distinct improvement, preserve the older entry and mark it `Superseded by: "new seed"` and or remove the old seed.

## Distilled Experience Registry

#### "Test narrow, prove broad"
- **Pattern:** Match review and test scope to risk: use quick supervisor checks and the smallest deterministic test first; expand only when a failure, relevant change, or coupled contract leaves risk unproven.
- **Evidence:** Repeated broad reviews delayed the plan, while focused assertions found defects quickly and risk-directed regression checks supplied confidence without rerunning every related test.
- **Failure state:** Low-risk work stalls behind redundant gates, high-risk integration defects receive shallow review, or verification expands without a concrete risk.
- **Deploy when:** Choosing review depth, handling feedback, or planning the focused-to-broader verification sequence.

#### "Fresh passing evidence needs no echo"
- **Pattern:** Track successful test commands and their covered surfaces. While those surfaces remain unchanged, run only tests implicated by new edits; do not repeat a passing command merely as another gate. If later work fails, rerun the last passing scope after the fix to confirm recovery.
- **Evidence:** Agents reran tests that had passed one or two minutes earlier without intervening changes, adding delay but no new confidence.
- **Failure state:** Redundant test runs slow implementation, obscure which change introduced a failure, and consume review time without increasing evidence.
- **Deploy when:** Iterating through red-green-refactor cycles, applying review feedback, or selecting the next verification command within one session.

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
