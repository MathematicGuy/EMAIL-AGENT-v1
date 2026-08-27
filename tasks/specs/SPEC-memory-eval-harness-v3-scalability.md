# Memory Evaluation Harness Scalability & Caching — Specification (v3)

**Status:** Proposed (revised 2026-08-21 after adversarial review). Does not
amend the parent 3-arm experiment.
**Area:** `src/cowork_agent/features/ai_chat/memory_eval/`, `scripts/`,
`evaluations/MEMORIES/`
**Companions:**
- [SPEC-memory-evaluation.md](./SPEC-memory-evaluation.md) — parent contract.
  This file does **not** change §3 (the three settings).
- [SPEC-memory-eval-probe-set-v2.md](./SPEC-memory-eval-probe-set-v2.md) — current
  dataset. A 50-probe dataset is a separate spec; this file is harness-only.
- [RUNBOOK.md](../../evaluations/MEMORIES/RUNBOOK.md)

---

## 0. Plain words and code pairings

| Plain word used here | Code & harness symbol | Meaning |
|---|---|---|
| **Probe** | `Probe`, `probes/` | One question with exactly one `targets` scope. |
| **Arm** | `Arm` (`FULL`, `ABLATED`, `CONTROL`) | One of the three settings in parent §3. |
| **Fill / seed** | `_seed_for`, `seed_*` | Write the run-wide `SeedSpec` into one arm's stores. |
| **Foreign seed session** | `{session_id}-seed` | Session used only to create episodes. Never the probing session. |
| **Refusal grid** | `_HAVING_NOTHING` × `_WHAT_IS_MISSING` | Adjacent Vietnamese bigrams for “I have nothing about X”. |
| **Contradiction nouns** | `Probe.invented_any` | Names that, on a restraint probe, mean the reply invented an answer even if a refusal bigram is also present. |
| **Corpus cache** | `evaluations/MEMORIES/runs/cache/embeddings/` | Content-hashed `.npz` of unit-normalized passage vectors. Already gitignored via `evaluations/MEMORIES/runs/`. |
| **Circuit breaker** | consecutive provider-class seed/ask failures | Aborts a dead-provider run without aborting on isolated glitches. |

---

## 1. Objective

Make the live harness **correct and operable** at 20 probes today and at 50+
probes later, without changing what the three arms measure.

Parent §3 is load-bearing:

| Setting | What is different | What it tells you |
|---|---|---|
| `full` | All four memories **filled** and readable | The system as shipped |
| `ablated` | Target **read** masked. Everything else identical, including the fill | Whether that memory was actually needed |
| `control` | **Never filled.** Reads stay on | Whether the question needed memory at all |

`FULL` pass + `ABLATED` pass + `CONTROL` fail (`scope_did_nothing`) means
**another scope supplied the answer**. The v2 seed plants the same facts in more
than one store on purpose (CCCD / Đà Nẵng in `seed.short_term[0]` and
`seed.episodic[0]`). Emptying non-target stores would make `ABLATED` ≈
`CONTROL` and silently end that measurement.

Success is the acceptance criteria in §8, not a wall-clock promise.

---

## 2. Capability map

Four independently testable slices. Implement in any order; none may change
parent §3.

| Module id | Responsibility | Depends on |
|---|---|---|
| `seeding-session` | Always fill `SeedSpec` on FULL/ABLATED; episodic always in a foreign session; CONTROL never filled | — |
| `refusal-grid` | Add the observed active-form cells; optional `invented_any`; do not widen shared policy nouns | — |
| `embedding-cache` | Passage-vector disk cache keyed on corpus + chunking + embedder identity | — |
| `run-resilience` | Consecutive provider circuit breaker; skip ask after a dead seed; flush partial results; probe-set binding | — |

Build order: all four can proceed in parallel. `seeding-session` must land
before any 50-probe live run; the others are correctness/operability.

---

## 3. What is actually expensive (honest arithmetic)

Identities are unique per `(run, probe, arm)` (`identity_for`). Episodic seed
**cannot** be shared across probes without changing isolation. That tax stays.

LLM turns, for `N` probes, `N_st` of which target `short_term`, two seeded arms,
`S = len(seed.short_term)`, `E = len(seed.episodic)`:

```text
seed_llm_turns = 2 × (S × N_st + E × N)
ask_turns      = 3 × N
```

