You are a senior Python and AI systems engineer.

Set up the folder structure and initial module boundaries for my Cowork Agent project.

The current feature is a deterministic Email-to-Action-Plan workflow:

Gmail
→ Normalize Email
→ Classify Actionability and Knowledge Sufficiency
→ Optionally Retrieve Company Knowledge through RAG
→ Generate Action Item and Action Plan
→ Persist Generated Output
→ Clear Temporary Email State

There is currently:

- no Reflexion loop;
- no multi-agent architecture;
- no AI evaluation framework;
- no experiment runner;
- no model-as-judge;
- no retrieval benchmark suite.

I already have existing Gmail and RAG implementations.

Your goal is to fit those existing implementations into a clean modular structure without rewriting working code unnecessarily.

## Important working rules

1. Inspect the current repository before creating or moving files.
2. Reuse existing Gmail and RAG code whenever possible.
3. Do not create duplicate Gmail or RAG implementations.
4. Do not create microservices.
5. Use one modular Python application.
6. Do not create an `evals/` directory yet.
7. Do not create experiment runners yet.
8. Do not create multiple placeholder implementations simply to fill folders.
9. Create interfaces only at real replacement or testing boundaries.
10. Keep filenames minimal and let folders provide context.
11. Keep class names descriptive.
12. Do not overwrite architecture prompt files.
13. Do not delete or move existing code without first documenting the mapping.
14. Keep all code importable and type-checkable.
15. Preserve current behavior while improving organization.

## Naming convention

Use this convention:

- directory = architectural responsibility;
- file = local operation or strategy;
- class = complete descriptive role;
- method = action.

Examples:

File:

`rag/ingestion/chunkers/semantic.py`

Class:

`SemanticChunker`

File:

`rag/retrieval/retrievers/hybrid.py`

Class:

`HybridRetriever`

File:

`memory/episodic/postgres.py`

Class:

`PostgresEpisodeStore`

Avoid repeated filenames such as:

- `semantic_chunker.py`
- `hybrid_retriever.py`
- `postgres_episode_store.py`
- `rag_service_manager.py`

Prefer:

- `semantic.py`
- `hybrid.py`
- `postgres.py`
- `service.py`

## Target folder structure

Create or migrate toward this structure:

