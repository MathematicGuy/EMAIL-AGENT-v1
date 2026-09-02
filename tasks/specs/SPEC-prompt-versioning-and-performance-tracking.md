# Prompt Versioning and Prompt Performance Tracking — Specification

**Status:** Partially implemented — the diagnosis half is built and used; the versioning half is not.
**Area:** `evaluations/MEMORIES/prompt-versioning/`, `src/cowork_agent/integrations/llm/`, `src/cowork_agent/features/ai_chat/memory_eval/`
**Parent:** [SPEC-memory-evaluation.md](./SPEC-memory-evaluation.md)
**Companions:** [RUNBOOK.md](../../evaluations/MEMORIES/RUNBOOK.md), [REPORT_FORMAT.md](../../evaluations/MEMORIES/reports/REPORT_FORMAT.md)

## Status

Built first, deliberately: attributing a failure is what unblocks everything else, and it
needed no new infrastructure because the three-arm signal was already in every run.

| Piece | State | Where |
|---|---|---|
| Fault router — verdict + arms → fault class | **Built** | `memory_eval/fault.py`, 11 unit tests |
| Triage issue harness — run → one issue file per open-cause probe | **Built** | `scripts/triage_memory_evaluation.py` |
| Loop documentation and fault-class table | **Built** | `evaluations/MEMORIES/prompt-versioning/README.md` |
| Prompt registry and version identity | **Not built** | — |
| Prompt version recorded in baselines and report | **Not built** | — |
| Fault class shown in the report itself | **Not built** — the issue harness covers the need | — |
| Version ledger files and index | **Not built** — fields defined, nothing written yet | — |
| Langfuse trace metadata and score emission | **Not built** | — |

Tracker publishing is dropped: development is local, and specs live in `tasks/specs/`.
Langfuse remains available locally and is still the intended prompt store, but nothing in
the built pieces depends on it.

Sections marked **[ASSUMED]** were never confirmed out loud; contradicting one changes the
spec, not the code.

---

## Problem Statement

The memory evaluation produces a report per run (`evaluations/MEMORIES/reports/<date>-<probe-set>.md`)
that names, per probe, what went wrong. The 2026-08-21 `v3_four_scopes_hard` run is the
current best-model reference: 15/20 pass at the full arm, 8/20 `scope_earned_it`,
4 `dangerous`.

Reading that report, some failures are memory failures and some are prompt failures, and
today nothing separates them:

- `ep_recall_01` misses in all three arms. Retrieval never delivered the fact. Rewriting the
  system prompt cannot fix it.
- `ep_restraint_02` answers "Phạm Quốc Huy" to a question about the CCCD task while the ablated
  and control arms both correctly refuse. Memory delivered a neighbouring episode and the
  generation step failed to check that the episode was about a different task. That is a prompt
  failure, and it is fixable by prompt text.

Two further gaps follow from that:

1. **There is no version identity for the prompt.** `_SYSTEM_INSTRUCTION` in `chat_reply.py` is a
   module constant. A report says which provider and model produced it, and nothing about which
   prompt produced it. Two reports a week apart cannot be compared, because the prompt may have
   moved underneath them and no artifact records that it did.
2. **There is no place to write down what a prompt change was for.** Trying a prompt variant today
   means editing production code, committing it to be able to run it, and losing the hypothesis —
   the reason the change was expected to work — as soon as the run is over. Failed experiments
   land in git history as product changes.

The loop wanted: read the report, decide whether the diagnosis lies in the prompt or in memory,
and if it lies in the prompt, draft a new prompt version and rerun the evaluation against it —
without that loop being an exercise in manual bookkeeping, and without prompt experiments
polluting production code history.

## Solution

Three pieces, each small.

**A prompt registry with a version identity.** The system instruction stops being read directly
from a module constant at request-build time and is read through a registry that resolves a named
prompt slot to `(text, version)`. Langfuse Prompt Management is the source of truth for the slot
when configured; the current constant remains in code as the fallback, so production never depends
on a network call and unit tests never make one. Every reply carries the resolved version.

