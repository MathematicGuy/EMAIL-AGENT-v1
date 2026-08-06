You are a senior AI systems architect.

Your task is to analyze my existing architecture and help me adapt it into a simpler production architecture for a deterministic Cowork Agent.

## Product use case

The feature is:

“Analyze Gmail messages, extract an Action Item, decide whether company knowledge is required, optionally retrieve relevant company documents through RAG, and generate an Action Plan with citations.”

The current workflow is deterministic and one-shot:

Email Input
→ Intent / Knowledge-Sufficiency Classification
→ Optional RAG Retrieval
→ Action Item + Action Plan Generation
→ Persist Output

There is currently no Reflexion loop and no multi-agent system.

## Target architecture principles

The target design should have these clearly separated bounded sections:

1. Entry and Control Plane
2. Gmail / Email Module
3. Agent Core
4. Four-Type Memory System
5. RAG Module
6. Output / Task Persistence
7. Observability and Operations

The four memory types are:

- Short-Term Memory:
  ephemeral run state, raw email context, classifier output, retrieved context, generated candidate output

- Long-Term Declarative Memory:
  stable user preferences, timezone, language, sender priority rules, output preferences

- Episodic Memory:
  generated Action Items and Action Plans, citations, Gmail pointer, status and outcomes

- Semantic Memory:
  company procedures, policies, governance documents and guidelines, accessed through the existing RAG module

Important episodic-memory policy:

- Persist generated task episodes with status = system_generated
- Set retrieval_eligible = false
- Only approved or completed episodes become retrieval eligible later

Important email privacy boundary:

- Raw email may exist in short-term memory during the run
- Raw email must not automatically enter long-term, episodic or semantic memory
- Development traces may contain full email content only when explicitly marked:
  “ALLOW ONLY FOR CURRENT DEVELOPMENT STAGE”

## My current Gmail module

Paste the extracted Gmail module here:

[GMAIL_MODULE_START]

{{PASTE GMAIL MODULE ARCHITECTURE, CODE STRUCTURE, APIs, SERVICES, DATABASES, QUEUES, RETRIES, AND DATA FLOW}}

[GMAIL_MODULE_END]

## My current RAG module

Paste the extracted RAG module here:

[RAG_MODULE_START]

{{PASTE RAG MODULE ARCHITECTURE, CODE STRUCTURE, INGESTION FLOW, RETRIEVAL FLOW, GENERATION FLOW, DATABASES, INDEXES, APIs, AND ERROR HANDLING}}

[RAG_MODULE_END]

## My current overall system architecture

Paste the overall system architecture here:

[OVERALL_ARCHITECTURE_START]

{{PASTE THE OVERALL SYSTEM DIAGRAM, SERVICE LIST, WORKFLOW, DATABASES, API CONTRACTS, AND STATE OWNERSHIP}}

[OVERALL_ARCHITECTURE_END]

## Required analysis

Perform the work in this order.

### Step 1 — Extract the existing architecture

Summarize my current system exactly as provided.

Do not redesign it yet.

Identify:

- services
- modules
- APIs
- databases
- queues
- state ownership
- retry behavior
- timeout behavior
- persistence paths
- observability paths
- Gmail data flow
- RAG data flow
- where generation occurs
- where routing occurs
- where memory currently exists, even if it is not called memory

Clearly label anything that cannot be determined from the provided material.

### Step 2 — Compare current versus target

Create a comparison table with these columns:

- Concern
- Current implementation
- Target implementation
- Keep
- Modify
- Remove
- Missing
- Reason

Compare at least:

- Gmail ingestion
- Email normalization
- Triggering and scheduling
- Intent classification
- Knowledge-sufficiency classification
- Direct-plan path
- RAG retrieval path
- RAG generation ownership
- short-term state
- long-term memory
- episodic memory
- semantic memory
- output persistence
- provenance
- confidence
- TTL
- deletion
- retries
- timeouts
- fallback paths
- observability
- development traces
- user or tenant namespaces

### Step 3 — Simplify before redesigning

Prefer adapting and reusing my current modules.

Do not replace working modules unless necessary.

Use these rules:

- Keep Gmail as an external tool or module
- Keep RAG as a plugable semantic-memory provider
- Keep the Agent Core as the owner of routing and final Action Plan generation
- Avoid duplicate generation between the RAG module and Agent Core
- Avoid duplicate semantic stores
- Avoid adding multi-agent architecture
- Avoid adding Reflexion
- Avoid adding unnecessary queues or services
- Prefer clear interfaces over tightly coupled implementations

### Step 4 — Recommend changes

Group recommendations into:

1. Keep unchanged
2. Wrap behind a new interface
3. Modify internally
4. Add
5. Remove or deprecate
6. Defer until later

For every change, provide:

- current component
- proposed component
- reason
- migration difficulty: low / medium / high
- required schema or API change
- backward-compatibility risk

### Step 5 — Produce the Mermaid diagrams

