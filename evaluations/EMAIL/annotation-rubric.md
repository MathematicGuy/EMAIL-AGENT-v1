# Email Pipeline Annotation Rubric

Rubric version: `email-pipeline-annotation-v2`.

## Actionability

- `action_required`: email explicitly obligates or asks the user to act.
- `action_suggested`: action may help but remains optional.
- `informational`: useful information with no requested or necessary action.
- `irrelevant`: promotional, noisy, unrelated, or not useful enough for a task.
- `unclear`: the intent or required action cannot be determined confidently.

## Retrieve-first truth

- `informational` and `irrelevant` set `retrieval_expected: false`.
- `action_required`, `action_suggested`, and `unclear` set
  `retrieval_expected: true` regardless of email sufficiency.
- `company_context_required: true` means a policy, procedure, governance
  document, guideline, template, product document, or unresolved internal term
  is needed for a complete plan.
- `company_context_required` is annotation truth about the request. It is not a
  prediction that retrieval will find adequate evidence.

`expected_route` is deliberately absent in v2. The final route is conditional
on observed evidence and is scored after retrieval:

| Observed evidence | Expected final behavior |
|---|---|
| `supported` | `retrieve_rag/full`; only gated chunks may reach generation |
| `unsupported` | `direct_plan/full` only when every actionable decision is sufficient, confidence is `> 0.5`, and no guard fires; otherwise `direct_plan/partial` |
| `unavailable` | `retrieve_rag/partial`, degraded, without weak chunks or citations |

`unclear` can produce a task only in `partial` mode. Every partial/degraded task
must contain `missingInformation`.

## Evidence gate

- Gate version: `email-rag-gate-v1`.
- Default minimum Cohere score: `0.30`.
- Relative cutoff: `0.85` of the top rerank score.
- Accepted chunks satisfy `score >= max(0.30, top_score * 0.85)`.
- A healthy `no_results` response is `unsupported`.
- Missing rerank scores, timeout, auth failure, provider/store/index failure, or
  malformed reranker output are `unavailable`, not `unsupported`.

## Expected document types

Allowed values are exactly:

```text
company_policy
governance_document
procedure
guideline
template
product_documentation
```

Use an empty array when no company document is required. `retrieval_query` is
not ground truth because multiple safe Vietnamese queries can be equally valid.