**A fault router in the evaluation.** The existing three-arm verdict already contains the
prompt-vs-memory signal; it is simply never stated. A pure function maps each probe's verdict and
arm outcomes to one of `prompt_fault`, `memory_fault`, `healthy`, or `inconclusive`, and the report
gains a section that lists the prompt-fault probes — the actionable worklist for a prompt revision.

**A prompt-version ledger.** One short Markdown file per prompt version in
`evaluations/MEMORIES/prompt-versioning/`, holding what Langfuse structurally cannot: the
hypothesis written *before* the run, the report evidence that motivated it, the links to the
Langfuse prompt version and the run, and the verdict — confirmed, refuted, or inconclusive. The
numbers themselves are never retyped into it; they are auto-filled from the run artifact or
linked.

The loop then reads: run eval → report → fault router says prompt or memory → if prompt, draft
version N+1 with a pre-registered hypothesis → approve the spend → rerun → ledger records verdict →
next hypothesis.

## User Stories

Stories 8–13, 22 and 32–38 are satisfied by what is built today. The rest wait on the
prompt registry and the ledger.

1. As an evaluation author, I want every eval run to record which system-prompt version produced
   it, so that two reports weeks apart are comparable rather than merely adjacent.
2. As an evaluation author, I want the prompt version recorded in the committed baseline metadata,
   so that the comparison survives `runs/` being gitignored.
3. As an evaluation author, I want the report to state the prompt version in its header next to
   provider and model, so that a reader never has to ask which prompt this was.
4. As a prompt author, I want to try a candidate prompt without committing it to production code,
   so that refuted experiments leave no trace in the product's history.
5. As a prompt author, I want the candidate prompt to be selectable at run time by label, so that
   rerunning the eval against a new version is a flag and not a code edit.
6. As a prompt author, I want the in-code prompt to remain the fallback, so that a Langfuse outage
   degrades the product to the last reviewed prompt rather than breaking it.
7. As a prompt author, I want unit tests never to fetch a prompt over the network, so that the
   suite stays offline and deterministic.
8. As a report reader, I want each failing probe labelled `prompt_fault` or `memory_fault`, so that
   I know whether to open the prompt or open retrieval.
9. As a report reader, I want the rule that produces that label written down and deterministic, so
   that the label is a derivation and not an opinion.
10. As a report reader, I want `broken` verdicts — a miss in all three arms — classified as memory
    faults, so that nobody rewrites the prompt to fix a retrieval hole.
11. As a report reader, I want `dangerous` verdicts where the ablated and control arms correctly
    refuse classified as prompt faults, so that the case where memory did its job and generation
    misused it is called by its name.
12. As a report reader, I want `scope_did_nothing` treated as a retrieval/probe-design question and
    not a prompt worklist item, so that the prompt worklist stays short and real.
13. As a report reader, I want an explicit `inconclusive` class, so that probes the rule cannot
    confidently attribute are not silently swept into either bucket.
14. As a prompt author, I want a ledger file per prompt version, so that each cycle is one
    reviewable artifact and one commit.
15. As a prompt author, I want the ledger to require a hypothesis and a target metric written
    before the run, so that I cannot invent an explanation after seeing the numbers.
16. As a prompt author, I want the ledger to require a verdict field of `confirmed` / `refuted` /
    `inconclusive`, so that a version that did not work is recorded as not having worked.
17. As a prompt author, I want the ledger to cite the specific probe ids that motivated the change,
    so that the next reader can check whether those probes actually moved.
18. As a prompt author, I want the ledger's metric block auto-filled from the run artifact, so that
    no number is transcribed by hand and no number can be rounded in my favour.
19. As a prompt author, I want the ledger to hold the diff of the prompt text against the previous
    version, so that "what changed" needs no archaeology.
20. As a prompt author, I want the ledger to link to the full report rather than duplicate it, so
    that there is one place where a number is authoritative.
21. As a prompt author, I want an index listing every version with its headline scores, so that the
    trend across versions is legible at a glance.
