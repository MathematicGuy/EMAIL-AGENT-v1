# Golden answers — `semantic`

Read `evaluations/MEMORIES/golden/prompts/CONTRACT.md` first. It carries the
rules; this file carries only what is yours.

## Your probes — 6

`sem_recall_01`, `sem_recall_02`, `sem_recall_03`, `sem_restraint_01`,
`sem_restraint_02`, `sem_restraint_03`

Read them from `evaluations/MEMORIES/probes/v2-four-scopes-wide.json`.

## What grounds them

`tests/fixtures/memory_eval/corpus-v2/` — five documents, and nothing else.
Company knowledge is the whole store here; there is no user-specific memory in
any of your answers.

Read all five before writing anything. Three of your probes are restraint
probes, and a restraint probe is only correct if the fact is **absent from every
document**, not merely from the obvious one.

## Watch for

- **`sem_restraint_02` and `sem_recall_03` are a matched pair over one
  document.** The travel document states a domestic per-diem and states that its
  scope is domestic only. The recall probe wants the figure; the restraint probe
  asks the same question about overseas travel, where the answer is that the
  corpus does not cover it. Ground the decline on the **scope line**, not on the
  absence of a number — "the document says it covers domestic travel only" is a
  citation; "I could not find it" is not.
- **`sem_restraint_03` turns on a silence.** The equipment document describes
  replacing a broken laptop and names no form. Two real form codes exist one
  document away. The reference decline must be specifically about the *form*,
  since the procedure itself is answerable — a reply that describes the
  procedure and declines to name a form is correct, and your reference should
  say so.
- **`sem_restraint_01` has no document at all.** Nothing in the corpus concerns
  long-term or sabbatical leave. The nearest document is the annual-leave
  policy, and answering from it is the failure. Do not let your decline be
  satisfiable by a reply about annual leave.
- **The three recall answers are a code, a code and a figure.** Quote each
  exactly as the document writes it, including the Vietnamese thousands
  separator, and list in `must_contain` any spelling the probe already accepts.

## Write to

`evaluations/MEMORIES/golden/parts/semantic.json`, `"scope": "semantic"`.
