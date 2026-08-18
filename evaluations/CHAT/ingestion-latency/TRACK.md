# Chat ingestion latency track

Living metadata-only ledger for the fixed ingestion protocol in
[README.md](./README.md). Initial runs record observations only; no percentile
is a regression gate yet.

## Required coverage per environment

| Project position | Fixed fixture | Measured repetitions | Retrieval check |
|---|---|---:|---|
| 1: cold small PDF | `dang-ky-xe-pdf-v1` | 10 | Required |
| 2: warm medium multi-chunk PDF | `procedure-116194-pdf-v1` | 10 | Required |
| 3: warm DOCX | `law-31-2024-docx-v1` | 10 | Required |

Each repetition creates one project and measures the three positions in order,
for 30 total samples. Warm-up activity is not counted. Keep fixture bytes,
browser/project state, provider labels, and database host class stable within a
run. Log loopback and remote runs separately.

## Baseline ledger

| Date | Environment | Providers | Scenarios / n | Report | Result |
|---|---|---|---|---|---|
| 2026-08-18 | local (loopback Postgres) | supabase_private_storage / jina_embedding_adapter / turbovec | 3 fixtures × 10 reps (n=30) | [baseline-2026-08-18-local-loopback.json](./baselines/baseline-2026-08-18-local-loopback.json) | 30/30 ready (100% success); small PDF p50 11.0s, medium PDF p50 9.2s, DOCX p50 65.1s (430 chunks) |

## Adding a row

1. Set one explicit `CHAT_INGESTION_TIMING_LOG` path before starting the actual
   API and worker, and inherit that identical path in the Playwright collector.
2. Run the three-fixture project sequence 10 times with
   `CHAT_INGESTION_LATENCY_LIVE=1`.
3. Enrich browser samples from the backend JSONL timing log, using
   `document_id` only for the transient join and removing it before writing the
   metadata-only raw artifact.
4. Aggregate the timestamped raw JSON with `uv run python
   scripts/evaluate_ingestion_latency.py --input
   evaluations/CHAT/ingestion-latency/runs/<timestamp>.json`; add
   `--expect-local` for a local database run.
5. Confirm failures, incomplete samples, and metric counts before interpreting
   percentiles.
6. Commit only a reviewed metadata-only baseline copied into `baselines/`.
7. Add one ledger row. Do not add a gate until repeated baselines establish
   normal variance for that environment class.
