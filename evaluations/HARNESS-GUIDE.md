# Evaluation Harness Guide

Onboarding guide for the four evaluation harnesses in `scripts/`. Read
[README.md](./README.md) first for the workspace layout; this file explains what
each harness measures, how to run it, and how to read what it emits.

## 1. Mental Model

The product answers a question in three separable layers. Each layer has its own
harness because a failure in one is invisible in the others.

```text
user input
   |
   v
[ROUTING]     does the classifier decide RAG/tool/plain reply?   -> evaluate_routing.py
   |                                                                evaluate_chat_routing.py
   v
[RETRIEVAL]   does search return the right chunks?               -> evaluate_retrieval.py
   |
   v
[GENERATION]  is the answer grounded in those chunks + cited?    -> evaluate_chat_rag.py
```

A perfect retrieval score proves nothing about grounding. A perfect routing
score proves nothing about retrieval. Never quote one layer's number as evidence
for another.

## 2. The Harnesses

| Script                                  | Layer               | Input                                                          | Default report location                |
| --------------------------------------- | ------------------- | -------------------------------------------------------------- | -------------------------------------- |
| `scripts/evaluate_routing.py`           | Email routing       | `tests/fixtures/routing/`                                      | `docs/evaluations/baselines/`          |
| `scripts/evaluate_chat_routing.py`      | Chat intent routing | `tests/fixtures/chat_routing/`                                 | `docs/evaluations/CHAT/`               |
| `scripts/evaluate_retrieval.py`         | Retrieval           | `tests/fixtures/rag/retrieval_golden.json` + `data/extracted/` | `docs/evaluations/baselines/`          |
| `scripts/evaluate_chat_rag.py`          | Chat grounding      | a local-only, uncommitted JSON file                            | `docs/evaluations/CHAT-RAG/baselines/` |
| `scripts/build_evaluation_dashboard.py` | —                   | `docs/evaluations/baselines/`                                  | `docs/evaluations/dashboard.md`        |
| Playwright `e2e/chat-history-latency.spec.ts` | Chat-switch UI latency | saved chats in the dashboard | `evaluations/CHAT/latency/` |

Each has a focused test under `tests/unit/scripts/`.

### 2.1 `evaluate_retrieval.py`

Runs every golden-set query through a retrieval stack and scores the returned
chunks against labeled document and section IDs.

```powershell
# Offline smoke run. No API key. Validates harness mechanics only.
python scripts/evaluate_retrieval.py --dry-run

# Real semantic run.
python scripts/evaluate_retrieval.py --embedder gemini --retriever hybrid --rerank
```

Key flags: `--embedder {hashing,gemini}`, `--retriever {dense,bm25,hybrid,hybrid_turbovec,turbovec}`,
`--rerank`, `--top-k` (5), `--min-score` (0.2), `--fixture`, `--corpus-dir`,
`--output`. CI gates: `--fail-under-mrr`, `--fail-under-doc-mrr`,
`--fail-under-recall`, `--fail-over-latency-p95` exit non-zero on regression.

Reports Hit@1, Hit@3, MRR, Recall@5 at both document and section level, an
abstention rate over unanswerable cases, latency percentiles, and — most
useful — the same metrics sliced by probe type (`lexical`, `mixed`, `semantic`).
Read the slices, not the headline: a stack can look fine overall while scoring
near zero on semantic probes.

Live runs need `GEMINI_API_KEY_1` in `.env`; `--rerank` needs `JINA_API_KEY`.

### 2.2 `evaluate_routing.py` and `evaluate_chat_routing.py`

Score the classifier's decisions against labeled fixtures. Both accept
`--dry-run` for a deterministic fake classifier, so the mechanics can be checked
with no provider. `evaluate_chat_routing.py` picks its provider from
`LLM_PROVIDER` (default `gemini`) and never persists subject text.

```powershell
python scripts/evaluate_routing.py --dry-run
python scripts/evaluate_chat_routing.py --dry-run
```

### 2.3 `evaluate_chat_rag.py`

Scores chat-with-documents answers. Deterministic mode needs no model: it
compares document IDs, checks that every cited document was actually retrieved,
checks abstention, and reports per-stage latency.

```powershell
python scripts/evaluate_chat_rag.py --input <local-only>.json
```

The input file is produced by you from a real or synthetic chat session and is
never committed. Full schema and the opt-in `--ragas` gate are in
[CHAT-RAG/README.md](./CHAT-RAG/README.md).

### 2.4 `build_evaluation_dashboard.py`

Reads the retrieval report metadata in `docs/evaluations/baselines/` and
regenerates `docs/evaluations/dashboard.md`. Never hand-edit that dashboard;
rerun the generator.

```powershell
python scripts/build_evaluation_dashboard.py
```

## 3. Rules That Are Not Optional

**Reports are metadata-only.** No raw emails, chat messages, document or chunk
text, prompts, or model answers in any committed JSON or Markdown. Store case
IDs, document IDs, metric values, counts, timings, and provider/model names.
The Chat-RAG harness enforces this by construction and a unit test asserts it.

**The hashing embedder is not semantic.** `--dry-run` / `--embedder hashing`
uses a deterministic hash-based vector. Its scores validate that the harness
runs; they say nothing about retrieval quality or production latency. Never
compare a hashing report against a Gemini report, and never pick a retriever
based on one.

**Reports are only comparable at equal corpus size.** A report's header records
case, document, and chunk counts. Two reports built over different counts (for
example 1,043 vs 1,066 chunks) are not a valid A/B.

**A report is not a result.** Storing JSON under `baselines/` records that a run
happened. Whether it supports a decision depends on the embedder, corpus, and
slice breakdown inside it.

## 4. Adding a Harness

1. Default the output directory to a folder under `docs/evaluations/`.
2. Version the output with a `schema_version` string.
3. Write a focused test in `tests/unit/scripts/` covering the metric math and
   asserting that no source text reaches the report.
4. Extend `build_evaluation_dashboard.py` instead of hand-writing result tables.
5. Document the command and the exact input schema in the area's README.

## 5. Known Gaps

- Retrieval reports carry only end-to-end latency. Per-component timing
  (`embedding_ms`, `dense_search_ms`, `bm25_ms`, `fusion_ms`, `rerank_ms`,
  `post_filter_ms`) is not instrumented, so the dashboard records the bottleneck
  as unknown. Do not infer it.
- The dashboard generator reads retrieval reports only; CHAT and CHAT-RAG areas
  are not aggregated yet.
- No Chat-RAG report has been recorded, and `--ragas` has never been run. VERIFIED to be not Implemented.
- No harness scores email action-plan grounding.