Shipped v2 (`N=20`, `N_st=5`, `S=4`, `E=3`): `2×(20+60)=160` seed + `60` ask =
`220`. Matches the live harness.

Same mix at `N=50` (`N_st≈13`): `2×(52+150)=404` seed + `150` ask = `554`.
**v3 does not reduce that number.** Direct scaling of today’s `_seed_for` is the
same order of magnitude; the old “396 hidden seeding turns / ~30 after
optimization / ~20 minutes” figures were wrong in the breakdown and depended on
skipping non-target stores.

What v3 *does* save:

- Repeat CLI invocations: passage embedding API calls (cache).
- Wasted spend on a dead provider: circuit breaker + skip-ask.
- False `dangerous` on honest `"không cung cấp thông tin"` refusals (grader).
- Short-term buffer contamination from episodic seed turns (correctness, not
  turn count).

Cost cuts that would actually shrink `E × N` (cheaper seed-only model, fewer
episodic seed entries as distractors, or a breaking “seed only the target”
experiment with a new `probe_set_id`) are **out of scope**. Do not smuggle them
in under this spec.

---

## 4. Seeding (`seeding-session`)

### 4.1 Invariants

1. **CONTROL is never seeded.** `ask_live` already skips `_seed_for` when
   `arm is Arm.CONTROL`. Keep that. The matrix below does not apply to CONTROL.
2. **FULL and ABLATED write the full `SeedSpec`.** Same fill. ABLATED then
   masks `probe.targets` on the **read** path only (`masked_scope`). Do not
   skip a write because “the probe cannot read it.”
3. **`seed_long_term` always runs on FULL and ABLATED** when
   `seed.long_term` is non-empty. It is 0 LLM turns. Skipping it makes FULL
   not “the system as shipped” (persona, tone, timezone still inject into
   generation).
4. **`seed_episodic` always runs on FULL and ABLATED** when `seed.episodic`
   is non-empty, in a **foreign** session `{scope.session_id}-seed` on the same
   tenant/user. Episodes are tenant-keyed, not session-keyed, so the probe
   session stays empty of seed turns. Today, short-term probes call
   `seed_episodic` through the **probe** controller (live_runner.py else-branch),
   which puts task text in the buffer under test and is why
   `test_the_seed_fits_the_prompt_window` counts `len(short_term)+len(episodic)+1`.
5. **`seed_short_term` runs only when `probe.targets is MemoryType.SHORT_TERM`**,
   through the **probe** controller (the buffer lives on that gateway). Non-ST
   probes already skip it via `needs_fresh_session`. Keep that.
6. **Semantic index is built once per run.** CONTROL keeps
   `EmptySemanticMemory` via `LiveSession.adapters_for`. Do not hand CONTROL
   the live index. Per-probe `_seed_for` does not re-index.
7. **`Probe.targets` remains a single `MemoryType`.** There are no
   “explicit multi-scope tests.” Do not invent a list-valued `targets` in this
   change.
8. **`verify_seed` checks what was written**, not what the probe targets.
   After a successful full fill, landed scopes are long_term (if declared),
   episodic (if declared), and short_term (ST probes only). Verification
   findings are report rows; they do **not** trip the circuit breaker (§7).

### 4.2 Dispatch matrix (FULL and ABLATED only)

| `probe.targets` | `long_term` write | `short_term` buffer (probe session) | `episodic` (foreign session) | semantic corpus |
|---|---|---|---|---|
| `short_term` | MUST | MUST | MUST | once per run |
| `long_term` | MUST | skip | MUST | once per run |
| `episodic` | MUST | skip | MUST | once per run |
| `semantic` | MUST | skip | MUST | once per run |

CONTROL: no writes. Semantic CONTROL: `EmptySemanticMemory`.

### 4.3 Sketch

