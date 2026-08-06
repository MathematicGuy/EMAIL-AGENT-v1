# Coding Agent Guidelines

## EXPERIENCE

This section stores durable reasoning lessons, not session history.

### Admission Rules

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

### Distilled Experience Registry

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
