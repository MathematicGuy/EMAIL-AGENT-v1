# Golden answers — `episodic`

Read `evaluations/MEMORIES/golden/prompts/CONTRACT.md` first. It carries the
rules; this file carries only what is yours.

## Your probes — 5

`ep_recall_01`, `ep_recall_02`, `ep_update_01`, `ep_restraint_01`,
`ep_restraint_02`

Read them from `evaluations/MEMORIES/probes/v2-four-scopes-wide.json`.

## What grounds them

`seed.episodic` — three task requests, all approved and therefore all
retrievable. Each `request` string is the whole of what that task record
contains. A task has no assignee, no reference number, no status beyond
approved, and no field the request text does not mention.

## Watch for

- **`ep_update_01` is a supersession, not a correction of a mistake.** Two of
  the three seeded tasks concern the same passport filing and carry two
  different dates; both records exist and both are retrievable. The reference is
  the later date. Ground it on *both* seed entries, and say in the `grounding`
  that the earlier one is still in the store — that is what makes preferring the
  later date a real behaviour rather than a lookup with one candidate.
- **The two recall probes must not be answerable from each other.** One asks
  which office a CCCD task is for; the other asks the same of a passport task.
  If your reference for one would satisfy the other, one of them is broken —
  report it in `defects` rather than writing around it.
- **`ep_restraint_02` asks who was assigned.** No seeded task names a person. A
  record that looks otherwise complete is what makes filling the empty field
  tempting, so cover a decline that names the person asked for.
- **The buffer is empty for these probes.** A `short_term` fact cannot ground an
  episodic answer: every non-`short_term` probe is asked in a fresh conversation.
  If the only support you can find for an answer is in `seed.short_term`, that is
  a `defects` entry.

## Write to

`evaluations/MEMORIES/golden/parts/episodic.json`, `"scope": "episodic"`.
