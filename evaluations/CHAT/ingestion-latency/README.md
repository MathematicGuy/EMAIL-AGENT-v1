# Chat ingestion latency

Measures the document path from client hashing and upload initiation through
server readiness, UI readiness, and the first retrieval-backed response. This
track is separate from chat-history loading latency.

Reports are metadata-only. Raw samples and committed reports may contain only
fixture identifiers, media type, byte/page/chunk counts, optional snapshot
bytes, actual provider labels, actual `loopback`/`remote` database
classification, status, retrieval verification, and numeric timings. Failed or
incomplete samples may use null environment/provider metadata rather than a
fabricated value. Never capture document text, questions, answers, prompts,
signed URLs, cookies, credentials, or retrieved chunk content.

## Fixed protocol

Run **10 measured repetitions of the three-fixture sequence**, after any
environment warm-up, with the same fixture bytes across comparisons. Each
repetition uses one new project and records these cumulative positions:

1. Cold position: small PDF `dang-ky-xe-pdf-v1`.
2. Warm position 2 in the same project: medium multi-chunk PDF
   `procedure-116194-pdf-v1`.
3. Warm position 3 in the same project: DOCX `law-31-2024-docx-v1`.

Every completed sample must carry the actual database host class and providers.
Do not compare loopback and remote samples as if they were the same environment.
Snapshot size is observational and may be null when the collector cannot derive
it.

## Commands

Choose one absolute timing-log path for the run. The actual API and durable
worker must inherit `CHAT_INGESTION_TIMING_LOG` before they start, and the
Playwright collector must inherit the exact same variable and path. For example,
from the repository root, create the path once and copy the printed absolute
value into both PowerShell sessions:

```powershell
$timingDir = New-Item -ItemType Directory -Force '.runlogs/ingestion-latency'
$runStamp = Get-Date -Format 'yyyy-MM-ddTHH-mm-ss-fffZ'
$timingLog = Join-Path $timingDir.FullName "backend-timings-$runStamp.jsonl"
$timingLog
```

Start the real API and worker in the runtime session:

```powershell
$env:CHAT_INGESTION_TIMING_LOG = '<absolute path printed above>'
uv run mail-todo-dev
```

Then run collection from a second session using that identical path:

```powershell
$env:CHAT_INGESTION_TIMING_LOG = '<same absolute path>'
$env:CHAT_INGESTION_LATENCY_LIVE = "1"
pnpm exec playwright test --project=chat-ingestion-latency

# Convert the raw artifact to a timestamped metadata-only report under runs/.
$raw = Get-ChildItem 'evaluations/CHAT/ingestion-latency/runs/????-??-??T*.json' |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1
uv run python scripts/evaluate_ingestion_latency.py --input $raw.FullName

# For an explicitly local run, reject any sample labeled remote.
uv run python scripts/evaluate_ingestion_latency.py --input $raw.FullName --expect-local
```

Setting the path only in the collector is insufficient: the backend JSONL does
not exist unless the actual API and worker processes inherited the same setting.
If API and worker are started separately instead of through `mail-todo-dev`, set
the identical variable before both `uv run mail-todo-api` and
`uv run mail-todo-worker`.

Before aggregation, enrich each browser sample from the local backend JSONL
timing log. The join may use `document_id` transiently, but the enriched raw
`{ "samples": [...] }` artifact must remove it. Neither that raw artifact nor
the generated report may contain `document_id`, document content, extracted
text, prompts, retrieval content, or other correlation payloads.

## Metrics

Each timing reports `count`, `min`, `p50`, nearest-rank `p95`, and `max` in
milliseconds. Missing or null timings reduce `count`; they are never converted
to zero. Failed and otherwise incomplete samples remain visible in the report's
status summary and safe per-sample metadata. `complete_sample_count` includes
only samples whose status is exactly `ready`, retrieval is verified, every
timing is present, and database/provider metadata is non-null.

Browser timings cover hashing, upload initiation, signed upload, completion,
server/UI readiness, and retrieval-backed response latency. Backend enrichment
adds `queue_delay`, `worker_execution`, `source_download`,
`extraction_chunking`, `chunk_persistence`, `embedding`,
`local_index_update`, `snapshot_upload`, and `ready_transition`.

This track is initially **record-only**. It has no regression gates until
repeatable live baselines exist for each environment class.

See [TRACK.md](./TRACK.md) for the run ledger and
[baselines/README.md](./baselines/README.md) for baseline policy.