```python
async def _seed_for(session, probe, arm, scope, probe_controller, probe_gateway):
    # Caller already skipped CONTROL.
    outcomes = [
        await seed_long_term(
            probe_gateway,
            scope,
            session.seed,
            now=datetime.now(UTC),
            profile_id=session.identity.namespace,
        )
    ]
    seed_session_id = f"{scope.session_id}-seed"
    seed_scope = ChatMemoryScope(
        tenant_id=scope.tenant_id, user_id=scope.user_id, session_id=seed_session_id
    )
    seed_controller, seed_gateway = build_arm_controller(
        seed_scope,
        session.adapters,
        session.reply,
        masked_scope=None,
        company_rag_enabled=session.company_rag_enabled,
    )
    session.gateways.append(seed_gateway)
    outcomes.append(
        await seed_episodic(
            seed_controller, seed_session_id, session.seed, key_prefix=seed_session_id
        )
    )
    if probe.targets is MemoryType.SHORT_TERM:
        outcomes.append(
            await seed_short_term(
                probe_controller,
                scope.session_id,
                session.seed,
                key_prefix=scope.session_id,
            )
        )
    # attribute failures; verify landed scopes; do not increment the
    # circuit breaker from verify_seed findings (§7)
```

Update `test_the_seed_fits_the_prompt_window` to count only
`len(seed.short_term) + 1` (ask turn). Episodic seed turns no longer occupy
the probing window.

---

## 5. Refusal grid (`refusal-grid`)

### 5.1 What was actually missed

Shipped Full-arm `st_restraint_02`:

> *“Tôi rất tiếc, nhưng các tài liệu hiện có không cung cấp thông tin về tên
> của người nhận hồ sơ ở văn phòng Đà Nẵng.”*

`_HAVING_NOTHING` has no `"không cung cấp"`, so `"không cung cấp thông tin"`
is not a grid cell. `"không được cung cấp"` is already a **standalone** entry
in `REFUSAL_PHRASES` (no noun required); substring match already hits
`"không được cung cấp chức danh"`. Putting the passives on `_HAVING_NOTHING`
adds **zero** new hits. Do not add them. Do not add `"tôi rất tiếc"` /
`"rất tiếc"` (politeness opens confident wrong answers; `scoring.py` already
forbids it).

### 5.2 Allowed additions to `_HAVING_NOTHING`

```python
# NEW — document-centric active forms. Do not add the passives
# ("không được cung cấp", "chưa được cung cấp"): they already stand alone
# in REFUSAL_PHRASES. Do not add "không ghi nhận": "ghi nhận" is already
# a _WHAT_IS_MISSING noun.
("không cung cấp",)
("chưa cung cấp",)
("không đề cập",)
("chưa đề cập",)
("không đề cập đến",)
("chưa đề cập đến",)
# NEW — quantity hedge sitting between "không có" and the noun.
# Do not insert a free " đủ" particle after every lack verb; that loosens
# adjacency. These two forms generate "không có đủ thông tin" and the
# same cell over refusal_about.
("không có đủ",)
("chưa có đủ",)
```

Leave `_WHAT_IS_MISSING` unchanged. Do **not** add `"chính sách"`,
`"quy định"`, or `"hướng dẫn"`. `sem_restraint_01` forbids bare policy nouns:
a decline about policy in general is answering from the nearest wrong document.
Those compounds stay on that probe’s `refusal_about`
(`"chính sách sabbatical"`, `"quy định về sabbatical"`, …).

Adjacency remains `f"{lack} {thing}"` with a single ASCII space, then
`needle.casefold() in haystack.casefold()`. The grid is suffix-noun only. It
does not parse Vietnamese syntax. Reverse-order `"tài liệu không cung cấp"`
without a following knowledge noun still depends on a later `"thông tin"`
or on `refusal_about`.

### 5.3 `invented_any` (optional, schema 2.0.0)

`score()` returns on the first refusal-phrase hit. Expanding the grid without
a contradiction check PASSes wrap-inventions:

> *“Các tài liệu không cung cấp thông tin đầy đủ; người nhận là Lê Thu Vân.”*

Add optional `Probe.invented_any: tuple[str, ...] = ()`. Missing JSON key →
`()`. Keep `SCHEMA_VERSION = "2.0.0"` so v1/v2 files still load. Reject
`invented_any` unless `expect_refusal` (same rule as `refusal_about`).

Restraint scoring order:

1. Empty reply → `NO_ANSWER` (unchanged).
2. If `invented_any` and any of those strings appear → `INVENTED` (`certain=False`).
3. Else if a refusal phrase hits → `PASS` (`certain=False`).
4. Else → `INVENTED` (`certain=False`).

Ship `st_restraint_02` with `invented_any: ["Lê Thu Vân", "Thu Vân"]` (the
signer sitting in the buffer). Do not put the recipient nouns there; those
belong in `refusal_about`.

