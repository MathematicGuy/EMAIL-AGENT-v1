# Email Intent Evaluation Standardization Design

**Date:** 2026-08-19  
**Status:** Approved in conversation; awaiting written-spec review  
**Scope:** `evaluations/EMAIL/`, its supporting scripts, and script-level tests

## Objective

Make the Email Intent Routing evaluation understandable at a glance and safe to
use for repeatable experiments. Human reference truth, private Gmail input,
reviewer proposals, model predictions, and derived reports must be separate
artifacts with explicit schemas and ownership.

The golden dataset is the control. A model run is an experimental observation.
Neither predictions nor derived comparison fields belong in golden truth.

## Decisions

- Start fresh; do not reuse the existing 31 reviewed labels.
- Delete the active legacy golden dataset and evaluation report without
  rewriting Git history.
- Fetch the newest 200 unique Gmail messages using read-only access and the
  query `in:inbox`, ordered by Gmail receipt time descending.
- Production continues to use its existing unread-only filter. The evaluation
  corpus deliberately samples the broader inbox.
- Store each selected message's complete normalized body without character
  truncation under the explicit name `gmail_content`.
- Keep private Gmail input, annotation proposals, and exported human review
  files out of Git.
- Use a separate Codex reviewer subagent, not an application LLM provider, for
  annotation proposals.
- The reviewer sees fresh Gmail input and the approved rubric only. It must not
  see prior predictions, scores, or labels.
- Calibrate on 70 route-diverse proposals before allowing agent-created golden
  labels for the remaining 130 cases.
- Keep live evaluation runs capped at 50 cases. Four explicit shards can cover
  the eventual 200-case golden set.

## Artifact Layout

```text
evaluations/EMAIL/
├── README.md
├── annotation-rubric.md
├── golden_dataset.json
├── gmail_candidates.json
├── annotation_proposals.json
├── reviewed_annotations.json
├── review/
│   └── review_annotations.html
├── runs/
│   └── <run-id>.json
└── reports/
    └── EMAIL-EVALUATION-REPORT.md
```

Artifact responsibilities:

| Artifact | Responsibility | Git policy |
|---|---|---|
| `README.md` | Workflow, commands, schemas, privacy boundary | Tracked |
| `annotation-rubric.md` | Immutable annotation vocabulary and rules | Tracked |
| `gmail_candidates.json` | Private full-content Gmail input | Ignored |
| `annotation_proposals.json` | Private 70-case reviewer proposals | Ignored |
| `reviewed_annotations.json` | Private browser-exported human decisions | Ignored |
| `golden_dataset.json` | Reference labels only | Tracked after promotion |
| `review_annotations.html` | Local review application with no embedded data | Tracked |
| `runs/*.json` | One provider/model/prompt/shard observation | Tracked, metadata-only |
| `reports/*.md` | Metrics derived from golden truth and one run | Tracked |

`golden_dataset.json` must not exist between legacy cleanup and the first
successful 70-case promotion. This prevents proposals from looking like truth.

## Private Candidate Schema

`gmail_candidates.json` is an object rather than a bare array so provenance is
self-describing:

```json
{
  "schema_version": 1,
  "fetched_at": "2026-08-19T00:00:00Z",
  "gmail_query": "in:inbox",
  "ordering": "received_at_desc",
  "case_count": 200,
  "cases": [
    {
      "case_id": "email_case_001",
      "source_message_id": "gmail-message-id",
      "gmail_thread_id": "gmail-thread-id",
      "sender": "Sender <sender@example.com>",
      "subject": "Example subject",
      "received_at": "2026-08-19T00:00:00Z",
      "labels": ["INBOX"],
      "gmail_content": "Complete normalized email body"
    }
  ]
}
```

The fetcher uses Gmail `format=full`, but the evaluation case contains only the
selected message. Cases sharing a thread remain associated through
`gmail_thread_id` and are grouped during classification, matching production.
Attachments remain presence-only under the existing product boundary and are
not copied into evaluation artifacts.

## Annotation Rubric

Rubric version: `email-intent-annotation-v1`.

### Actionability

- `action_required`: the email explicitly obligates or directly asks the user
  to act.
- `action_suggested`: action could benefit the user, but it is optional.
- `informational`: useful information with no requested or necessary action.
- `irrelevant`: unrelated, promotional, noisy, or not useful enough to create
  an action.