cowork-agent/
├── pyproject.toml
├── README.md
├── Makefile
├── .env.example
├── AGENTS.md
├── CLAUDE.md
│
├── src/
│   └── cowork_agent/
│       ├── __init__.py
│       ├── app.py
│       ├── config.py
│       │
│       ├── domain/
│       │   ├── models.py
│       │   ├── enums.py
│       │   ├── errors.py
│       │   └── identifiers.py
│       │
│       ├── features/
│       │   └── email_action_plan/
│       │       ├── workflow.py
│       │       ├── state.py
│       │       ├── router.py
│       │       ├── generator.py
│       │       ├── validators.py
│       │       ├── policies.py
│       │       ├── schemas.py
│       │       └── prompts/
│       │           ├── classify.md
│       │           └── generate.md
│       │
│       ├── runtime/
│       │   ├── session.py
│       │   ├── context.py
│       │   ├── state.py
│       │   └── cleanup.py
│       │
│       ├── integrations/
│       │   ├── gmail/
│       │   │   ├── client.py
│       │   │   ├── auth.py
│       │   │   ├── normalizer.py
│       │   │   ├── models.py
│       │   │   └── errors.py
│       │   │
│       │   └── llm/
│       │       ├── client.py
│       │       ├── models.py
│       │       └── providers/
│       │           ├── openai.py
│       │           └── anthropic.py
│       │
│       ├── memory/
│       │   ├── service.py
│       │   ├── contracts.py
│       │   ├── router.py
│       │   ├── policies.py
│       │   │
│       │   ├── short_term/
│       │   │   ├── store.py
│       │   │   └── redis.py
│       │   │
│       │   ├── long_term/
│       │   │   ├── store.py
│       │   │   └── postgres.py
│       │   │
│       │   ├── episodic/
│       │   │   ├── store.py
│       │   │   ├── models.py
│       │   │   └── postgres.py
│       │   │
│       │   └── semantic/
│       │       ├── provider.py
│       │       └── rag.py
│       │
│       ├── rag/
│       │   ├── service.py
│       │   ├── contracts.py
│       │   ├── models.py
│       │   ├── registry.py
│       │   │
│       │   ├── ingestion/
│       │   │   ├── pipeline.py
│       │   │   ├── loaders/
│       │   │   │   ├── filesystem.py
│       │   │   │   ├── drive.py
│       │   │   │   └── pdf.py
│       │   │   ├── parsers/
│       │   │   │   ├── markdown.py
│       │   │   │   └── pdf.py
│       │   │   ├── chunkers/
│       │   │   │   ├── fixed_size.py
│       │   │   │   ├── recursive.py
│       │   │   │   └── semantic.py
│       │   │   ├── enrichers/
│       │   │   │   └── metadata.py
│       │   │   └── embedders/
│       │   │       ├── openai.py
│       │   │       └── local.py
│       │   │
│       │   ├── indexing/
│       │   │   ├── vector/
│       │   │   │   ├── pgvector.py
│       │   │   │   └── chroma.py
│       │   │   └── lexical/
│       │   │       └── bm25.py
│       │   │
│       │   ├── retrieval/
│       │   │   ├── pipeline.py
│       │   │   ├── retrievers/
│       │   │   │   ├── dense.py
│       │   │   │   ├── sparse.py
│       │   │   │   └── hybrid.py
│       │   │   ├── filters/
│       │   │   │   ├── tenant.py
│       │   │   │   └── metadata.py
│       │   │   └── rerankers/
│       │   │       ├── none.py
│       │   │       ├── cross_encoder.py
│       │   │       └── llm.py
│       │   │
│       │   └── context/
│       │       ├── builder.py
│       │       ├── budget.py
│       │       └── citations.py
│       │
│       ├── persistence/
│       │   ├── database.py
│       │   ├── repositories/
│       │   │   ├── tasks.py
│       │   │   └── runs.py
│       │   └── migrations/
│       │
│       ├── orchestration/
│       │   ├── scheduler.py
│       │   ├── queue.py
│       │   ├── worker.py
│       │   └── retry.py
│       │
│       └── ops/
│           ├── tracing.py
│           ├── logging.py
│           ├── metrics.py
│           └── events.py
│
├── tests/
│   ├── unit/
│   │   ├── features/
│   │   ├── memory/
│   │   └── rag/
│   ├── integration/
│   │   ├── gmail/
│   │   ├── rag/
│   │   └── database/
│   └── fixtures/
│       ├── emails/
│       └── documents/
│
├── configs/
│   ├── rag/
│   │   ├── baseline.yaml
│   │   ├── dense.yaml
│   │   ├── bm25.yaml
│   │   └── hybrid.yaml
│   ├── memory/
│   │   └── default.yaml
│   └── environments/
│       ├── development.yaml
│       └── production.yaml
│
├── scripts/
│   ├── ingest.py
│   ├── rebuild_index.py
│   └── run_email.py
│
├── docs/
│   └── architectures/
│       ├── current-architectures/
│       └── target-architectures/
│
└── .runtime/
    ├── indexes/
    ├── caches/
    └── traces/

## Important structure rule

Do not create every file blindly.

For each proposed file:

- map it to existing code;
- create it only when it has a current responsibility;
- avoid empty directories that have no present implementation;
- preserve the target structure as the intended direction.

For example, if the project currently supports only recursive chunking:

Create:

`rag/ingestion/chunkers/recursive.py`

Do not create empty files for:

- `fixed_size.py`
- `semantic.py`

Those files can be added when their implementations exist.

## Core interfaces to create now

Create stable Python `Protocol` interfaces for real replacement boundaries.

### Email

- `EmailProvider`

Responsibilities:

- authenticate;
- retrieve messages;
- return normalized email data or raw provider data;
- expose source identifiers.

### Memory

- `ShortTermStore`
- `LongTermStore`
- `EpisodeStore`
- `SemanticMemory`

### RAG ingestion

- `DocumentLoader`
- `DocumentParser`
- `Chunker`
- `Embedder`
- `SearchIndex`

### RAG retrieval

- `Retriever`
- `Reranker`

Do not create one generic `RAGPhase` interface.