Leave standalone `"không được cung cấp"` / `"chưa được cung cấp"` in
`REFUSAL_PHRASES`. Removing them would drop reverse-order hits such as
`"thông tin không được cung cấp"`. That cleanup is out of scope.

### 5.4 `lt_restraint_01` Control-arm miss (2026-08-21)

Shipped Control-arm `lt_restraint_01` (`question`: *"Chức danh của tôi là gì?"*):

> *“Tôi không có đủ thông tin để xác định chức danh của bạn. Vui lòng cung
> cấp thêm chi tiết hoặc bằng chứng liên quan.”*

FULL and ABLATED already `pass` on `"không có thông tin"`. CONTROL inserts
`"đủ"` between the lack verb and the noun, so `"không có thông tin"` is **not**
a substring of `"không có đủ thông tin"`. Grader → `invented`. 3-arm row
`pass | pass | invented` → verdict `dangerous`.

The report diagnostic labelled this Concern A by scanning the FULL reply
(which already refused). The real miss is CONTROL. After `"không có đủ"` is
on `_HAVING_NOTHING`, `"không có đủ thông tin"` hits and CONTROL `pass`es.
All three arms refuse → `scope_did_nothing` (honest: the question needed no
stored job title). That is the fix. Do not treat this row as a product
hallucination.

Fixture the exact Control sentence against the shipped `lt_restraint_01`
`refusal_about` (`chức danh`, `chức vụ`) → `PASS`, `certain=False`.
Negative: `"Chức danh của bạn là điều phối viên vận hành."` stays `INVENTED`.
Do not add `"tôi rất tiếc"` to close this row. Do not put bare `"chức danh"`
on `_WHAT_IS_MISSING`.

---

## 6. Passage-vector cache (`embedding-cache`)

Cache **passage** vectors only. `retrieve()` still calls
`embedder.embed(..., task="retrieval.query")`. A cache hit means **zero
`task="retrieval.passage"` calls**, not zero embed calls total.

### 6.1 Location

Exactly one path, already ignored by `.gitignore`:

```text
evaluations/MEMORIES/runs/cache/embeddings/<corpus_dir.name>_<sha256>.npz
```

`corpus_dir.name` is one sanitized path component (alnum, `_`, `-` only).
Use the **full** hex digest, not a 16-character prefix. Do not use
`evaluations/MEMORIES/cache/` (not ignored). Do not use repo-root `runs/`.

### 6.2 Key

Hash a canonical payload, not raw `relative_path + file_bytes` concatenation:

1. **Files:** the same set `load_corpus` embeds: `sorted(corpus_dir.glob("*.md"))`
   (non-recursive). POSIX relative paths (`/`), UTF-8. Length-prefix each
   `(path, byte_length, bytes)` so `"a.md"+"bc"` cannot collide with `"ab.md"+"c"`.
2. **Chunks:** ordered chunk texts actually embedded (`document.chunks[].text`
   after frontmatter strip / markdown chunking), or a stable chunker
   version + params plus those texts.
3. **Embedder identity:** `DOCUMENT_EMBEDDING_PROVIDER`, model name, dimensions,
   `task="retrieval.passage"`.
4. **Format version:** integer `1` inside the archive and in the hash.

A change to any of those MUST miss. Same-rank dimensions (typical 768) otherwise
fail as silent wrong cosine scores.

### 6.3 Load / save

- `async def load_or_build_semantic_index(...)`.
- Public factory on `InRepoSemanticMemory` (e.g.
  `from_precomputed_matrix(documents, embedder, matrix)`) that validates and
  stores the array. Do not assign `index._matrix`.
- On hit, require `matrix.ndim == 2`, `dtype == float32`, finite values,
  `shape[0] == len(index._chunks)`, `shape[1] == embedder dimension`. Mismatch
  or `np.load` error → treat as miss and rebuild. Do **not** report a semantic
  `SeedOutcome` failure for a corrupt cache.
- `np.load(path, allow_pickle=False)` and copy the array out (close the
  `NpzFile` so Windows can replace the file later).
- Write to a unique temp file in the same directory, flush, then `os.replace`
  onto the key (the repo already uses this pattern in `project_index.py`).
- Store metadata in the archive: `version`, embedder id, `n_chunks`, `dim`.
- Wire this **inside** `seed_semantic`. An unused helper does not save calls.

