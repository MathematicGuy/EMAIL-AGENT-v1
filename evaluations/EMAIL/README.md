| Artifact | Ownership |
|---|---|
| Gmail candidates | private model input |
| Golden dataset | reference truth |
| Runs | system observations |
| Reports | derived comparisons |

# Email Intent Evaluation

This directory separates human reference truth, private Gmail input, model
observations, and derived comparisons. The report builder consumes one
validated golden object and one validated run object. It joins by `case_id`,
checks the dataset fingerprint, computes aggregate metrics on demand, and does
not persist per-case comparisons.

## Lifecycle

1. Fetch up to 200 unique inbox candidates into the ignored private path:

   ```powershell
   uv run python scripts/fetch_gmail_evaluation_candidates.py --query "in:inbox" --limit 200 --output evaluations/EMAIL/gmail_candidates.json
   ```

2. Validate the route-diverse proposal batch before opening it:

   ```powershell
   uv run python scripts/review_email_annotations.py validate-proposals --candidates evaluations/EMAIL/gmail_candidates.json --proposals evaluations/EMAIL/annotation_proposals.json
   ```

3. Open `evaluations/EMAIL/review/review_annotations.html` locally, select the
   candidate and proposal files, review all proposals, resolve every conflict,
   and export `evaluations/EMAIL/reviewed_annotations.json`.

4. Promote reviewed truth only after the promotion gate passes:

   ```powershell
   uv run python scripts/review_email_annotations.py promote --reviewed evaluations/EMAIL/reviewed_annotations.json --second-pass evaluations/EMAIL/annotation_second_pass.json --output evaluations/EMAIL/golden_dataset.json
   ```

5. Run one explicit, metadata-only shard. Every run is capped at 50 cases:

   ```powershell
   uv run python scripts/evaluate_email_golden.py --candidates evaluations/EMAIL/gmail_candidates.json --golden evaluations/EMAIL/golden_dataset.json --runs-dir evaluations/EMAIL/runs --shard-index 1 --shard-count 4 --limit 50
   ```

6. Build a report only when the golden/run pair is validated and compatible:

   ```powershell
   uv run python scripts/build_email_evaluation_report.py --golden evaluations/EMAIL/golden_dataset.json --run evaluations/EMAIL/runs/<run-id>.json --output evaluations/EMAIL/reports/EMAIL-EVALUATION-REPORT.md
   ```

7. Run the focused script route and its static check:

   ```powershell
   uv run pytest -q tests/unit/scripts
   uv run ruff check scripts tests/unit/scripts
   ```

Task 6 removes the legacy golden and report and does not recreate either a
golden dataset or a report. The commands above document the later lifecycle;
they are not a request to run the private or report-producing steps during
this cleanup.

## Artifact status and privacy

| Path | Status | Boundary |
|---|---|---|
| `README.md` | tracked | lifecycle and ownership documentation |
| `annotation-rubric.md` | tracked | immutable annotation vocabulary and rules |
| `gmail_candidates.json` | ignored | private full-content model input |
| `annotation_proposals.json` | ignored | private reviewer proposals |
| `reviewed_annotations.json` | ignored | private browser-exported decisions |
| `annotation_second_pass.json` | ignored | private corrected-case review |
| `review/review_annotations.html` | tracked | local review UI with no embedded data |
| `golden_dataset.json` | tracked after promotion; absent until then | reference labels only |
| `runs/*.json` | tracked | metadata-only provider/model observations |
| `reports/*.md` | tracked | aggregate comparisons derived at report time |

Private candidate, proposal, review, and second-pass files never enter Git.
Golden truth contains labels and annotation provenance, not Gmail message
content or model predictions. A run contains prediction and routing metadata
only. Reports contain aggregate counts, distributions, accuracy, consistency,
shard identity, provider/model, prompt/rubric versions, and the dataset
fingerprint. Reports exclude sender, subject, source Gmail IDs, rationale,
knowledge gaps, retrieval queries, message content, and all other private
fields.

## Schemas

The shared validators in `scripts/email_evaluation_artifacts.py` own the
schemas and fixed enums.

Golden truth has these top-level fields:

```text
schema_version
rubric_version
case_count
cases[]
```

Each golden case has `case_id`, `source_message_id`, `ground_truth`, and
`annotation`. Ground truth has actionability, sufficiency, knowledge gaps,
expected document types, expected route, and annotation rationale. Annotation
has its source, rubric version, and review timestamp. The report builder uses
the labels for comparison but never renders the private or diagnostic fields.

Each run has:

```text
schema_version
run_id
created_at
dataset_fingerprint
rubric_version
provider
model
prompt_version
shard { index, count, case_count }
cases[]
```

Each run case has `case_id`, a prediction object, and a routing object. The
prediction records actionability, sufficiency, knowledge gaps, retrieval query,
expected document types, confidence, and `source_status`. The source status is
either `model_prediction` or `classifier_fallback`. Routing records
`resolved_route` and reason codes. The evaluator writes no golden fields into
the run.

Reports are not input artifacts. They are generated from a compatible pair and
contain only aggregate metadata and metrics. Per-case comparison records are
never persisted.

## Promotion gate and shards

The annotation promotion gate requires exactly 70 reviewed cases, at least 90%
of actionability proposals accepted unchanged, at least 90% of expected-route
proposals accepted unchanged, no unresolved systematic error, every diagnostic
and route conflict resolved, and a second pass for every corrected case. If
the gate fails, no proposals are promoted and no 130-case expansion occurs.

Live evaluation runs are capped at 50 cases. Four explicit 50-case shards can
cover the eventual 200-case golden dataset. A run is bound to the exact ordered
golden labels by `dataset_fingerprint`; an unknown case ID, duplicate case ID,
mismatched fingerprint, or run over 50 cases fails before a report is written.

## Recovery

- A failed candidate fetch does not replace the previous private candidate file;
  validate the new export before using it.
- A proposal or promotion failure leaves golden truth unpublished. Correct the
  private review inputs, rerun validation, resolve conflicts, and rerun the
  gate.
- A report compatibility failure leaves the output path untouched. Use the
  matching golden dataset, the run's recorded fingerprint, and a run within
  the 50-case cap.
- The Task 6 legacy deletion is recoverable from Git history. To restore the
  exact tracked legacy paths intentionally removed here:

  ```powershell
  git restore 3dd7275 -- evaluations/EMAIL/golden_dataset.json evaluations/EMAIL/EMAIL-EVALUATION_REPORT.md
  ```

  That recovery command is documented only; it was not run by Task 6.
