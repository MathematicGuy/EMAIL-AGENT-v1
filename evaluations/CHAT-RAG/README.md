# Chat-RAG Evaluation

This folder is reserved for evaluating the AI Chat workflow when it answers
from ready user documents or company knowledge. It is intentionally separate
from [RETRIEVAL](../RETRIEVAL/): retrieval quality alone does not prove that a
multi-turn answer is grounded, scoped to the right documents, or useful.

## Evidence Boundary

No Chat-RAG quality report is committed yet. The existing chat routing evaluator
measures intent decisions, not answer grounding. Do not mark this area complete
until a report is stored under `baselines/` and reflected in `dashboard.md`.

## Dataset Contract

Each evaluation case needs a stable ID plus the minimum fields needed by the
chosen metric:

| Evaluation slice | Required inputs | Primary measures |
|---|---|---|
| Retrieval relevance | chat question, retrieved contexts, expected document/section IDs | Context Precision, Context Recall, Hit@K, MRR |
| Grounded answer | chat question, retrieved contexts, generated answer | Faithfulness, citation correctness |
| Helpful answer | chat question, generated answer, human reference where available | Response Relevancy, Answer Correctness |
| Multi-turn scope | recent turns, active document set, generated answer | history grounding, tenant/document ACL isolation |
| Abstention | unanswerable question, contexts, generated answer | correct abstention rate, false-abstention rate |

Keep raw chat and document text out of committed report JSON. Store only case
IDs, document IDs, metric values, timing, provider/model identifiers, and
aggregate counts. Synthetic fixtures may contain synthetic text when a test
needs it; never place user content in fixtures or reports.

## RAGAS Adoption Gate

RAGAS is appropriate for the first three slices once a provider, cost budget,
and privacy review are explicitly approved. A RAGAS run must record:

- RAGAS version, evaluator LLM, evaluator embedding model, and prompt/version;
- dataset version and exact case/document counts;
- per-metric aggregate, failure count, and unavailable/failed-evaluation count;
- retrieval, generation, and evaluator latency separately;
- whether contexts came from user documents, company knowledge, or both.

RAGAS scores are evaluation evidence, not runtime policy. Do not use a metric
threshold to reject user answers until it is calibrated against a representative
human-reviewed set.

## Harness

`scripts/evaluate_chat_rag.py` computes the deterministic slices offline. It
reads a local-only dataset file that is never committed and writes a
`chat-rag-eval.v1` report containing metadata only.

```powershell
python scripts/evaluate_chat_rag.py --input <local-only>.json
python scripts/evaluate_chat_rag.py --input <local-only>.json --output evaluations/CHAT-RAG/baselines/chat-rag-eval-YYYY-MM-DD-<dataset>-<model>.json
```

Input schema:

```json
{
  "dataset_version": "local-v1",
  "provider": "optional metadata",
  "model": "optional metadata",
  "cases": [
    {
      "id": "stable-id",
      "expected_document_ids": ["doc-a"],
      "retrieved_document_ids": ["doc-a", "doc-b"],
      "citation_document_ids": ["doc-a"],
      "should_abstain": false,
      "abstained": false,
      "latency_ms": {"retrieval": 12, "generation": 40, "evaluator": null},
      "question": "LOCAL ONLY, required for --ragas",
      "answer": "LOCAL ONLY, required for --ragas",
      "contexts": ["LOCAL ONLY, required for --ragas"],
      "reference_answer": "LOCAL ONLY, required for --ragas"
    }
  ]
}
```

The four text fields are optional in deterministic mode, required in every case
under `--ragas`, and are stripped from the report by construction — a unit test
asserts they never appear at any depth of the output JSON.

Report contents: retrieval `hit_at_1`/`hit_at_5`/`mrr`/`recall_at_5` over
document IDs, citation-linkage valid rate (cited IDs ⊆ retrieved IDs),
abstention accuracy, and p50/p95 for retrieval, generation, and evaluator
latency.

`--ragas` is opt-in and needs the optional `ragas` and `datasets` packages plus
an evaluator provider. Neither is installed here, so `--ragas` currently exits
with a dependency error. Do not install or run it before the adoption gate above
is approved.

## Report Layout

```text
CHAT-RAG/
  README.md                 # this contract
  dashboard.md              # current decision state
  baselines/
    chat-rag-eval-YYYY-MM-DD-<dataset>-<model>.json
```

`build_evaluation_dashboard.py` does not read this area yet. Extend the
generator rather than hand-editing result tables once the first report lands.
