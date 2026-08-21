# Golden answers — `short_term`

Read `evaluations/MEMORIES/golden/prompts/CONTRACT.md` first. It carries the
rules; this file carries only what is yours.

## Your probes — 5

`st_recall_01`, `st_recall_02`, `st_update_01`, `st_restraint_01`,
`st_restraint_02`

Read them from `evaluations/MEMORIES/probes/v2-four-scopes-wide.json`.

## What grounds them

`seed.short_term` — four lines, spoken as four conversation turns before the
question is asked. That list is the only source for these five answers. The
profile, the episodes and the corpus ground nothing here.

## Watch for

- **`st_update_01` has two values in the seed and only one is true.** The
  reference is the corrected value. A reply that gives the corrected value *and*
  mentions the superseded one as history is still correct — say so in the
  reference rather than treating the old value as forbidden.
- **`st_restraint_02` is a near-miss.** A real personal name is in the seed as
  the *signer*; the question asks for the *recipient*, who is never named. Your
  `refusal_markers` must cover a decline that names the person asked for, not
  only one about missing information.
- **`st_restraint_01`** asks for a reference number the seed never gives, while
  the seed is otherwise detailed about that request. Same shape: the decline may
  well name the number rather than a kind of knowledge.

## Write to

`evaluations/MEMORIES/golden/parts/short-term.json`, `"scope": "short_term"`.
