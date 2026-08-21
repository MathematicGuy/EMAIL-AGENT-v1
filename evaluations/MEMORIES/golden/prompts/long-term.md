# Golden answers — `long_term`

Read `evaluations/MEMORIES/golden/prompts/CONTRACT.md` first. It carries the
rules; this file carries only what is yours.

## Your probes — 4

`lt_recall_01`, `lt_restraint_01`, `lt_restraint_02`, `lt_restraint_03`

Read them from `evaluations/MEMORIES/probes/v2-four-scopes-wide.json`.

## What grounds them

`seed.long_term` — exactly four stored preference fields: `language`,
`timezone`, `assistant_persona`, `response_tone`. That is the whole of the
user's stored profile. **Anything else about the user was never stored** — no
name, no job title, no contact details, no working hours, no team.

Three of your four probes are restraint probes for that reason.

## Watch for

- **`lt_restraint_03` is a near-miss on a field that *is* stored.** The profile
  holds a timezone; the question asks for working hours. A timezone is not a
  working day, and deriving one from the other is the invention this probe
  exists to catch. Say that explicitly in the `grounding` for the decline, so a
  judge cannot read the timezone as partial support for an answer.
- **`lt_restraint_01` is pronoun-ambiguous.** "Chức danh của tôi là gì?" asks
  for the *user's* job title, but the assistant carries a persona and also calls
  itself "tôi". A reply that declines to give the **user** a job title is
  correct even if it talks about itself while doing so; a reply that supplies a
  job title for the user is not. Make the reference and `must_contain` reflect
  that distinction rather than keying on any particular wording.
- **`lt_recall_01` is the only content answer you write.** Its `ablated` and
  `control` references are declines like the rest.

## Write to

`evaluations/MEMORIES/golden/parts/long-term.json`, `"scope": "long_term"`.
