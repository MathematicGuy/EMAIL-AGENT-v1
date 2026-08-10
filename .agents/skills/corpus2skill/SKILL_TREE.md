# Company Knowledge RAG - Project Skill Tree

Tài liệu này định nghĩa cây tri thức (Skill Tree) để AI Agent tra cứu nhanh toàn bộ kiến thức kiến trúc, mã nguồn, tài liệu RAG SOTA và bộ đánh giá (evaluation) trong dự án **Company-knowledge-RAG**.

---

## 🌳 Structure Overview

```
Company-knowledge-RAG
├── 1. Kiến Trúc & Tổng Quan Hệ Thống (Architecture & System Overview)
├── 2. SOTA RAG Knowledge Base (Tài Liệu Chuyên Sâu RAG)
├── 3. Mã Nguồn Core Pipeline (src/)
├── 4. Đánh Giá & Benchmark (Evaluation & Golden Set)
└── 5. MLOps, Deploy & Monitoring (Infra & Observability)
```

---

## 🌿 Detailed Nodes & File Mappings

### 1. Kiến Trúc & Tổng Quan Hệ Thống (Architecture & Specs)
* **1.1 System Architecture & Technical Decisions**
  * File: [`RAG-ARCHITECTURE.md`](file:///D:/User/ProjectGithub/hiepnguyenn-99/Company-knowledge-RAG/RAG-ARCHITECTURE.md)
  * Mô tả: Quyết định kiến trúc Phase 1 (Ingestion), Phase 2 (Hybrid Retrieval + Score Thresholding), Phase 3 (Citation-Gated Generation).
* **1.2 Project Setup & Operational Manual**
  * File: [`README.md`](file:///D:/User/ProjectGithub/hiepnguyenn-99/Company-knowledge-RAG/README.md)
  * Mô tả: Hướng dẫn cài đặt environment (`uv`), chạy API, CLI, Docker, và cấu hình `.env`.

### 2. SOTA RAG Knowledge Base (Tài Liệu Chuyên Sâu RAG)
* **2.1 RAG Knowledge Handbook (11 Chuyên Đề)**
  * File: [`RAG-KNOWLEDGE-SUMMARY.md`](file:///D:/User/ProjectGithub/hiepnguyenn-99/Company-knowledge-RAG/RAG-KNOWLEDGE-SUMMARY.md)
  * Nội dung:
    * Section 1: Chunking Strategies (Fixed, Semantic, Parent-Child, Late Chunking, RAPTOR, Contextual Embeddings, HyQA).
    * Section 2: Hybrid Search & Rank Fusion (BM25 vs Dense, RRF $k=60$, ColBERT, SPLADE, ColPali, MMR).
    * Section 3: GraphRAG & Knowledge Graphs (NER, Coreference, Graph Traversal, Leiden Algorithm).
    * Section 4: Agentic RAG & LangGraph (Prompt Chaining, Supervisor, Checkpointing, HITL).
    * Section 5: Guardrails & AI Safety (Prompt Injection, Lethal Trifecta, NeMo Guardrails, Input/Output Rails).
    * Section 6: Evaluation & Benchmarking (RAG Triad, Ragas, Trulens, LLM-as-a-Judge, G-Eval).
    * Section 7: Infrastructure & Serving (vLLM, Continuous Batching, PagedAttention, Speculative Decoding).
    * Section 8: Fine-Tuning & Alignment (LoRA, QLoRA, DPO, ORPO, Synthetic Data).
    * Section 9: Advanced Memory & Personalization (Episodic, Semantic, Working Memory, MemGPT/Zep).
    * Section 10: Human-in-the-Loop & Operations (Approval workflows, Active Learning).
    * Section 11: Multi-modal RAG (Vision-Language Models, Audio/Video RAG, ColPali).
* **2.2 Advanced RAG Roadmap**
  * File: [`docs/RAG-ROADMAP-ADVANCED.md`](file:///D:/User/ProjectGithub/hiepnguyenn-99/Company-knowledge-RAG/docs/RAG-ROADMAP-ADVANCED.md)
  * Mô tả: Lộ trình phát triển tính năng RAG nâng cao cho hệ thống.

### 3. Mã Nguồn Core Pipeline (`src/`)
* **3.1 Ingestion Engine**
  * Multi-format Parser: [`src/ingestion/parser.py`](file:///D:/User/ProjectGithub/hiepnguyenn-99/Company-knowledge-RAG/src/ingestion/parser.py)
  * Recursive Chunker: [`src/ingestion/chunker.py`](file:///D:/User/ProjectGithub/hiepnguyenn-99/Company-knowledge-RAG/src/ingestion/chunker.py)
  * Contextual Enrichment: [`src/ingestion/enrichment.py`](file:///D:/User/ProjectGithub/hiepnguyenn-99/Company-knowledge-RAG/src/ingestion/enrichment.py)
* **3.2 Storage & Document Registry**
  * JSON Document Registry: [`src/storage/registry.py`](file:///D:/User/ProjectGithub/hiepnguyenn-99/Company-knowledge-RAG/src/storage/registry.py)
* **3.3 Retrieval Engine**
  * Qdrant Vector Store: [`src/retrieval/qdrant_store.py`](file:///D:/User/ProjectGithub/hiepnguyenn-99/Company-knowledge-RAG/src/retrieval/qdrant_store.py)
  * BM25 Lexical Engine: [`src/retrieval/bm25.py`](file:///D:/User/ProjectGithub/hiepnguyenn-99/Company-knowledge-RAG/src/retrieval/bm25.py)
  * Hybrid Search & Reciprocal Rank Fusion (RRF): [`src/retrieval/hybrid.py`](file:///D:/User/ProjectGithub/hiepnguyenn-99/Company-knowledge-RAG/src/retrieval/hybrid.py)
* **3.4 Generation & Citation Guardrails**
  * Source-Gated Generator: [`src/generation/generator.py`](file:///D:/User/ProjectGithub/hiepnguyenn-99/Company-knowledge-RAG/src/generation/generator.py)
  * System Prompts: [`src/prompts/`](file:///D:/User/ProjectGithub/hiepnguyenn-99/Company-knowledge-RAG/src/prompts/)
* **3.5 API & CLI Interfaces**
  * FastAPI Application & Endpoints: [`src/api/routes.py`](file:///D:/User/ProjectGithub/hiepnguyenn-99/Company-knowledge-RAG/src/api/routes.py), [`src/api/app.py`](file:///D:/User/ProjectGithub/hiepnguyenn-99/Company-knowledge-RAG/src/api/app.py)
  * CLI Command Suite: [`src/cli.py`](file:///D:/User/ProjectGithub/hiepnguyenn-99/Company-knowledge-RAG/src/cli.py)
  * System Settings & Configuration: [`src/settings.py`](file:///D:/User/ProjectGithub/hiepnguyenn-99/Company-knowledge-RAG/src/settings.py)

### 4. Đánh Giá & Benchmark (Evaluation & Golden Set)
* **4.1 Golden Set Specification & Dataset**
  * Spec: [`evaluation/GOLDEN_SET_SPEC.md`](file:///D:/User/ProjectGithub/hiepnguyenn-99/Company-knowledge-RAG/evaluation/GOLDEN_SET_SPEC.md)
  * Ground Truth Data: [`evaluation/golden_set/`](file:///D:/User/ProjectGithub/hiepnguyenn-99/Company-knowledge-RAG/evaluation/golden_set/)
* **4.2 LLM-as-a-Judge Evaluation Prompts**
  * Rubric & Prompts: [`docs/llm_judge_prompt.md`](file:///D:/User/ProjectGithub/hiepnguyenn-99/Company-knowledge-RAG/docs/llm_judge_prompt.md)
  * Evaluator Code: [`src/evaluation/`](file:///D:/User/ProjectGithub/hiepnguyenn-99/Company-knowledge-RAG/src/evaluation/)

### 5. MLOps, Deploy & Monitoring (Infra & Observability)
* **5.1 Langfuse Observability**
  * Tracing Client: [`src/observability/langfuse.py`](file:///D:/User/ProjectGithub/hiepnguyenn-99/Company-knowledge-RAG/src/observability/langfuse.py)
  * Docker Service: [`docker-compose.langfuse.yml`](file:///D:/User/ProjectGithub/hiepnguyenn-99/Company-knowledge-RAG/docker-compose.langfuse.yml)
* **5.2 Containerization & Deployment**
  * App Container: [`Dockerfile`](file:///D:/User/ProjectGithub/hiepnguyenn-99/Company-knowledge-RAG/Dockerfile)
  * Compose Stack: [`docker-compose.yml`](file:///D:/User/ProjectGithub/hiepnguyenn-99/Company-knowledge-RAG/docker-compose.yml)

---

## 💡 Navigation Workflow for AI Agent

```
User Query -> Identify Topic Node in SKILL_TREE.md -> Read Specific Target File -> Synthesize Grounded Answer with File Links
```
