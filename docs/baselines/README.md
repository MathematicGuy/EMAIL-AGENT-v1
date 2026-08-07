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