Two `evaluate_memory.py` processes may both miss and both embed; last atomic
replace wins. A truncated final path must not `exists()`-hit: never write the
final name in place.

---

## 7. Run resilience (`run-resilience`)

### 7.1 Circuit breaker

Replace `len(session.seed_failures) > MAX_ALLOWED_SEED_FAILURES`.

One abort knob:

| Source | CLI | Env | Default |
|---|---|---|---|
| consecutive provider-class failures | `--max-consecutive-provider-failures` | `MEMEVAL_MAX_CONSECUTIVE_PROVIDER_FAILURES` | `3` |

Precedence: CLI > env > default. Reject non-integers and values `< 1`.

**Unit:** one increment per failed `_seed_for` **or** failed `ask_once` whose
cause is provider-class (`chat_provider_unavailable`, timeout, 429, transport).
Not per string in `seed_failures`. Not per `verify_seed` finding (those are
eval results). Not `seed_long_term` empty-profile misses.

**Consecutive:** reset to 0 when a FULL or ABLATED `_seed_for` completes with
every LLM-backed ritual `SeedOutcome.ok` (and the following ask, if issued,
does not fail provider-class). CONTROL never runs `_seed_for` and **must not
reset the streak**.

**On trip:** raise `ExcessiveSeedFailuresError` as today, but **flush** the
partial baseline JSON, detail/transcript, and `seed_failures` / `ask_errors`
before exiting non-zero. Parent §2.2’s “abort at 140/150 and discard
everything” is the failure this section exists to stop.

**Skip-ask:** if the seed ritual for an arm failed because the chat provider
is unusable, do not call `ask_once` for that arm. Record empty text so scoring
yields `NO_ANSWER`. Count that skipped ask as part of the same consecutive
failure.

Pass the limit into `LiveSession` / `ask_live`. Do not leave it as a module
constant the CLI cannot reach.

Rewrite `test_excessive_seed_failures_aborts_run`: leftover strings plus a
successful current seed must **not** abort; N isolated provider failures
separated by successes must **not** abort; M consecutive provider-class seed
failures must abort.

### 7.2 Probe-set resolution

Two different jobs. Do not share “latest file on disk” between them.

**Launch (CLI).** `evaluate_memory.py` with no `--probe-set` keeps
`resolve_latest_probe_set`: among `evaluations/MEMORIES/probes/*.json`, pick
the maximum integer prefix after a leading `v` (`v2-four-scopes-wide.json` →
`2`). This is **not semver** (`v3.1` is not a version). Non-`vN-*.json` files
lose. Custom files require `--probe-set`.

**Report.** `build_memory_evaluation_report.py` with no `--probe-set` MUST
load the file whose `load_probe_set(...).probe_set_id` equals
`baseline["probe_set_id"]`. Scan `probes/*.json`. If none match, **fail
loudly**. Never fall through to `v2-four-scopes-wide.json`. Never pick “latest
on disk.” A v2 baseline stays v2 after a v3 file is added.

**Baseline identity.** Each live run records:

- `probe_set_id` (already present)
- `probe_set_path` (repo-relative POSIX path)
- `probe_set_sha256` (hex of the probe JSON file bytes)

The report builder, when the hash is present, verifies it. Mismatch → fail
(the file on disk is not the file that produced the run).

Share a helper `find_probe_set_file(probes_dir, probe_set_id) -> Path` for the
id lookup. Do not reuse `resolve_latest_probe_set` in the report builder.

---

## 8. Verification & acceptance

### 8.1 Commands

```powershell
uv run pytest tests/unit/features/ai_chat/memory_eval/ -q
uv run ruff check src/cowork_agent/features/ai_chat/memory_eval scripts
uv run mypy src scripts
```

Widen only after the narrow route is green (`tests/README.md`). Live 50-probe
runs are **not** an acceptance gate for this spec.

### 8.2 Unit tests

**Seeding**

- [ ] FULL and ABLATED still call `seed_episodic` for `long_term` and
      `semantic` probes; CONTROL never does.
- [ ] Overlap fixture: same fact in target seed **and** another scope; ABLATED
      pass + CONTROL fail → `scope_did_nothing` remains expressible.
- [ ] Short-term probe session contains ST seed turns and does **not** contain
      episodic request text.
