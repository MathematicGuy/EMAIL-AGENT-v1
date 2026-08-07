# Routing Fixture Set (P0-B)

Labeled routing evaluation dataset for PRD-v1 §14 / §16 Milestone 2. Consumed
by the routing evaluation harness (V1-M2, task T2.6) and the baseline-capture
script (`scripts/capture_baseline.py`, P0-C).

## Files

- `routing_labels.json` — the labeled cases (28 cases).
- `loader.py` — typed loader with schema validation, used by evaluation tests.

## Case schema

Each case is an object with:

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | string | yes | Unique, `r-NNN`. |
| `subject` | string | yes | Synthetic email subject. |
| `sender` | string | yes | Synthetic sender address. |
| `body` | string | yes | Synthetic email body. Never real user data. |
| `thread_id` | string \| null | yes | Non-null when the case belongs to a correlated thread; cases sharing a `thread_id` exercise incident correlation (P0-D contract). |
| `labels.actionability` | enum | yes | See actionability labels. |
| `labels.email_is_sufficient` | boolean | yes | Whether the email alone suffices to plan the action (PRD-v1 FR-05). |
| `labels.expected_route` | enum | yes | See route labels. |
| `labels.reason_codes` | enum[] | yes | Subset of PRD-v1 FR-05 reason codes; at least one. |

### Actionability labels (PRD-v1 FR-05)

`action_required`, `action_suggested`, `informational`, `unclear`, `irrelevant`.

### Route labels (PRD-v1 FR-06)

`NO_ACTION`, `DIRECT_PLAN`, `RETRIEVE_RAG`. Expected route follows the FR-06
resolver ladder: `informational`/`irrelevant` → `NO_ACTION`; sufficient →
`DIRECT_PLAN`; gap answerable from company documents → `RETRIEVE_RAG`; gap not
answerable from company documents → `DIRECT_PLAN` in partial mode (see
`r-028`).

### Reason codes (PRD-v1 FR-05)

`no_action`, `email_self_contained`, `company_procedure_required`,
`governance_required`, `policy_required`, `template_required`,
`internal_term_unresolved`, `domain_knowledge_required`.

## FR-07 guard category coverage

| Guard category (PRD-v1 FR-07) | Reason code used | Case ids |
|---|---|---|
| Company policy | `policy_required` | r-003, r-009, r-026 |
| Governance | `governance_required` | r-004, r-021 |
| Internal procedures | `company_procedure_required` | r-002, r-016, r-018, r-019 |
| Forms | `company_procedure_required` (FR-05 has no forms-specific code; a required form is internal procedure) | r-006 |
| Templates | `template_required` | r-005, r-024 |
| Tax or regulatory guidance | `domain_knowledge_required` (regulatory guidance answerable from company documents) | r-007 |
| Unresolved internal terminology | `internal_term_unresolved` | r-008, r-016 |

## Special cases

- **False-negative retrieval risk (PRD-v1 §14/§18):** `r-009` reads as a
  self-contained approval request but requires the company discount policy —
  `email_is_sufficient` must be `false` and the route `RETRIEVE_RAG`.
- **Correlated threads:** `T-refund` (r-002, r-018, r-019) and `T-deploy`
  (r-020, r-021).
- **Partial-mode direct plan:** `r-028` has a gap not answerable from company
  documents, so FR-06 resolves `DIRECT_PLAN` with a missing-information
  warning, not `RETRIEVE_RAG`.
- **Language mix:** Vietnamese cases (r-001..r-003, r-005, r-007, r-009,
  r-011, r-012, r-014, r-015, r-018..r-019, r-022, r-023, r-025, r-028) and
  English cases (the rest). Several bodies reuse/adapt
  `tests/fixtures/emails/sample_emails.json` entries.

## Growing the set

Keep every coverage requirement above satisfied when adding cases: all five
actionability labels, all three routes, every FR-07 guard category, the
false-negative-retrieval case, correlated threads, and both languages. The
loader fails loudly on schema violations, so validation runs in the loader
unit test (`tests/unit/fixtures/test_routing_loader.py`).
