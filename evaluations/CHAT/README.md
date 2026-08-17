# Chat Routing Evaluation

`scripts/evaluate_chat_routing.py` writes its metadata-only JSON reports here.
These reports measure intent and route classification. They are not Chat-RAG
grounding evidence; use [CHAT-RAG](../CHAT-RAG/) for document-retrieval and
answer-quality evaluation.

Chat **UI switch latency** is a separate harness:
[latency/](./latency/).
