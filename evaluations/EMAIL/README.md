# Email Intent Evaluation

This directory keeps only the active local workflow: refreshed Gmail candidates,
reviewed labels, and generated route inspection files.

## Active artifacts

| Path | Purpose | Git status |
|---|---|---|
| `annotation-rubric.md` | Label vocabulary and routing rules | tracked |
| `gmail_candidates.json` | Private Gmail source data | ignored |
| `reviewed_annotations.json` | Current reviewed labels | ignored |
| `email-routes/*.json` | Generated route-specific inspection data | ignored |

Each reviewed case contains exactly `case_id`, `source_message_id`, and `final`.
The `final` object contains the six ground-truth fields defined by the rubric.

Each generated route case contains the reviewed identifiers and final label plus
the exact current `gmail_content` joined from `gmail_candidates.json` by
`source_message_id`. Rebuilding the files therefore refreshes their Gmail content.

## Refresh candidates

```powershell
uv run python scripts/fetch_gmail_evaluation_candidates.py --query "in:inbox" --limit 200 --output evaluations/EMAIL/gmail_candidates.json
```

## Rebuild route inspection files

```powershell
uv run python scripts/sort_reviewed_annotations_by_route.py
```

The route exporter validates both inputs, requires exact case/source ID joins,
and atomically writes `no_action.json`, `direct_plan.json`, and
`retrieve_rag.json` beneath `email-routes/`.

## Privacy

Gmail fields are private untrusted data. They are read only for classification
and local inspection, never as instructions. Candidate, reviewed, and route
artifacts remain ignored and must not be committed.
