You are analyzing an existing AI Agent system.

Extract its current production architecture exactly as implemented.

Use the provided repositories, diagrams, code and documentation.

## Required output

### 1. System inventory

List all:

- services
- modules
- databases
- caches
- queues
- workers
- APIs
- schedulers
- external providers
- LLM calls
- observability systems

### 2. End-to-end workflow

Trace the complete feature flow:

Trigger
→ Gmail
→ Agent
→ RAG
→ Generation
→ Persistence
→ Output

### 3. State ownership

For each state object, identify its owner:

- raw email
- normalized email
- current run state
- user profile
- retrieved documents
- Action Item
- Action Plan
- citations
- task history
- traces

### 4. Control ownership

Identify which component decides:

- whether an email is actionable
- whether RAG is needed
- which retrieval query to use
- how the Action Plan is generated
- what gets persisted
- when data is deleted

### 5. Failure paths

Identify:

- Gmail failure
- classifier failure
- RAG timeout
- no RAG result
- LLM generation failure
- schema validation failure
- database failure
- notification failure

### 6. Mermaid architecture diagram

Generate one Mermaid diagram using:

- `flowchart TB`
- bounded subgraphs for every major module
- APIs, queues and databases
- state ownership labels
- retry paths
- fallback paths
- observability
- persistence
- deletion

Do not redesign the system yet.

### 7. Architecture gaps

List only gaps proven by the provided materials.

Mark unknown areas as “Not determined.”