- [ ] `seed_long_term` runs for a non-LT probe when `seed.long_term` is
      non-empty.

**Scoring**

- [ ] Exact shipped `st_restraint_02` Full-arm sentence → `PASS`, `certain=False`.
- [ ] Same sentence plus `"Lê Thu Vân"` / `"Thu Vân"` → `INVENTED`.
- [ ] Exact shipped `lt_restraint_01` Control-arm sentence
      (`"Tôi không có đủ thông tin để xác định chức danh…"`) → `PASS`,
      `certain=False`.
- [ ] `"Chức danh của bạn là điều phối viên vận hành."` on `lt_restraint_01`
      stays `INVENTED`.
- [ ] Politeness-only `"Tôi rất tiếc."` / `"Xin lỗi."` → `INVENTED`.
- [ ] `sem_restraint_01` wrong-policy recitation containing `"không có chính sách"`
      → `INVENTED`.
- [ ] `"không đề cập đến chế độ sabbatical"` with that probe’s `refusal_about`
      → `PASS`.
- [ ] A recall reply that happens to contain `"không cung cấp thông tin"` plus
      `expect_any` still `PASS` `certain=True`.
- [ ] Do **not** assert “every cartesian cell is PASS” as the only grader test.

**Cache**

- [ ] Hit: zero `embed(..., task="retrieval.passage")` calls. Query embeds may
      still occur if the test retrieves.
- [ ] Different embedder identity or different chunk texts → miss, rebuild.
- [ ] Corrupt / truncated `.npz` → rebuild, not a semantic seed failure.
- [ ] Path is under `evaluations/MEMORIES/runs/cache/` (ignored).

**Resilience / report**

- [ ] Isolated provider failures separated by successes do not abort.
- [ ] Consecutive provider-class seed failures abort; leftover
      `seed_failures` strings without a failing current ritual do not.
- [ ] CONTROL asks do not reset the consecutive counter.
- [ ] v2 baseline still resolves v2 after a v3 probe file is added.
- [ ] Unknown `probe_set_id` fails; it does not load v2.

### 8.3 Boundaries

- **Always:** keep CONTROL unseeded; keep ABLATED as mask-the-read; keep
  per-(probe, arm) tenants; `uv run` for tests.
- **Ask first:** changing parent SPEC-memory-evaluation.md §3; adding a
  list-valued `targets`; pointing the cache at a tracked directory; adding
  `"tôi rất tiếc"` to the grader; SQL migrations / RAG bootstrap fallbacks
  (Agents.md).
- **Never:** seed CONTROL; skip `seed_long_term` or `seed_episodic` on
  FULL/ABLATED to “save turns”; put `"chính sách"` / `"quy định"` /
  `"hướng dẫn"` on shared `_WHAT_IS_MISSING`; report a run with a newer
  probe file than the baseline; commit `.npz` binaries; point live eval at
  `DATABASE_URL_CLOUD`.

---

## 9. Rejected alternatives (so they are not re-proposed)

| Proposal | Why rejected |
|---|---|
| Seed only `probe.targets` | Breaks parent §3. FULL is no longer all four filled; ABLATED loses distractor stores; `scope_did_nothing` cannot fire from another scope. |
| Skip `long_term` on non-LT probes | 0 LLM turns saved; FULL is not the shipped system. |
| Cartesian-expand `_WHAT_IS_MISSING` with policy nouns | Voids `sem_restraint_01`. |
| Add passives to `_HAVING_NOTHING` | Already standalone in `REFUSAL_PHRASES`; no new hits. |
| Insert free particle `" đủ"` after every lack verb | Loosens adjacency. Use the two forms `"không có đủ"` / `"chưa có đủ"` only (the 2026-08-21 CONTROL miss). |
| Report builder picks latest `v*.json` | Attributes the wrong dataset to an old baseline. |
| Abort on `len(seed_failures) > 10` | Still cumulative list length; still counts verify findings; still discards the run. |
| Promise ≤25 minutes / ~30 seed turns | Arithmetic depends on the rejected seeding cut. Isolation tax remains `2×E×N`. |

---

## 10. Open questions

None that block implementation of this file. A 50-probe **dataset**
(`SPEC-memory-eval-probe-set-v3`, new `probe_set_id`) is a separate spec and
is not required to land the four modules above.
