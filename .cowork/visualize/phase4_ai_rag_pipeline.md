# Phase 4: AI & RAG Pipeline Engineering

## AI Processing Pipeline Diagram

```mermaid
flowchart TD
    E[Email Envelopes Fetched via MailboxPort] --> R[Route Classifier LLM]
    
    R -->|NO_ACTION| S1[Skip Processing & Save 0 Actions]
    R -->|DIRECT_ACTION| G[Action Plan Generator LLM]
    R -->|RETRIEVE_RAG| H[Hybrid Semantic Memory Search]
    
    subgraph RAG ["Hybrid Retrieval Engine (integrations/rag/)"]
        H --> DENSE[Dense Vector Search]
        H --> BM25[Sparse BM25 Keyword Search]
        DENSE --> RRF[Reciprocal Rank Fusion RRF]
        BM25 --> RRF
    end
    
    RRF -->|Top Relevant Chunks| G
    G --> V[Pydantic Schema & Guardrail Policy Validation]
    V --> P[Persist Validated Action Plan]
```

## Key Architectural Principles

1. **Routing Strategy**: Not every email needs full RAG context. The `RouteClassifier` decides if an email contains explicit actions, needs company context (RAG), or requires no action. This saves token cost and latency!
2. **Hybrid Semantic Memory**: Combines dense vector semantics with sparse BM25 keyword matching fused via Reciprocal Rank Fusion (RRF).
3. **Guardrails & Schema Validation**: Raw LLM output is never trusted directly. It is parsed through Pydantic schemas and business policies before database insertion.