22. As an agent working this loop, I want a written decision table for prompt-fault versus
    memory-fault, so that my diagnosis is reproducible and checkable rather than a fresh judgement
    each time.
23. As the account owner, I want a rerun to require explicit approval, so that a 60-call live run
    is never triggered by an agent iterating on its own.
24. As the account owner, I want prompt iteration blocked from being scored against the same probes
    indefinitely, so that a version does not become good at v3 while being no better in the product.
25. As an evaluation author, I want a holdout probe slice reserved for the run that promotes a
    version, so that the claim "this version is better" rests on probes the prompt was not tuned on.
26. As a Langfuse user, I want each probe/arm turn traced with prompt version, probe id, arm and
    probe-set as metadata, so that version-to-score comparison is a filter in Langfuse rather than
    a spreadsheet.
27. As a Langfuse user, I want the deterministic verdict pushed as a score on the trace, so that the
    grading that already exists is the grading Langfuse shows.
28. As a Langfuse user, I want the existing ablation harness kept as the runner, so that three-arm
    masking — which no dataset-experiment shape expresses — is not lost to a migration.
29. As a maintainer, I want the new version identity named distinctly from the existing
    `task_proposal.prompt_version` contract field, so that two unrelated things do not share a name.
30. As a maintainer, I want the registry to be the single seam where a prompt version enters the
    system, so that adding a second versioned prompt later is a registration and not a refactor.
31. As a maintainer, I want prompt resolution failures to be visible in the run artifact, so that a
    run silently executed on the fallback prompt is not reported as a run on version N.
32. As a triage reader, I want failing probes triaged by a coding agent that can open the probe
    definition, the seed, the corpus and the reply text, so that the triage rests on the same
    evidence I would read rather than on the reply alone.
33. As a triage reader, I want the deterministic verdict to remain authoritative when an agent
    disagrees with it, so that an agent can inform a reading and never overturn a score.
34. As a triage reader, I want every claim in an agent's triage to cite the probe id, seed line or
    corpus line it rests on, so that I can check the triage without rerunning anything.
35. As a triage reader, I want the triage to state which agent and model produced it, so that a
    reading I later find wrong is attributable and not anonymous.
36. As a triage reader, I want to be able to run two different agents over the same failures and
    keep both readings, so that disagreement between them is visible as a signal that the case is
    genuinely ambiguous.
37. As a triage reader, I want an agent's triage to be advisory input to a prompt hypothesis and
    never a licence to edit production code, so that the rule against editing the thing being
    measured is not routed around by an agent.
38. As a runbook follower, I want the existing rules — no remote Postgres, one run at a time, never
    edit production code to make a report green — to bind this loop unchanged, so that prompt
    versioning does not become the exception that erodes them.

## Implementation Decisions

### What is built, and how it differs from what was specified

**Fault router** (`memory_eval/fault.py`). `classify(verdict, full, ablated, control) →
FaultClass`. It takes the verdict and the three outcomes; the spec originally passed the
probe too, which turned out to be unused — every rule reads outcomes only, and a parameter
nothing reads invites a rule that quietly depends on probe metadata. `TRIAGE_WORTHY` names
the two classes whose cause is still open (`prompt_fault`, `inconclusive`), so the selection
rule lives next to the classification rather than in the script.

**Triage harness** (`scripts/triage_memory_evaluation.py`). Reads a baseline for verdicts and
its matching detail file for replies, classifies each row, and writes one issue Markdown file
per triage-worthy probe plus an `ISSUES.md` index into `runs/triage/<run_key>/`. Each issue
carries the question, the probe's expectations (`expect_any`, `stale_any`, `expect_refusal`,
`refusal_about`, `invented_any`), the probe's stated purpose, the seeded state for that scope,
all three arm replies with outcome and `why`, and an empty triage record to fill.

Three behaviours the harness commits to that the spec had not stated:

- **It refuses to pair mismatched artifacts.** Baseline and detail must agree on `nonce` or
  `run_key`; there is no "use the newest detail file" fallback. One run's verdicts beside
  another run's replies reads as evidence and is not.
