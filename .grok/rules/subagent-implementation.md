# Subagent implementation strategy

Project convention for EMAIL-AGENT-v1. Orchestrator owns the skill
lifecycle. Subagents execute one numbered task. They do not re-plan,
re-scope, or start the next module.

## Lifecycle (do not skip)

```
spec-driven-development
  → planning-and-task-breakdown   (tasks/plans/PLAN-*.md)
    → incremental-implementation + TDD per task
      → orchestrator verify (ruff, mypy, focused then full suite)
```

Subagents start only after the plan names file-disjoint tasks with
acceptance criteria. They do not write specs, capability maps, ADRs,
or the implementation plan.

## Orchestrator keeps

- Spec interpretation and scope gate
- Task graph, prompts, and wave timing
- `tests/README.md` route rows
- `uv run ruff` / `uv run mypy` / `uv run pytest -q`
- Diff review against the spec (reject year/category, Qdrant, corpus
  rewrite, project-plane extras unless the active module id says so)

## Subagents get

One module-id task, a closed file list, TDD (failing test first),
and `uv run pytest <focused path>`. Always `uv run` — never plain
`python -m`.

## Wave rules

- **Parallel** only when tasks share no write paths.
- **Sequential** when a later task imports the earlier contract.
- Isolation: shared workspace (`none`) when files are disjoint.
- Do not give a subagent: planning, spec edits, `data/extracted/*.md`
  rewrite, SQL migrations, RAG bootstrap fallbacks, or the full suite.

## Prompt shape

State the module id, in-scope files, out-of-scope list, exact public
names to import, and the focused test command. Name tests by behaviour.

## Evidence

Used for `document-loading` (2026-08-16): Wave 1 T1/T2/T3 in parallel,
Wave 2 T4/T5 after T1, orchestrator T6. Subagents stayed on their file
lists. Drift risk is highest when a child is asked to "finish the
feature" instead of one task.
