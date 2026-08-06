You are analyzing an existing RAG module for an AI Agent system.

Your goal is to extract the current implementation exactly as it exists before redesigning it.

Analyze all supplied code and documentation.

## 1. RAG module purpose

Explain what the module currently supports:

- ingestion
- indexing
- retrieval
- reranking
- generation
- citations
- access control

## 2. Ingestion architecture

Trace:

Source
→ Ingestion API
→ Parser
→ Chunker
→ Metadata enrichment
→ Embedding
→ Index
→ Document storage

Identify:

- queues
- workers
- retries
- failed-ingestion paths

## 3. Retrieval architecture

Trace:

Request
→ Query preprocessing
→ Authorization
→ Metadata filtering
→ Vector or keyword search
→ Reranking
→ Context assembly
→ Response

## 4. Generation ownership

Determine whether the RAG module:

- only retrieves context
- retrieves and generates an answer
- supports both

Identify exactly where the final LLM call happens.

## 5. Data stores

Identify:

- vector database
- keyword index
- metadata database
- object/document storage
- cache
- queue
- trace store

## 6. API contracts

Document request and response payloads for:

- ingestion
- retrieval
- generation

## 7. Provenance and citations

Identify whether retrieval results include:

- document ID
- chunk ID
- title
- section
- source URL
- version
- relevance score
- rerank score

## 8. Tenant and ACL isolation

Identify:

- tenant namespace
- user namespace
- document ACL filters
- organization filters

## 9. Reliability

Identify:

- timeout handling
- retries
- no-result behavior
- partial-result behavior
- embedding failure
- indexing failure
- retrieval failure
- generation failure

## 10. Mermaid diagrams

Create two Mermaid diagrams.

### Diagram A — Ingestion

Use `flowchart LR`.

Bounded subgraphs:

- SOURCES
- INGESTION API
- PROCESSING
- STORAGE
- FAILURE HANDLING

### Diagram B — Retrieval and Generation

Use `flowchart LR`.

Bounded subgraphs:

- CALLER
- RETRIEVAL API
- SEARCH
- CONTEXT
- GENERATION
- OBSERVABILITY

Clearly show whether generation is inside or outside the RAG module.

## 11. Unknowns

List anything that cannot be confirmed from the source.

Do not redesign the module.
Do not replace current technologies with preferred technologies.