Each interface must use typed, phase-specific inputs and outputs.

## Shared RAG models

Define shared models in:

`src/cowork_agent/rag/models.py`

At minimum:

- `RawDocument`
- `Document`
- `Chunk`
- `EmbeddedChunk`
- `RetrievalQuery`
- `RetrievedChunk`
- `Citation`
- `RetrievalResult`

Include identifiers and metadata needed for:

- tenant isolation;
- source provenance;
- document version;
- chunk position;
- retrieval score;
- rerank score;
- source URL.

## Email Action Plan workflow

The feature workflow must own the business flow:

1. Receive temporary Gmail context.
2. Load long-term user configuration.
3. Classify the email.
4. Select:
   - `no_action`;
   - `direct_plan`;
   - `retrieve_rag`.
5. Retrieve company knowledge only when required.
6. Generate the Action Item and Action Plan.
7. Validate the output schema.
8. Persist a minimal task artifact.
9. Save an episodic record with:
   - `status = system_generated`;
   - `retrieval_eligible = false`.
10. Clear temporary email content.

The RAG module must not own final Action Plan generation.

The RAG module returns context and citations.

The Email Action Plan feature owns the final generation.

## Baseline RAG configuration

Create one baseline RAG configuration that matches the existing working implementation.

Do not activate every possible strategy.

Example:

- current loader;
- current parser;
- current chunker;
- current embedding provider;
- current search index;
- current retriever;
- no reranker unless already implemented;
- current context token budget.

The baseline must reproduce existing behavior before future experimentation.

## Registry responsibility

`rag/registry.py` should map configuration names to implementations.

Example conceptually:

- `recursive` → `RecursiveChunker`
- `openai` → `OpenAIEmbedder`
- `pgvector` → `PgVectorIndex`
- `dense` → `DenseRetriever`
- `none` → `NoReranker`

Do not use reflection-based imports or hidden dynamic behavior.

Prefer explicit mappings that are easy to read and test.

## Runtime and generated data

Add `.runtime/` to `.gitignore`.

Do not commit:

- indexes;
- caches;
- temporary email content;
- development traces containing email bodies;
- generated model outputs;
- OAuth tokens.

## Tests included at this stage

Create only normal software tests:

### Unit tests

Test:

- email normalization;
- classifier schema parsing;
- route decisions;
- chunk creation;
- context building;
- memory policies;
- output validation.

### Integration tests

Test:

- Gmail client with mocks or a sandbox;
- RAG ingestion against the configured local test backend;
- RAG retrieval;
- database persistence;
- workflow execution with fake LLM responses.

Do not create:

- benchmark datasets;
- retrieval-quality metrics;
- model judges;
- experiment comparisons;
- `evals/`;
- evaluation reports.

## Required execution process

Perform the work in this order.

### Step 1 — Inspect

Inspect:

- current repository structure;
- current Gmail implementation;
- current RAG implementation;
- current Action Plan generation code;
- existing configuration;
- existing tests;
- current persistence code.

### Step 2 — Map

Before changing files, produce a mapping table:

| Current path | Target path | Action | Reason |
|---|---|---|---|

Allowed actions:

- keep;
- move;
- rename;
- wrap;
- split;
- defer.

### Step 3 — Scaffold

Create only the required directories and files.

Do not add empty experimental implementations.

### Step 4 — Migrate

Move or wrap existing code while preserving behavior.

### Step 5 — Wire

Wire dependencies through `app.py` or a small composition function.

The feature should receive configured implementations rather than construct databases or SDK clients itself.

### Step 6 — Verify

Run:

- import checks;
- formatting;
- type checking;
- unit tests;
- integration tests that do not require unavailable credentials.

### Step 7 — Document

Create:

`docs/architectures/target-architectures/project-structure.md`

Document:

- final folder tree;
- ownership of each top-level module;
- dependency direction;
- current RAG baseline;
- deferred experimental components;
- unresolved architecture questions.

## Required final response

Return:

1. Existing-to-target mapping
2. Final created folder tree
3. Files created
4. Files moved or renamed
5. Existing code reused
6. Interfaces introduced
7. Deferred components
8. Test results
9. Remaining unresolved questions

Do not add an evaluation system during this task.