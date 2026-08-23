# mimo-v2.5-pro chat_reply trail

Progress tracker for the **MIMO** memory-eval series (`mimo-v2.5-pro`, probe set
`v4_four_scopes_hard`). Not comparable to the mistral v1–v4 ledgers.

Per-version hypothesis and scoreboard live in
[`evaluations/MEMORIES/prompt-versioning/chat_reply/`](../../../evaluations/MEMORIES/prompt-versioning/chat_reply/).
How to run: [`evaluations/MEMORIES/RUNBOOK.md`](../../../evaluations/MEMORIES/RUNBOOK.md).
Concerns A–D: RUNBOOK §5. Wrap-invention rule: SPEC-memory-evaluation §6.3
(`invented_any` before refusal; do not add grader patterns to swallow a near-miss).

---

## Current statement (2026-08-23)

The v5 eval **finished**. Mimo is up. Do not treat nonce `1177eb1e` as a dead API
or as a clean prompt scoreboard.

| Layer | What happened | What it is |
|---|---|---|
| Preflight `--provider mimo` (first try) | `getaddrinfo failed` for `token-plan-ams.xiaomimimo.com` | DNS blip. `nslookup` resolved; retry **PASS** (88 chars). |
| Preflight default (no `--provider`) | `mistral/mistral-medium-3-5` PASS | `.env` `LLM_PROVIDER`, not the eval target. Parallel runner defaults to `mimo`. |
| v5 parallel eval | 13.2 min, `aborted: false`, **0 unrecovered asks** | Run completed. SHA v5 `8cff0394…`. |
| 7 `seed_failures` | `chat_provider_unavailable` while **creating** episodic tasks | Transient seed flake (Concern C). Ask-path recovered 7 empty replies. |
| Recorded 8/20 pass, 0/10 restraint | Grader missed inverted refusals | Not a product collapse. Offline re-score of the same transcript: **16/20 pass, 8/10 restraint, 2 dangerous**. |

**Read the re-score, not the recorded 8/20.** Next spend is a **clean rerun** of
the same v5 prompt + the inverted-absence grader. Do not confirm nonce `1177eb1e`.

---

## Series identity

| | |
|---|---|
| Model | `mimo` / `mimo-v2.5-pro` |
| Probe set | pin `evaluations/MEMORIES/probes/v4-four-scopes-hard.json` (`v4_four_scopes_hard`) |
| Store | SQLite scratch, `POSTGRES_MODE=off` |
| Runner | `scripts/evaluate_memory_parallel.py` (`--max-retries` default 5; **never retry** `chat_response_invalid`) |
| Live prompt | `src/cowork_agent/integrations/llm/chat_reply.py` `_SYSTEM_INSTRUCTION` / `system_prompt_sha()` |
| Clean prior (read this, not v5 recorded) | nonce `c5333fe6`, SHA v4 `76be3b3d…`, 17/20 pass, 7/10 restraint, 3 dangerous wrap-invention |

`run_key` hashes `(probe_set_id, model, seed)`, **not** the prompt. v4 and v5 share
`3e8c1df9f3bb`. Bind reports with `--detail` to the nonce you mean.

---

## What has been fixed

| When | Commit | Concern | What |
|---|---|---|---|
| v2 prompt (shared SHA, earlier mistral cycle) | in `chat_reply.py` | D | Empty `citation_ids` when no project evidence — company chunk IDs were killing `sem_*` turns as `chat_response_invalid`. |
| v2 harness | same cycle | C | `ChatResponseInvalid` split from `ChatReplyUnavailable`. Parallel runner **never retries** contract-invalid. |
| v3 prompt | time-rule | D | Later `updated_at` replaces earlier episode **when answering**, not only on `task_proposal`. |
| v4 prompt | SHA `76be3b3d…` | D | Apply recency **silently** (v3 side-effect: model narrated the timestamp compare). |
| 2026-08-23 | `df3b72f` | C | `--max-retries` default **5**. |
| 2026-08-23 | `2d23924` | A mislabel | `diagnose_needs_reading_probe`: full `invented` + ablated/control `pass` = wrap-invention **Concern D**, not grader miss. |
| v5 prompt | `2b1ca13`, SHA `8cff0394…` | D | Refuse-means-stop: *when the asked fact is absent, that absence is the complete answer; write one statement and stop.* |
| 2026-08-23 | `29b3ef5` | A | Grader grid: `không có trong` / `không có trong các` + noun `ngữ cảnh`. Inverted *“Thông tin về X không có trong dữ liệu…”* is now a refusal. **Does not** pass wrap that names bait. |