- `unclear`: the intent or required action cannot be determined confidently
  from the email.

### Sufficiency and expected route

- Informational or irrelevant -> `no_action`.
- Actionable and fully executable from the email alone -> `direct_plan`.
- Actionable but dependent on missing company knowledge -> `retrieve_rag`.
- Unclear -> `retrieve_rag`.
- Required policy, procedure, governance document, guideline, template,
  product documentation, or unresolved internal term -> `retrieve_rag`.
- Informational and irrelevant cases use `email_is_sufficient: true`; no
  additional knowledge is needed to make the no-action decision.

Allowed expected document types are exactly:

```text
company_policy
governance_document
procedure
guideline
template
product_documentation
```

Use an empty array when no company document is required. `retrieval_query` is
not a ground-truth field because multiple query phrasings can be equally valid.

## Reviewer Proposal Workflow

The delegated reviewer privately inspects all 200 cases to choose a
route-diverse calibration batch, but emits complete proposals for only 70.
Target selection is approximately:

- 24 likely `no_action`
- 23 likely `direct_plan`
- 23 likely `retrieve_rag`

The target is coverage, not a forced class distribution. If the mailbox does
not contain enough credible examples of a route, the proposal metadata records
the actual distribution and shortage.

Proposal files contain no `gmail_content`. Each case records:

```json
{
  "case_id": "email_case_001",
  "source_message_id": "gmail-message-id",
  "proposed_ground_truth": {
    "actionability": "action_required",
    "email_is_sufficient": false,
    "knowledge_gaps": ["Missing internal reimbursement policy"],
    "expected_document_types": ["company_policy"],
    "expected_route": "retrieve_rag",
    "rationale": "The requested action depends on company policy."
  },
  "resolver_expected_route": "retrieve_rag",
  "consistency_status": "consistent",
  "selection_reason": "Likely policy-dependent actionable case",
  "review_status": "pending"
}
```

The reviewer chooses `expected_route` directly. Separately, the production
resolver computes `resolver_expected_route` from the diagnostic fields. A
disagreement is preserved and marked `needs_review`; neither value silently
overwrites the other.

## Human Review Page

`review/review_annotations.html` is a static, dependency-free local page. It
contains no email or proposal data and makes no network requests.

The reviewer selects `gmail_candidates.json` and
`annotation_proposals.json` using file inputs. JavaScript validates both
schemas, joins cases by `case_id`, and holds the joined data in browser memory.

The page provides:

- Accept proposal and Needs correction actions
- Buttons for all five actionability values
- Buttons for all three routes
- Editable sufficiency, knowledge gaps, expected document types, and rationale
- Pending, accepted, corrected, route, and actionability filters
- Previous and next navigation
- Completed and remaining counters
- Export of `reviewed_annotations.json`

The export includes the original proposal and final human decision so unchanged
acceptance rates can be measured deterministically. It does not include Gmail
content.

## Promotion Gate

All 70 proposals must be reviewed. Expansion is allowed only when:

- At least 90% of actionability proposals are accepted unchanged.
- At least 90% of expected-route proposals are accepted unchanged.
- No systematic error remains for a route or actionability category.
- Every diagnostic/route conflict is resolved.
- Corrected cases receive a second reviewer-agent pass under the corrected
  rubric.

After the gate passes:

1. The 70 reviewed cases enter golden truth with
   `annotation.source: human_reviewed`.
2. The calibrated reviewer labels the remaining 130 directly.
3. Those cases use `annotation.source: calibrated_labeling_agent`.
4. The final golden dataset contains exactly 200 cases.

If the gate fails, no proposals are promoted and no 130-case expansion occurs.

## Golden Dataset Schema

The golden artifact contains labels and annotation provenance only:

```json
{
  "schema_version": 1,
  "rubric_version": "email-intent-annotation-v1",
  "case_count": 200,
  "cases": [
    {
      "case_id": "email_case_001",
      "source_message_id": "gmail-message-id",
      "ground_truth": {
        "actionability": "action_required",
        "email_is_sufficient": false,
        "knowledge_gaps": ["Missing internal reimbursement policy"],
        "expected_document_types": ["company_policy"],
        "expected_route": "retrieve_rag",
        "rationale": "The requested action depends on company policy."
      },
      "annotation": {
        "source": "human_reviewed",
        "rubric_version": "email-intent-annotation-v1",
        "reviewed_at": "2026-08-19T00:00:00Z"
      }
    }
  ]
}
```

