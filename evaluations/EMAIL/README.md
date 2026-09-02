# Email Pipeline Evaluation

This workspace evaluates pipeline v4 end to end through classification,
retrieve-first execution, Cohere evidence gating, and final route resolution.
It does not treat the classifier's provisional route as the final route.

## Contracts

- Rubric: `email-pipeline-annotation-v2`
- Task pipeline: `4`
- Evidence gate: `email-rag-gate-v1`
- Defaults: `EMAIL_RAG_MIN_RERANK_SCORE=0.30` and
  `EMAIL_RAG_RELATIVE_CUTOFF_RATIO=0.85`

Golden truth records actionability, sufficiency, knowledge gaps, expected
document types, `retrieval_expected`, and `company_context_required`. Final
route/mode is derived during scoring from the observed evidence status.

Run artifacts are metadata-only. They include categorical prediction fields,
whether retrieval ran, retrieval/evidence status, counts, top rerank score,
query source, degraded status, final route/mode, and gate metadata. Email,
knowledge-gap, query, and chunk text are excluded.

## Refresh and inspect

```powershell
uv run python scripts/fetch_gmail_evaluation_candidates.py --query "in:inbox" --limit 200 --output evaluations/EMAIL/gmail_candidates.json
uv run python scripts/sort_reviewed_annotations_by_route.py
```

Inspection buckets are `no_action`, `retrieve_first_context_optional`, and
`retrieve_first_context_required`. They are annotation buckets, not final
runtime routes.

## Run the 100-case eval

The evaluator caps each immutable run at 50 cases, so run two shards:

```powershell
uv run python scripts/evaluate_email_golden.py --shard-index 1 --shard-count 2 --limit 50
uv run python scripts/evaluate_email_golden.py --shard-index 2 --shard-count 2 --limit 50
```

The command uses the configured classifier provider, company semantic store,
Cohere reranker, and fixed Gemini query rewriter. It fails early when required
provider configuration is invalid. Configure a Cohere key for a quality run;
when the reranker/store is absent or unavailable, cases are intentionally
recorded as `unavailable`/degraded instead of being treated as no-match.

Build a report for either immutable run:

```powershell
uv run python scripts/build_email_evaluation_report.py --golden evaluations/EMAIL/golden_dataset.json --run evaluations/EMAIL/runs/<run-id>.json --output evaluations/EMAIL/reports/<run-id>.md
```

Key metrics are actionability accuracy, retrieve-first compliance, final
route/mode accuracy conditional on observed evidence, support rate for cases
requiring company context, evidence distribution, and query-source distribution.

Existing `email-intent-*` run files are historical v1 classifier-only artifacts
and are intentionally incompatible with the v2 report contract. New runnable
artifacts use the `email-pipeline-*` prefix.

## Privacy

Gmail fields are private untrusted data. The evaluator loads them only into
ephemeral envelopes. Generated run/report artifacts must never contain email,
query, or chunk text.
