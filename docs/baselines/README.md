# Baselines

Regression baselines captured before pipeline migrations. See
`tasks/plan.md` Phase 0 (P0-C) for the rationale: the split-call migration
(V1-M2) must not regress the combined-extractor quality/latency/call-count
baseline recorded here.

## combined-extractor-baseline-*.json

Produced by `scripts/capture_baseline.py`. Captures the configured combined
classify+plan extractor (`LLM_PROVIDER`, default `gemini`) over the labeled
routing fixtures (`tests/fixtures/routing/routing_labels.json`):

- per-case classification agreement vs human labels (the current
  classifications `actionable` / `informational` / `newsletter` /
  `automated_no_action` map to PRD-v1 actionability labels per the
  `ACTIONABILITY_BY_CLASSIFICATION` table in the script);
- one extractor call per case (call count) and per-case latency;
- summary agreement rate and total latency.

Reports store case ids and statistics only — never email bodies or subjects.

## Regenerate

```powershell
# Live capture (needs provider API keys in .env; skips gracefully without them)
python scripts/capture_baseline.py

# Deterministic smoke run without keys
python scripts/capture_baseline.py --dry-run
```

Live capture requires API keys and costs provider calls; the user authorizes
and runs it. Commit the resulting JSON together with the prompt/provider
version context noted in the PR.

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