- **Output is gitignored.** `runs/triage/` sits under `runs/`, because an issue carries reply
  and seed text and RUNBOOK rule 5 keeps that uncommitted. Nothing in the tracker is a
  committed artifact today.
- **`memory_fault` probes get no issue** unless `--all` is passed. They are already
  attributed; triage exists to answer a question that is still open.

**Deviation worth naming:** the spec said the *report* would grow a fault-class column and a
prompt-fault worklist. That was not built, because the issue harness delivers the same
information in a form an agent can work directly, and adding it to the report as well would
mean two places where the attribution is stated and could disagree. If a human reader wants
it in the report later, the report should call `fault.classify` rather than re-derive it.

### The seam

There is exactly one seam, and it already exists: `_request_payload` in the chat-reply module
builds the provider payload and sets `"system"` from the module constant `_SYSTEM_INSTRUCTION`.
All four providers (Mistral, OpenRouter, Vyce, Gemini) read `payload["system"]`; none holds its own
copy. Changing what that one key resolves to changes every provider at once, which is why it is the
highest available seam and the only one this spec touches.

**Decision:** `_request_payload` reads the system instruction from a prompt registry rather than
from the constant directly. The constant stays in the module and becomes the registry's fallback
value for its slot.

### Prompt registry module

A new module in the LLM integrations package, deliberately small:

- Resolves a **prompt slot** — a stable string id, `chat_reply.system` being the first and, at
  first, only one — to a resolved prompt: the text plus a version identity plus a source marker
  (`langfuse` or `fallback`).
- Consults Langfuse Prompt Management when Langfuse is configured and a label is selected;
  otherwise returns the in-code fallback. Uses the Langfuse SDK's own caching and its fallback
  argument, so a fetch failure returns the in-code text rather than raising.
- Never performs I/O at import time. The existing `langfuse_bootstrap` module already establishes
  that import-time work is limited to loading configuration; that constraint holds here.
- Exposes the currently resolved version identity for the process, so the eval harness can record
  what it actually ran on without threading a value through every call.

**Label selection.** The label to resolve is configuration, defaulting to the production label.
The evaluation harness selects a candidate label for a candidate run. This is what makes "rerun the
eval against prompt version N+1" a flag rather than a code edit.

**Naming.** The field is `system_prompt_version` everywhere it is recorded. The chat contract
already carries an unrelated `task_proposal.prompt_version`, which is a different concept and must
not be conflated; the schema and its `null` rule are untouched by this spec.

**Fallback honesty.** When resolution falls back, the run artifact records the source as `fallback`
and the version as the in-code fallback identity. A run that quietly executed on the fallback while
the report claims version N is the one failure mode that would make every later comparison a lie.

### Fault router

A pure function in the memory-eval package, alongside the existing verdict derivation, mapping a
probe plus its three arm outcomes plus its verdict to a fault class. The rule, written as a table
because it is a derivation and not a judgement:

| Verdict / shape | Fault class | Why |
|---|---|---|
| `broken` (full arm not PASS) | `memory_fault` | Retrieval never delivered the fact; prompt text cannot conjure it. |
| `dangerous`, full arm `invented` or `stale`, ablated **and** control refuse | `prompt_fault` | Memory delivered; generation misused it. The arms prove the store was not the problem. |
| `dangerous`, ablated or control also `invented`/`stale` | `inconclusive` | The failure reproduces without the scope; attribution is not established. |
| `leaked` | `memory_fault` | Control passed a recall probe — a store-isolation defect. |
| `scope_did_nothing` | `inconclusive` | The answer was reachable without the scope. A probe-design or retrieval question, not a prompt worklist item. |
| `scope_earned_it`, `restraint_held` | `healthy` | Nothing to act on. |
| `unreadable` | `inconclusive` | The run failed for this probe; it supports no claim in either direction. |

Applied to the 2026-08-21 reference report, this yields `ep_recall_01` as `memory_fault` and
`ep_restraint_02` as `prompt_fault` — the two cases identified by hand, which is the correctness
check for the rule.