v4 MIMO diagnosis (verified): three Needs Reading rows were wrap-invention
(`prompt_fault`), not Concern A. Retrieval worked (blind arms refused). Product
step was prompt v5, not the memory store, not more refusal regex.

v5 MIMO on those three:

| Probe | v4 (clean) | v5 re-score (dirty seeds) |
|---|---|---|
| `st_restraint_02` | refuse + **Lê Thu Vân / Mai Liên** → `dangerous` | one-line absence → `restraint_held` |
| `sem_restraint_02` | refuse + **450.000** → `dangerous` | still recites 450.000 → `dangerous` / `prompt_fault` |
| `sem_restraint_03` | IT process, no form-code bait → `dangerous` | still recites IT process → `dangerous` / `prompt_fault` |

---

## Open issues (do not mix)

1. **Same-chunk wrap (D / prompt)** — `sem_restraint_02` (domestic per-diem 450.000 on an overseas question), `sem_restraint_03` (IT replacement process on a form-code question). Ablated/control refuse. Hypothesis for v6: a missing-fact answer must not continue with other facts from the same evidence chunk. **Do not** add grader patterns for these rows (`invented_any` already catches 450.000).
2. **Persona vs system identity (D?, unconfirmed)** — `lt_recall_01` all three arms: “Cowork AI Chat Assistant” instead of stored `Hải Âu`. v4 earned this. Measure on a **clean** rerun before changing the prompt.
3. **Seed flake (C)** — 7 episodic creates `chat_provider_unavailable`. `ep_recall_01` full answered Đà Nẵng because seed 3 (Hải Phòng) never landed. Do not read that row as ranking failure.
4. **Report §4.2 on recorded outcomes** — auto-diagnosis still labels inverted refusals and wrap rows from the *pre-grader-fix* baseline. Trust transcript + re-score until a clean rerun.

---

## Artifacts

| Kind | Path |
|---|---|
| v4 ledger (MIMO) | `evaluations/MEMORIES/prompt-versioning/chat_reply/v4-2026-08-23-mimo-v2.5-pro.md` |
| v5 ledger (MIMO) | `evaluations/MEMORIES/prompt-versioning/chat_reply/v5-2026-08-23-mimo-v2.5-pro.md` |
| mistral v4 (blocked, **do not edit**) | `evaluations/MEMORIES/prompt-versioning/chat_reply/v4-2026-08-23.md` |
| v4 baseline / detail | `baselines/mimo-v2.5-pro-v4-parallel.json` · `runs/2026-08-23T14-40-32Z-v4_four_scopes_hard-detail.json` |
| v5 baseline / detail | `baselines/mimo-v2.5-pro-v5-parallel.json` · `runs/2026-08-23T15-53-26Z-v4_four_scopes_hard-detail.json` |
| v4 report (gitignored) | `evaluations/MEMORIES/reports/2026-08-23-v4_four_scopes_hard.md` |
| v5 report (gitignored) | `evaluations/MEMORIES/reports/2026-08-23-mimo-v2.5-pro-v5-parallel.md` |
| v4 triage (gitignored) | `evaluations/MEMORIES/runs/triage/3e8c1df9f3bb-c5333fe6/` |

Reports `*.md` and `runs/*-detail.json` are gitignored. Baselines may be locally
excluded (`.git/info/exclude`). They stay on disk.

---

## Next spend

1. Preflight **`--provider mimo`** (not the `.env` default). Retry once on DNS.
2. One parallel eval, **same v5 SHA**, pin `v4-four-scopes-hard.json`, `--max-retries 5`, `POSTGRES_MODE=off`, `RTK_DISABLED=1`, `uv run`.
3. If seed_failures > 0: **inconclusive**, do not confirm, do not change prompt.
4. If clean: compare to v4 `c5333fe6` and to the v5 **re-score**, never to recorded 8/20.
5. Confirm only if first clean run is worth the 60 calls.
6. v6 same-chunk refuse only if `sem_restraint_02` / `sem_restraint_03` still wrap.

Guardrails: never `MEMEVAL_ALLOW_REMOTE_POSTGRES=1`; never commit `.env` or
`runs/memeval-chat.db`; one live eval at a time.