Generate the following Mermaid flowcharts.

## Diagram 1 — Current Overall Architecture

Show my existing architecture as-is.

Requirements:

- use `flowchart TB`
- use bounded `subgraph` sections
- show services, databases, APIs and queues
- show state ownership
- show current Gmail and RAG flows
- do not add target components that do not exist

## Diagram 2 — Target Overall Architecture

Show the recommended production architecture.

Required bounded sections:

- ENTRY & CONTROL PLANE
- EMAIL MODULE
- AGENT CORE SYSTEM
- MEMORY SYSTEM — 4 TYPES
- RAG MODULE
- OUTPUT & PRODUCT DATA
- OBSERVABILITY & OPERATIONS

Required core flow:

Trigger
→ Gmail fetch
→ Email normalization
→ Load context
→ Intent and Knowledge-Sufficiency Classifier
→ Route Resolver
→ NO_ACTION / DIRECT_PLAN / RETRIEVE_RAG
→ Action Plan Generator
→ Output Validator
→ Persist Task
→ Persist system_generated Episode
→ Clear ephemeral email state

Show:

- queue and dead-letter queue
- service APIs
- database types
- state ownership
- retry boundaries
- timeout handling
- fallback paths
- TTL
- provenance
- confidence
- deletion
- development versus production traces

## Diagram 3 — Gmail Module: Current versus Target

Use two bounded subgraphs:

- CURRENT GMAIL MODULE
- TARGET EMAIL MODULE

Show how each current component maps to the target design.

Show:

- OAuth
- Gmail API
- fetch service
- normalization
- ephemeral envelope
- retries
- timeout
- partial failures
- tracing
- ownership boundary

## Diagram 4 — RAG Module: Current versus Target

Use two bounded subgraphs:

- CURRENT RAG MODULE
- TARGET RAG MODULE

Show:

- ingestion
- parsing
- chunking
- metadata
- embedding
- indexing
- retrieval
- ACL filtering
- reranking
- citation packaging
- failure paths

Clearly show whether final generation currently occurs inside RAG.

If generation currently occurs inside RAG, propose one of these:

- keep it for standalone RAG usage but bypass it for the Cowork workflow
- move final Action Plan generation to Agent Core

## Diagram 5 — Agent Core with Intent Classifier

Show:

- deterministic state machine
- hard policy rules
- structured LLM classifier
- route resolver
- confidence threshold
- classifier retry
- fail-open retrieval behavior
- direct path
- RAG path
- no-action path
- generation
- schema validation
- grounding validation
- partial-plan fallback

Use this retrieval decision:

RAG_REQUIRED =
ACTIONABLE
AND EMAIL_NOT_SUFFICIENT
AND MISSING_INFORMATION_LIKELY_EXISTS_IN_COMPANY_KB

## Diagram 6 — Four-Type Memory System

Show four separate bounded memory areas:

1. Short-Term Memory
2. Long-Term Declarative Memory
3. Episodic Memory
4. Semantic Memory

Show the Agent Core interacting with each one.

Show:

- read policy
- write policy
- namespace resolver
- provenance
- confidence
- TTL
- deletion
- retrieval eligibility
- RAG as semantic-memory provider

## Diagram 7 — Migration Architecture

Show how to migrate from the current system to the target system incrementally.

Suggested phases:

Phase 1:
wrap Gmail and RAG behind interfaces

Phase 2:
add the classifier and route resolver

Phase 3:
add short-term and long-term memory

Phase 4:
add system_generated episodic records

Phase 5:
add approval or completion feedback

Phase 6:
enable validated episodic retrieval

## Mermaid style requirements

Use:

- `flowchart TB` for overall diagrams
- `flowchart LR` for comparison diagrams when clearer
- bounded `subgraph` sections
- databases represented as `[(...)]`
- decision nodes represented as `{...}`
- queues represented as `[("Queue")]`
- dotted arrows for optional or future flows
- solid arrows for current production flows
- edge labels that explain payloads
- concise node labels
- `<br/>` for multiline nodes
- no decorative emojis
- no unsupported Mermaid syntax

Keep each diagram readable.

Do not put every implementation detail in one diagram.

Separate system-level architecture from module-level architecture.

### Step 6 — Produce implementation contracts

After the diagrams, define these contracts:

1. `EphemeralEmailEnvelope`
2. `EmailRouteDecision`
3. `MemoryContextRequest`
4. `SemanticRetrievalRequest`
5. `SemanticRetrievalResponse`
6. `ActionPlanOutput`
7. `TaskEpisode`
8. `TraceEvent`

Use language-neutral YAML schemas.

### Step 7 — Produce the final change plan

End with:

- top 5 architecture changes
- top 5 risks
- first implementation milestone
- recommended order of work
- open questions that must be clarified

Do not invent details absent from my architecture.

Clearly label:

- source-derived observations
- design recommendations
- assumptions
- unresolved questions

