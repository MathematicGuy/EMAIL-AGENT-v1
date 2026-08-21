# Memory Eval Probe Set v3 Design

**Date:** 2026-08-22
**Status:** Design approved in conversation; written spec awaits human review
**Authoritative spec:** [`tasks/specs/SPEC-memory-eval-probe-set-v3.md`](../../../tasks/specs/SPEC-memory-eval-probe-set-v3.md)

## Objective

Ship a **harder-than-v2** diagnostic probe set (`v3_four_scopes_hard`, 20
probes) so live `/mem-eval` can stress-test harness v3 (`invented_any`,
foreign EP, bind-by-id, ST window `len(ST)+1`). Not a 50-probe volume soak.
Not a product quality gate.

## Decisions

- Approach A: same 20-cell grid as v2; hardness from seed, corpus, and
  `invented_any`, not from more rows. Volume (~32–50) is a later set.
- New file `evaluations/MEMORIES/probes/v3-four-scopes-hard.json`. New
  `probe_set_id`. Do not edit v1/v2 or their corpora.
- Dataset is **Vietnamese only** (questions, seed, corpus, grader phrases).
  JSON `note` fields stay English. Toponym aliases (`Da Nang`) allowed.
- `S=6` ST lines, `E=4` episodes (CCCD Hải Phòng ranking distractor, no
  assignee; Phạm Quốc Huy is assigned on the passport-create episode so
  `ep_restraint_02` invented_any is a cross-task near-miss). Window 7/8.
  Live cost ≈ 280 turns vs v2 ≈ 220.
- Fork `corpus-v3/` from v2; add `overtime-night-policy.md` with lookalike
  `OT-141` for làm ca đêm, never làm thêm giờ.
- `invented_any` only when the neighbour is in the seed. `lt_restraint_03`
  stays refusal-only.
- Default `evaluate_memory.py` launch becomes v3 (integer prefix). Report
  still binds by id + sha256.
- No production prompts, no schema bump, no parent SPEC §3 edit, no golden
  judge, no `v3_50_probes` id, CONTROL never seeded.

## Why harder

Crowded ST (two offices, two people, oldest line near trim). Same-shape EP
ranking (two CCCD tasks). Semantic lookalike code. Grader `invented_any` on
those neighbours. Full rationale: spec §5.

## Success

Honesty tests green (including `invented_any` grounding). One `/mem-eval`
run loads v3, report binds v3, 3-arm matrix present. Low Full-arm pass rate
is allowed.
