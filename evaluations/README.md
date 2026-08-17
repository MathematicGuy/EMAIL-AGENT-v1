# Evaluation Workspace

This is the single in-repository home for committed, metadata-only evaluation
reports and dashboards. Do not store raw emails, chat messages, retrieved
chunks, prompts, or generated answers here.

New to the harnesses? Start with the [Evaluation Harness Guide](./HARNESS-GUIDE.md).

| Area | Purpose | Dashboard |
|---|---|---|
| [RETRIEVAL](./RETRIEVAL/) | Email-RAG routing and retrieval quality | [dashboard.md](./dashboard.md) |
| [CHAT-RAG](./CHAT-RAG/) | Chat-with-documents grounding evaluation | [dashboard.md](./CHAT-RAG/dashboard.md) |
| [CHAT](./CHAT/) | Chat intent and route-classification evaluation | JSON reports only |
| [CHAT/latency](./CHAT/latency/) | Chat-switch UI latency (Playwright) | [TRACK.md](./CHAT/latency/TRACK.md) |
| [baselines](./baselines/) | Retained JSON reports from retrieval and email-routing evaluators | Generated into the retrieval dashboard |

## Refresh

```powershell
# Retrieval reports default here.
python scripts/evaluate_retrieval.py --dry-run

# Rebuild the decision dashboard from stored retrieval reports.
python scripts/build_evaluation_dashboard.py

# Chat-RAG deterministic slices; the input file is local-only and uncommitted.
python scripts/evaluate_chat_rag.py --input <local-only>.json
```

The hashing smoke run validates evaluator mechanics only. Use live embeddings
and record corpus/case counts before making semantic or release decisions.