Golden truth excludes Gmail content, predictions, provider/model metadata,
`ground_truth_status`, `eval_result`, and every other reconstructable field.

## Evaluation Runs

One run records one provider/model/prompt/shard observation. It is capped at 50
cases and never mutates golden truth.

```json
{
  "schema_version": 1,
  "run_id": "email-intent-2026-08-19-openrouter-v1-shard-01",
  "created_at": "2026-08-19T00:00:00Z",
  "dataset_fingerprint": "sha256:...",
  "rubric_version": "email-intent-annotation-v1",
  "provider": "openrouter",
  "model": "model-name",
  "prompt_version": "email-intent-v1",
  "shard": {"index": 1, "count": 4, "case_count": 50},
  "cases": [
    {
      "case_id": "email_case_001",
      "prediction": {
        "actionability": "action_required",
        "email_is_sufficient": false,
        "knowledge_gaps": ["Missing internal reimbursement policy"],
        "retrieval_query": "reimbursement policy",
        "expected_document_types": ["company_policy"],
        "confidence": 0.93,
        "source_status": "model_prediction"
      },
      "routing": {
        "resolved_route": "retrieve_rag",
        "reason_codes": ["policy_required"]
      }
    }
  ]
}
```

`prompt_version` must be immutable; `current` is not a valid persisted version.
The dataset fingerprint binds a run to the exact ordered golden labels used.

## Reporting

The report generator joins one run to golden truth by `case_id` and computes:

- Prediction and fallback coverage
- Actionability accuracy
- Route accuracy
- Per-class distributions
- Diagnostic/route consistency counts
- Shard identity and dataset fingerprint

Comparison values are computed at report time and are never written into the
golden dataset. Reports include the plain-language actionability meanings.

## Failure Handling

- Gmail fetch failure leaves the previous private candidate file untouched by
  writing to a temporary file and replacing only after validation.
- Fewer than 200 unique inbox messages is a hard failure; no partial active
  candidate set is published.
- Duplicate case IDs or source message IDs fail schema validation.
- Missing complete Gmail content fails candidate validation.
- Proposal content fields that copy Gmail bodies fail validation.
- Resolver disagreement marks a proposal for review and blocks promotion.
- A run with unknown golden case IDs, a mismatched fingerprint, or more than 50
  cases fails before any report is written.

## Privacy and Observability

- Gmail access remains `gmail.readonly`.
- Full Gmail content exists only in ignored private candidate input and browser
  memory.
- Full content is never committed, copied into proposals, golden truth, runs,
  reports, Langfuse, or agent handoff summaries.
- Annotation and evaluation disable telemetry export while processing private
  email.
- Attachments are not persisted or analyzed.

## Verification

The narrow script test route owns these invariants:

- Candidate export uses `in:inbox`, newest-first selection, exactly 200 unique
  messages, complete `gmail_content`, and atomic validated writes.
- Private paths are gitignored.
- Proposal and golden schemas reject Gmail content and unknown enum values.
- Reviewer selection emits no more than 70 proposals and records its route mix.
- Resolver conflicts are preserved and block promotion.
- Promotion enforces 70 reviews and both 90% unchanged-label thresholds.
- The HTML contains no embedded private data and exposes the required controls.
- Evaluations write run JSON without mutating golden truth.
- Runs are capped at 50 and reports derive metrics from a run/golden join.

Run the focused script tests first, then the full non-live suite once. A live
Gmail refresh is separately verified by validated output counts and uniqueness;
it is not part of the offline test suite.

## Definition of Done

- Legacy active golden and report files are removed.
- Documentation and annotation rubric describe every artifact and field.
- Private files are ignored and tracked artifacts contain no Gmail content.
- A fresh, validated 200-case `in:inbox` candidate file exists locally.
- A delegated reviewer produces a valid, route-diverse 70-case proposal file.
- The local review page loads both private files and exports valid review data.
- No golden dataset is created before the promotion gate passes.
- Offline focused and full verification pass.
