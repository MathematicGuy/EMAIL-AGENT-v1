# Memory Evaluation

Measures whether each of our four memory scopes holds what was put in it, drops
superseded values, refuses to invent, and cannot leak across tenants — with
every result attributable to exactly one scope.

Read [SPEC.md](./SPEC.md) for the design and [PLAN.md](./PLAN.md) for the build.

## Run it

```powershell
# Mechanics only. No key, no database, scripted replies.
python scripts/evaluate_memory.py --dry-run
```

A dry run validates that the harness works. It measures nothing about the real
system and must never be used to make a decision.

## How to read a report

Every probe produces three outcomes and one verdict.

| verdict | means | what to do |
|---|---|---|
| `dangerous` | some arm asserted a superseded answer or invented one | Fix first. This is the headline. |
| `broken` | the scope did not deliver even with everything enabled | The scope is not working; check the seed landed. |
| `leaked` | the control arm passed | Not a memory probe. Rewrite it or drop it. |
| `scope_did_nothing` | right answer, but the ablated arm passed too | The answer came from elsewhere; the probe is mis-targeted. |
| `scope_earned_it` | only the full arm passed | The scope is doing its job. |

Rows are sorted worst-first, so the top of the table is where to look.

## The three arms

| arm | what changes |
|---|---|
| `full` | nothing — all four scopes seeded and readable |
| `ablated` | the probe's target scope is masked out of the read |
| `control` | **the seed is skipped** — all scopes enabled, store empty |

`control` disables the seed, **not** the read. A probe the model can answer from
its training data will pass under `control`, and that is exactly the signal —
without it, such a probe would look like a memory success.

## Rules

- **Committed reports are metadata-only.** Case ids, counts, verdicts, timings,
  model identifiers. No questions, no replies, no seed text. A unit test
  enforces this.
- **`runs/` is gitignored.** Full replies live there for debugging.
- **Two reports are comparable only at the same `probe_set_id` and
  `schema_version`.**
- **Exit code 0 means the harness ran**, not that memory is good.

## Known limitation: semantic tenancy

The company RAG corpus has no tenant partition. `KnowledgeChunk` carries no
tenant field, `allowed_chunk_indices` filters only on document id, year and
month, and `load_corpus(corpus_dir, *, tenant_id)` accepts a `tenant_id` it
never reads. Company knowledge is corpus-wide by design — `delete_all_memory`
documents that it never touches company RAG.

The isolation probe therefore targets `long_term`, where isolation is real and
enforced in SQL. A semantic isolation probe would report a leak on every run
that describes the store's design rather than a regression, which is worse than
having no probe at all.

## Known gap: `lt_isolation_01` reports `broken`

Read this before reading a live report. `RunIdentity` carries a foreign tenant
and user, but **nothing writes the foreign profile yet**. So `lt_isolation_01`
asks for material that was never seeded and gets a refusal from an empty store,
not from real isolation — it proves nothing about tenancy in either direction
and must not be read as a passing isolation check.

The full list of what is and is not covered is in
[PLAN-LIVE.md](PLAN-LIVE.md#open-work-after-task-10): foreign-tenant seeding,
semantic tenancy, `write_chat_summary` having no production caller, the launch
gate staying out of CI, and the live tier never yet having run end to end.