### Report changes *(not built — superseded by the issue harness; see the deviation note above)*

The report gains, per `REPORT_FORMAT.md`'s existing pyramid structure and Vietnamese prose:

- `system_prompt_version` and its source in the run header block, beside provider and model.
- A fault-class column in the existing per-probe verdict matrix.
- A short prompt-fault worklist section: the probes classed `prompt_fault`, each with its verdict
  and the one-line reason — the input to drafting the next prompt version.

The report generator already exists and is under test; this is an addition to it, not a new writer.

### Langfuse integration

- **The harness stays the runner.** Ablation arms are implemented by a gateway subclass that masks
  one scope's *read*; that shape has no equivalent in a dataset-experiment run, and porting it
  would mean rebuilding the thing being measured. Langfuse receives results; it does not orchestrate.
- **One trace per (probe, arm) turn**, carrying metadata: `probe_id`, `arm`, `probe_set`,
  `system_prompt_version`, `run_key`. Providers are already `@observe`-decorated, so traces exist;
  what is added is the metadata that makes them groupable.
- **Deterministic verdict pushed as a score** on the trace. The grading that already exists is the
  grading Langfuse displays; no second, divergent notion of correctness is introduced.
- **No LLM-as-a-Judge evaluator.** Langfuse's server-side judge is not used. Triage is done by a
  coding agent against the full evidence bundle — see the next section — because the question at
  triage time ("did retrieval hand generation the wrong episode, or did generation misuse the right
  one?") is answered by reading the probe definition, the seed and the retrieval trace, none of
  which a reply-text judge can see. Agent triage is written into the ledger, not pushed as a score.

### Agent-assisted triage *(built — the harness assembles the bundle and the empty record)*

The deterministic grader answers *what happened*. A coding agent — Claude, Codex, Antigravity, Qwen
— answers *why*, by reading the same files a human triager would. It runs strictly after the
deterministic pass, on the probes that pass selects, and it produces a written artifact rather than
a number.

**Selection.** Triage is requested for probes whose fault class is `prompt_fault` or `inconclusive`.
`memory_fault` probes are already attributed and need retrieval work, not a reading; `healthy`
probes have nothing to explain. In the 2026-08-21 reference report that is a handful of probes, not
twenty — triage stays cheap because the deterministic pass has already done the sorting.

**Evidence bundle.** The agent is given, and limited to: the probe definition (question, expected
answers, refusal expectations, purpose), the seeded state for that scope, the corpus documents the
probe draws on, the three arm replies, the deterministic outcome and verdict per arm, and the
retrieval/context payload the full arm actually received. That last item is the one an LLM judge
cannot have and the one that decides most cases — whether the wrong episode was retrieved or the
right one was misread.

**Output contract.** One triage record per probe, structured:

```
probe_id, fault_class_from_router
agrees_with_router   -- yes | no, with reason if no
mechanism            -- what the evidence shows went wrong, one paragraph
evidence             -- probe id, seed line, corpus line, or context field; every claim cited
prompt_change_idea   -- optional; the instruction that would have prevented it, or "none"
confidence           -- high | medium | low
produced_by          -- agent name and model id
```

**Rules that bind the agent.**

- The deterministic verdict is authoritative. An agent that disagrees records the disagreement in
  `agrees_with_router`; it does not change the score, and the report keeps the deterministic label.
  The 2026-08-21 run is why: two of its four `dangerous` cases were near-miss *by design*, so a
  reading that "corrects" them would be relabelling a working grader.
- Every claim carries a citation into the bundle. An uncited mechanism is not triage.
- The agent proposes prompt text; it does not edit production code, probe JSON, or the grader.
  RUNBOOK rule 4 stands, and an agent is exactly the actor most likely to route around it by
  reflex.
- `produced_by` is required. Agent triage is not reproducible across models or across days, so an
  unattributed reading cannot be audited later.

**Multiple agents.** Running two agents over the same failures is supported and their records are
kept side by side, not merged. Agreement is weak evidence the mechanism is real; disagreement is a
useful flag that the case is ambiguous and the prompt hypothesis built on it is speculative. No
voting, no averaging — a majority of language models is not a measurement.

**Where it lands.** Triage records are written into the version ledger file that the resulting
prompt hypothesis belongs to, under a `triage` section. They are inputs to `hypothesis`, and the
ledger keeps them so that a refuted hypothesis can be traced back to the reading that produced it.

### Ledger layout *(README built; version files and index not written yet)*

Under `evaluations/MEMORIES/prompt-versioning/`:

- `INDEX.md` — one row per version: version, date, probe set, headline metrics, verdict, link.
- `README.md` — the loop, the fault-class decision table, and the rule that a hypothesis is written
  before the run.
- `<slot>/v<N>-<YYYY-MM-DD>.md` — one file per prompt version.

Each version file carries English field keys and Vietnamese analysis prose, matching the existing
split where `REPORT_FORMAT.md` and the reports are Vietnamese while `RUNBOOK.md`, `README.md` and
`CONTRACT.md` are English. **[ASSUMED]**

Required fields, in order — hypothesis first, because a file whose hypothesis is written after its
results is not evidence:

```
version, slot, date, parent_version, langfuse_prompt_url
hypothesis          -- written BEFORE the run
target_metric       -- the number expected to move, and by how much
motivating_probes   -- probe ids from the prior report, with their fault class
triage              -- agent triage records for those probes, each with produced_by
prompt_diff         -- unified diff vs parent_version
run                 -- run_key, probe set, provider/model, report link
scoreboard          -- auto-filled: pass rate, scope earned-it, restraint, dangerous count, latency
verdict             -- confirmed | refuted | inconclusive
what_worked / what_did_not
next_hypotheses
```

**Auto-fill boundary.** A scaffold generator fills version identity, prompt diff, run key and the
scoreboard from the run artifact. Hypothesis, verdict, what-worked and next-hypotheses are written
by a human or an agent under review; they are never generated from the numbers, because a
conclusion derived from the numbers by the same process that reports them adds no information.

**Prompt text storage.** The ledger stores a pointer (Langfuse version URL, plus git SHA and module
path when the fallback was used) and the diff against the parent — not a full copy. Full snapshots
rot; pure pointers make "what changed" an archaeology exercise.

### Process guardrails

- **Rerun requires explicit approval.** A live run is 60 provider calls under `v3`. Automatic rerun
  on every drafted version also invites tuning the prompt until the scoreboard is green, which is
  overfitting to 20 probes.
- **Holdout slice.** The probe set is split into a development slice used for iteration and a
  holdout slice run only when promoting a version to the production label. Only a holdout result
  supports the claim that a version is better. **[ASSUMED]**
- **RUNBOOK rules bind unchanged**, in particular: never point the harness at a remote or production
  database; one run at a time; and never edit production code to make a report green. The last rule
  is precisely why prompt candidates live behind a label rather than in a commit.

## Testing Decisions

A good test here asserts external behaviour: what the payload carries, what the artifact records,
what the router concludes. It does not assert that a particular helper was called, and it does not
reach the network. The prior art is the existing memory-eval unit suite — verdicts, scoring, arm
masking, report generation — which tests pure functions against constructed inputs, plus the
chat-reply unit tests which assert payload shape against fake completion callables.

**Prompt registry.**
- Resolves to the in-code fallback when Langfuse is not configured, and reports source `fallback`.
- Resolves to the Langfuse text and version when a stub client returns one, and reports source
  `langfuse`.
- Returns the fallback, without raising, when the stub client raises or times out.
- Performs no I/O at import time.
- The unit suite runs with no Langfuse credentials present. Note the standing hazard that settings
  reload `.env`, so a test that deletes a key in-process does not necessarily make it absent —
  assert on resolution behaviour given an explicit configuration, not on ambient env.

**Chat reply payload.**
- The `system` value in the built payload is the registry-resolved text, exercised through the
  existing payload-shape tests rather than a new seam.
- All four provider adapters continue to receive the same resolved text — extending the existing
  per-provider payload tests.

**Fault router.** *(Built — `tests/unit/features/ai_chat/memory_eval/test_fault.py`, 11 tests.)*
- One test per row of the decision table above.
- The two reference cases from the 2026-08-21 report are pinned by name: a three-arm miss classes
  `memory_fault`; a full arm `invented` with both blind arms clean classes `prompt_fault`.
- Every `Verdict` member has a fault class, so a new verdict cannot be added without deciding its
  class — the one wrong answer nobody would notice in a report.
- `TRIAGE_WORTHY` is pinned, so widening triage is a deliberate edit.

**Report.**
- The header block contains the prompt version and source.
- The prompt-fault worklist lists exactly the probes the router classed `prompt_fault`.
- A run that fell back records source `fallback` in the report — the honesty case.

**Ledger scaffold.**
- Generating from a fixture run artifact produces every required field.
- The scoreboard numbers equal those in the run artifact — no transcription.
- Generation fails, rather than emitting a partial file, when the parent version is unknown.
- The generator writes no hypothesis and no verdict text.

**Triage harness.** *(Not yet tested — the script is exercised by hand against real runs. The
items below are the tests it should have, and the first two are the ones that would have caught
real behaviour already observed.)*
- A baseline whose `verdicts` list is empty produces a loud failure, not an empty index. One
  aborted run in `baselines/` does exactly this today and reports "0 issues" as if it were good news.
- A baseline with no matching detail file exits non-zero rather than pairing the newest one.
- The bundle assembled for a probe contains the probe definition, seeded state, corpus lines, three
  arm replies, per-arm outcomes and the full arm's retrieval context — asserted against a fixture
  run artifact.
- Bundles are assembled only for probes classed `prompt_fault` or `inconclusive`.
- A triage record missing `produced_by`, `evidence` or `agrees_with_router` is rejected rather than
  written into a ledger file.
- A triage record cannot change the verdict or fault class recorded in the report — asserted by
  generating a report from an artifact with and without triage records attached and diffing the
  score sections.

**Langfuse emission.**
- Trace metadata carries `probe_id`, `arm`, `probe_set`, `system_prompt_version`, asserted against a
  stub client.
- Score emission is skipped without credentials and never fails a run — an observability backend
  being down must not change an evaluation result.

Live provider calls stay out of the unit suite, matching the existing split where the live smoke
test sits in the integration suite.

## Improvement Advice — what to do next, in order

Written after building the first half and running it against real artifacts. The ordering is by
value per unit of work, not by the order the spec introduces things.

**1. Record a prompt fingerprint in the baseline. Ten lines, do it before anything else.**
Hash `_SYSTEM_INSTRUCTION` and write `system_prompt_sha` into the baseline artifact. This buys the
entire comparability argument — two runs are comparable iff the shas match — without Langfuse,
without a registry, without a label. Every day this is missing is a day of runs that can never be
compared retroactively, because the artifacts do not record what they were run against. The full
registry can follow whenever; the fingerprint cannot be backfilled.

**2. Fail loudly on an unusable baseline.** A baseline with `verdicts: []` or `aborted: true`
currently yields "issues: 0" and an empty index, which is indistinguishable from a clean run. One
such file is already sitting in `baselines/`. Exit non-zero with the reason instead.

**3. Split `inconclusive`, because it is currently two different things.** On the mistral-medium
v3 run, four of five issues were `unreadable` — provider dropouts — and one was a real
`prompt_fault`. A dropout is not a diagnosis task; it is a rerun task. Suggested split:
`run_failed` (from `unreadable`) and `not_attributable` (from `scope_did_nothing` and ambiguous
`dangerous`), with only the latter triage-worthy. Without this, agent time goes to reading
timeouts, and a genuinely ambiguous probe is buried among them.

**4. Only then build the prompt registry.** Its value is that a candidate prompt becomes a label
rather than a commit. That matters once you are iterating; it matters not at all while the
worklist is one probe long. Keep the in-code fallback, keep unit tests offline.

**5. Write the first ledger file by hand before generating any.** The fields are guesses until a
real cycle has been through them. Generate the scaffold after you know which fields you actually
filled and which you skipped — a generator built on guessed fields makes the guesses permanent.

**6. Langfuse last, and narrowly.** Since it runs locally, the cheap win is trace metadata
(`probe_id`, `arm`, `system_prompt_sha`) so a version comparison is a filter rather than a
spreadsheet. Prompt Management as the source of truth is worth it only once several versions
exist. Do not let it become the orchestrator; the ablation harness stays the runner.

**Housekeeping found in passing, unrelated to this spec:** `probes/test-sem-recall-03.json` is a
single-probe scratch set that fails three shipped-probe-set tests (`≥2 probes per scope`, every
test type exercised, cue-gated retrieval). Either move scratch sets to a directory the shipped-set
tests do not glob, or delete it. Three permanently-red tests train everyone to ignore the suite.

## Out of Scope

- Versioning the Email Action Plan prompts in the provider prompts module. The registry is designed
  so that registering a second slot is a registration rather than a refactor, but the memory
  evaluation does not exercise those prompts, and versioning what cannot be measured produces dead
  metadata.
- Porting probes to Langfuse Datasets or running Langfuse Experiments as the orchestrator.
- Replacing the deterministic grader with an LLM judge, and Langfuse's server-side LLM-as-a-Judge
  evaluators generally. Triage is done by coding agents against the evidence bundle instead.
- Automating the agent triage step as an unattended pipeline. Triage is requested per run, and its
  output is reviewed before a hypothesis is written on it.
- Scoring or benchmarking the triage agents against each other.
- CI gating on experiment results. There is no CI value until a version has proved stable by hand.
- Automatic promotion of a prompt version to the production label.
- Any change to retrieval, the memory gateway, the arms, or the probe sets. This spec adds a reading
  of the existing results and a version identity; it does not change what is measured.
- Any change to the `task_proposal.prompt_version` contract field or its `null` rule.
- Multi-tenant or per-project prompt overrides.
- Publishing specs or issues to an external tracker. Development is local; specs live in
  `tasks/specs/` and triage issues in `runs/triage/<run_key>/`.

## Further Notes

- **The name collision is the sharpest trap here.** `prompt_version` already exists in the chat
  contract as a `task_proposal` field with an unrelated meaning and a hard `null` rule. Every new
  field is `system_prompt_version`.
- **The fault router is a reading, not a new measurement.** Everything it needs is already in the
  three-arm outcomes; the value is that the reading becomes deterministic and reviewable instead of
  being re-derived by hand from a long report each time.
- **Why the hypothesis-before-run rule is not ceremony.** The golden-answer contract already states
  that a reference answer is a specification written before a run, not a summary of one. The same
  reasoning applies to a prompt hypothesis, and for the same reason: post-hoc explanation of a
  number is always available and never informative.
- **Ledger versus Langfuse.** Langfuse records what changed and what it scored. It does not record
  why the change was expected to work or what was concluded. The ledger exists for exactly that
  residue, which is why it is short and why it never retypes a number.
- **Why a coding agent instead of a judge.** A judge scores a reply. Triage here needs to decide
  between two mechanisms that produce identical-looking replies — retrieval handed generation the
  wrong episode, versus generation misread the right one — and the evidence that separates them is
  the retrieval context, the seed and the probe's stated purpose, which live in files. An agent that
  can open those files is answering the question; a judge reading reply text is guessing at it.
  `ep_restraint_02` is the case in point: "Phạm Quốc Huy" is a plausible answer on its face and
  wrong only against the seed.
- **The cost of agent triage is that it is not reproducible.** Two models, or the same model next
  week, will write different readings. That is acceptable because triage is an input to a
  hypothesis, and the hypothesis is then tested by a run — the reproducible part. It would not be
  acceptable if triage produced scores, which is why it does not.
- **Report language.** Reports are Vietnamese per `REPORT_FORMAT.md`; the runbook and contracts are
  English. The ledger follows the reports for prose and code for keys.
