# Baselines

Regression baselines for pipeline migrations. See `tasks/plan.md` Phase 0
(P0-C) for the original rationale.

## combined-extractor-baseline-*.json (retired)

`scripts/capture_baseline.py` captured the combined classify+plan extractor
over the routing fixtures as the regression gate for the V1-M2 split-call
migration. The combined extractor was deleted in the V1-M3 generator
migration (T3.3), so the script was retired; no live capture was ever
authorized. The standing regression harness is the routing evaluation below.

## Routing evaluation

Produced by `scripts/evaluate_routing.py` (task T2.6). Evaluates the
configured Route Classifier (`LLM_PROVIDER`, default `gemini`) together with
the deterministic Route Resolver over the labeled routing fixtures
(`tests/fixtures/routing/routing_labels.json`):

- actionability agreement and overall accuracy (predicted vs labeled);
- per-Route confusion counts, precision, and recall (`NO_ACTION`,
  `DIRECT_PLAN`, `RETRIEVE_RAG`);
- the false-negative-retrieval metric: cases labeled `RETRIEVE_RAG` whose
  resolved Route is not `RETRIEVE_RAG`. PRD-v1 §14 flags this as the
  highest-risk error — the email needs company knowledge, but the system
  routes directly to generation — so it is reported separately in the
  `false_negative_retrieval` block (count, rate, case ids).

Run this after EVERY classifier prompt or provider change: it is the V1-M2
evaluation obligation (PRD-v1 §16 Milestone 2). Reports store case ids and
statistics only — never email bodies or subjects.

```powershell
# Live evaluation (needs provider API keys in .env; skips gracefully without them)
python scripts/evaluate_routing.py

# Deterministic smoke run without keys (validates plumbing, not model quality:
# the fake classifier replays the fixture labels, so scores are perfect by construction)
python scripts/evaluate_routing.py --dry-run
```

Live evaluation requires API keys and costs provider calls; the user
authorizes and runs it. Commit the resulting JSON together with the
prompt/provider version context noted in the PR.

## Retrieval reports

`scripts/evaluate_retrieval.py` writes `retrieval-eval-<date>-<embedder>-<retriever>.json` here by default. Run `python scripts/build_evaluation_dashboard.py` after adding a report to refresh [the retrieval dashboard](../dashboard.md).

Only compare reports with the same case count, document count, and chunk count. Hashing reports validate evaluator mechanics; semantic-quality decisions require a live embedding run. Chat routing reports write to `evaluations/CHAT/`, and future chat-with-documents grounding reports belong in `evaluations/CHAT-RAG/baselines/`.
