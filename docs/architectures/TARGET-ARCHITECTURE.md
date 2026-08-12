# TARGET ARCHITECTURE

## Cowork Agent — AI Chat Assistant with chat-native TaskEpisodes

**Architecture level:** Level 2 — Production Engineer<br>
**Status:** Baseline target architecture<br>
**Agent pattern:** Multi-turn Chat Controller with typed memory<br>
**Memory model:** Short-term, Long-term Declarative, Episodic, Semantic<br>
**Reflexion:** Not included in this baseline<br>
**Decision authority:** [ADR-004 — Chat-native TaskEpisodes](../adr/ADR-004-chat-native-task-episodes.md)<br>
**Primary use case:** Sustain grounded multi-turn chat with safe, selectively retrieved memory. The standalone PRD-v1 Email Agent remains a separate, stateless, memory-free product flow.

---

## 1. Product and Architecture Hypothesis

> Can a multi-turn Cowork Chat Assistant combine typed memory and enterprise
> knowledge while retaining only user-authorized, body-free task records?

The primary transformation is:

```text
User chat message + active session buffer
        +
Explicit profile + eligible episodes + enterprise RAG
        ↓
Streamed chat response
        ↓
If and only if explicitly requested, bounded task proposal
        ↓
Persisted chat turn and system-generated, retrieval-ineligible TaskEpisode
```

### Current workflow characteristics

- The primary product entry is a multi-turn AI Chat session.
- The Chat Controller owns session orchestration, context assembly, task-proposal production, and SSE streaming.
- A TaskEpisode can be proposed and persisted only after an explicit user request for a task or action plan; ordinary chat, background processes, and model inference alone cannot create one.
- The RAG module is a company-knowledge provider. Its citations may be stored as coordinates, but copied chunks and source text may not.
- Gmail is accessed only by the separate, standalone PRD-v1 Email Agent. AI Chat has no executable Email tool, mailbox selector, or Gmail state.
- Reflexion and multi-agent orchestration are out of scope.
- Email attachments are out of scope under ADR-003; record presence only and do not process
  content.
- System-generated TaskEpisodes are persisted but are not eligible for retrieval until approved or completed in their originating chat session.

---

# 2. Superseded pre-ADR-004 architecture (historical; not the target)

The material in this historical section predates ADR-004. Its executable
in-chat `@Email` path, Email-derived episodes, Gmail/run/tool provenance, and
Action Plan card lifecycle are superseded and must not guide new work. The
accepted replacement begins in §20. The standalone PRD-v1 Email Agent remains
unchanged and memory-free; mentions of it in this historical record do not
make it an AI Chat capability.

```mermaid
flowchart TB

    %% =========================================================
    %% ENTRY AND CONTROL PLANE
    %% =========================================================
    subgraph ENTRY["1. CHAT ENTRY & CONTROL PLANE"]
        CLIENT["AI Chat Client / UI"]
        API["Chat API Controller<br/>sessions · messages"]
        SSE["Streaming SSE Handler<br/>deltas · tool events · citations"]
        CHAT["Chat Controller & Orchestrator<br/>session state · memory · tool routing"]
    end

    CLIENT --> API --> SSE --> CHAT

    %% =========================================================
    %% EMAIL MODULE
    %% =========================================================
    subgraph EMAIL["2. @EMAIL TOOL PLANE"]
        TOOL["@Email Skill / Tool Adapter<br/>stateless execution boundary"]
        TOKEN[("OAuth Token Store<br/>encrypted credentials")]
        GMAIL["Google Gmail API<br/>read-only access"]
        FETCH["Email Reader Service<br/>fetch selected messages"]
        NORMALIZE["Email Normalizer<br/>headers · body · sender · date"]
        ENVELOPE["EphemeralEmailEnvelope<br/>message_id · Gmail link<br/>normalized body · metadata"]
    end

    CHAT -->|explicit tool invocation| TOOL
    TOOL --> FETCH
    TOKEN --> FETCH
    FETCH <--> GMAIL
    FETCH --> NORMALIZE --> ENVELOPE

    %% =========================================================
    %% AGENT CORE
    %% =========================================================
    subgraph AGENT["3. DETERMINISTIC @EMAIL PIPELINE"]
        WORKER["Tool Run Coordinator<br/>owns transient execution lifecycle"]
        CONTEXT["Tool Context Assembler"]
        RULES["Deterministic Policy Guard<br/>tax · governance · policy · forms"]
        CLASSIFIER["Actionability + Knowledge-Sufficiency Classifier<br/>structured LLM output"]
        RESOLVER["Route Resolver<br/>rules + classifier + confidence"]
        ROUTE{"Execution Route"}

        NOACTION["NO_ACTION<br/>informational / irrelevant"]
        DIRECT["DIRECT_PLAN<br/>email is self-contained"]
        NEEDRAG["RETRIEVE_RAG<br/>company knowledge gap"]

        GENERATE["Action Plan Generator<br/>one call per task candidate"]
        VALIDATE["Output Validator<br/>schema · grounding · citations"]
        BUILD["Output Builder<br/>minimal durable task artifact"]
    end

    TOOL --> WORKER
    ENVELOPE --> WORKER
    WORKER --> CONTEXT
    CONTEXT --> RULES
    RULES --> CLASSIFIER
    CLASSIFIER --> RESOLVER --> ROUTE

    ROUTE -->|no action| NOACTION --> BUILD
    ROUTE -->|direct| DIRECT --> GENERATE
    ROUTE -->|retrieve| NEEDRAG

    %% =========================================================
    %% MEMORY SYSTEM
    %% =========================================================
    subgraph MEMORY["4. MEMORY SYSTEM — 4 TYPES"]
        MEMAPI["Memory Gateway / Facade<br/>namespace and policy enforcement"]

        SHORT[("Chat Session Buffer<br/>Redis / in-memory TTL<br/>session_id · bounded turns · active tool state")]

        LONG[("Long-Term Memory<br/>PostgreSQL profile store<br/>persona · preferences · assistant rules")]

        EPISODE[("Episodic Memory<br/>chat summaries + @Email plans<br/>system_generated / approved / completed")]

        SEMPORT["Semantic Memory Port<br/>company knowledge interface"]

        POLICY["Memory Policy Engine<br/>read/write eligibility<br/>provenance · TTL · deletion"]
    end

    CHAT -->|read/write active turns| MEMAPI
    MEMAPI --> POLICY
    POLICY -->|active session + tool state| SHORT
    SHORT --> MEMAPI
    MEMAPI -->|bounded session context| CHAT
    POLICY -->|compact profile per turn| LONG
    LONG --> MEMAPI

    CHAT -->|optional prior-context query| MEMAPI
    POLICY -->|validated episodes only| EPISODE
    EPISODE --> MEMAPI

    POLICY -->|enterprise chat question| SEMPORT
    NEEDRAG --> SEMPORT

    %% =========================================================
    %% RAG MODULE
    %% =========================================================
    subgraph RAG["5. RAG MODULE — EXTERNAL PLUGGABLE SERVICE"]
        RAGAPI["RAG Retrieval API"]
        AUTHZ["Tenant + ACL Filter"]
        SEARCH["Hybrid Retriever<br/>vector + keyword"]
        RERANK["Reranker"]
        INDEX[("Vector / Hybrid Index")]
        DOCSTORE[("Document + Metadata Store")]
        PACK["Context Pack Builder<br/>chunks + citations + scores"]
    end

    SEMPORT --> RAGAPI
    RAGAPI --> AUTHZ --> SEARCH
    SEARCH <--> INDEX
    SEARCH <--> DOCSTORE
    SEARCH --> RERANK --> PACK
    PACK --> MEMAPI
    PACK --> GENERATE

    DIRECT --> GENERATE
    GENERATE --> VALIDATE --> BUILD

    %% =========================================================
    %% OUTPUT
    %% =========================================================
    subgraph OUTPUT["6. OUTPUT & PRODUCT DATA"]
        TASKDB[("Task Output Database<br/>title · minimal paraphrase<br/>plan · citations · Gmail pointer")]
        CARD["In-Chat Action Plan Card"]
        APPROVAL{"Approve · Complete · Reject"}
    end

    BUILD --> TASKDB --> CARD --> SSE

    BUILD -->|tool result DTO| CHAT
    CHAT -->|write turn + tool episode<br/>status=system_generated| MEMAPI
    CARD --> APPROVAL
    APPROVAL -->|lifecycle command| CHAT
    CHAT -->|set lifecycle + eligibility| MEMAPI

    %% =========================================================
    %% OBSERVABILITY
    %% =========================================================
    subgraph OPS["7. OBSERVABILITY & OPERATIONS"]
        EVENTS["Event Stream<br/>run lifecycle events"]
        TRACEDEV[("Development Trace Store<br/>metadata only; no raw email")]
        TRACEPROD[("Production Trace Store<br/>metadata only")]
        METRICS["Metrics + Alerts<br/>latency · route · retrieval quality"]
        PURGE["Retention / Purge Jobs"]
    end

    CHAT --> EVENTS
    WORKER --> EVENTS
    EMAIL --> EVENTS
    AGENT --> EVENTS
    RAG --> EVENTS
    MEMORY --> EVENTS

    EVENTS --> TRACEDEV
    EVENTS --> TRACEPROD
    EVENTS --> METRICS

    PURGE --> SHORT
    PURGE --> TRACEDEV

    %% =========================================================
    %% CLEANUP
    %% =========================================================
    BUILD -->|tool complete| WORKER
    WORKER -->|purge raw email state| SHORT
```

## Primary execution shape

```text
User message → assemble bounded chat memory → stream assistant deltas
→ optionally invoke @Email → one route decision per selected email
→ zero or one RAG retrieval per task candidate
→ one validated Action Plan per task candidate
→ render card and write system-generated episode
→ purge ephemeral email/tool state
```

There is no Reflexion loop. Retries are infrastructure retries, schema-repair retries, or module fallbacks—not autonomous reasoning retries.

---

# 3. Email Module Architecture

```mermaid
flowchart LR

    subgraph CALLER["CALLER"]
        TOOL["@Email Skill / Tool Adapter"]
        CHAT["Chat Controller<br/>active session_id"]
    end

    subgraph EMAIL["EMAIL MODULE"]
        API["Email Module API<br/>read_messages()"]
        AUTH["OAuth Credential Manager"]
        TOKENS[("Encrypted Token Store")]
        CONNECTOR["Gmail Connector"]
        FETCH["Batch Message Fetcher"]
        NORMALIZER["Email Normalizer"]
        POLICY["Content Handling Policy"]
        RESULT["EphemeralEmailEnvelope"]
    end

    subgraph GOOGLE["GOOGLE"]
        GMAIL["Gmail API"]
    end

    subgraph RETRY["RETRY & FAILURE POLICY"]
        LIMIT["429 / 5xx<br/>exponential backoff<br/>max 3 attempts"]
        TIMEOUT["Network timeout<br/>one retry"]
        AUTHFAIL["Expired token<br/>refresh once"]
        PERMFAIL["Permission / revoked access<br/>fail job"]
        PARTIAL["Partial batch success<br/>continue + incomplete flag"]
    end

    subgraph TRACE["OBSERVABILITY"]
        DEVTRACE[("Dev Trace Store<br/>full content allowed<br/>development only")]
        PRODTRACE[("Prod Trace Store<br/>message_id · latency · status only")]
    end

    CHAT --> TOOL --> API
    API --> AUTH
    AUTH <--> TOKENS
    AUTH --> CONNECTOR
    CONNECTOR <--> GMAIL
    CONNECTOR --> FETCH
    FETCH --> NORMALIZER
    NORMALIZER --> POLICY
    POLICY --> RESULT
    RESULT --> TOOL -->|formatted Action Plan DTO| CHAT

    CONNECTOR -. 429 / 5xx .-> LIMIT
    CONNECTOR -. timeout .-> TIMEOUT
    AUTH -. expired .-> AUTHFAIL
    AUTH -. revoked .-> PERMFAIL
    FETCH -. partial page/batch .-> PARTIAL

    RESULT -. development only .-> DEVTRACE
    API -. production telemetry .-> PRODTRACE
```

## Email module responsibilities

The Email Module owns:

- Google OAuth and credential refresh.
- Gmail API request construction.
- Paging and batch reads.
- Retry behavior for transient Google errors.
- Email-body normalization.
- Gmail message and thread identifiers.
- Gmail source links.
- Partial-success reporting.

The Email Module does not own:

- chat sessions or SSE connections;
- task persistence;
- long-term memory;
- episodic retrieval policy;
- semantic document ingestion;
- Action Plan generation.

## EphemeralEmailEnvelope contract

```yaml
run_id: string
tenant_id: string
user_id: string

gmail_message_id: string
gmail_thread_id: string
gmail_url: string

sender:
  name: string
  email: string

recipients:
  - string

subject: string
received_at: datetime
labels:
  - string

normalized_body: string
body_format: text | html_converted

attachments_present: boolean
attachments_processed: false

fetch_status: complete | partial
```

---

# 4. Agent Core and Intent Classifier Architecture

The target has two deliberately separate operational modes. The Chat
Controller runs the multi-turn event loop and may invoke allow-listed tools;
the `@Email` tool retains the bounded deterministic V1-M4 pipeline.

```mermaid
flowchart LR
    USER["User chat message"] --> CHAT["Chat Controller"]
    CHAT --> MEMORY["Assemble profile + episodes<br/>RAG + session buffer"]
    MEMORY --> LLM["Chat LLM"]
    LLM -->|assistant delta| SSE["SSE stream"]
    LLM -->|explicit @Email call| TOOL["@Email Tool Adapter"]
    TOOL --> PIPE["Deterministic Email Pipeline"]
    PIPE --> CARD["Structured Action Plan DTO"]
    CARD --> SSE
    CHAT -->|record bounded turn| MEMORY
```

## Chat Controller event loop

1. Validate the user, tenant, and `session_id`.
2. Read bounded working memory, compact profile, eligible episodes, and any
   selectively requested enterprise RAG context through the Memory Gateway.
3. Stream assistant deltas and typed tool events over SSE.
4. Route an explicit `@Email` call through the tool adapter.
5. Render the returned Action Plan DTO in the originating chat thread.
6. Record the turn and derived episode, then purge transient tool data.

## Deterministic `@Email` tool pipeline

```mermaid
flowchart TB

    subgraph INPUT["INPUT"]
        EMAIL["EphemeralEmailEnvelope"]
        PROFILE["Long-Term User/Profile Context"]
        EPISODES["Validated Episodic Hits<br/>optional"]
    end

    subgraph CORE["@EMAIL TOOL — DETERMINISTIC STATE MACHINE"]
        START["Start Tool Run"]
        STATE[("Short-Term Run State")]

        EXTRACT["Pre-Extraction<br/>candidate outcome · deadline<br/>sender · explicit instructions"]

        RULES["Hard Policy Rules<br/>force retrieval categories"]

        CLASSIFY["LLM Intent Classifier<br/>single structured call"]

        DECISION["Decision Resolver"]
        ROUTE{"Route"}

        NONE["NO_ACTION"]
        DIRECT["DIRECT_PLAN"]
        RAG["RETRIEVE_RAG"]

        RETRIEVE["Call SemanticMemoryPort"]
        RETRYRAG{"RAG success?"}
        PARTIAL["Partial Plan Mode<br/>missing_context=true"]

        GEN["Action Plan Generator<br/>single structured call"]
        VALIDATE["Schema Validator"]
        GROUND["Grounding Validator"]
        FINAL["Final Output Builder"]
    end

    subgraph FAILURE["TECHNICAL FAILURE POLICY"]
        CLASSFAIL["Classifier invalid / timeout<br/>retry once"]
        FAILOPEN["Conservative fallback<br/>route to RAG"]
        GENFAIL["Generation invalid<br/>retry once with repair prompt"]
        HARDFAIL["Persist failed-run status<br/>send to retry queue / DLQ"]
    end

    EMAIL --> START
    PROFILE --> START
    START --> STATE
    STATE --> EXTRACT
    EXTRACT --> RULES
    RULES --> CLASSIFY
    CLASSIFY --> DECISION
    DECISION --> ROUTE

    ROUTE -->|informational| NONE --> FINAL
    ROUTE -->|self-contained| DIRECT --> GEN
    ROUTE -->|knowledge gap| RAG --> RETRIEVE

    EPISODES --> GEN

    RETRIEVE --> RETRYRAG
    RETRYRAG -->|context found| GEN
    RETRYRAG -->|timeout / no useful context| PARTIAL --> GEN

    GEN --> VALIDATE
    VALIDATE --> GROUND
    GROUND --> FINAL

    CLASSIFY -. invalid output .-> CLASSFAIL
    CLASSFAIL --> FAILOPEN --> RAG

    VALIDATE -. schema invalid .-> GENFAIL
    GENFAIL --> GEN
    GENFAIL -. retry exhausted .-> HARDFAIL
```

### Router purpose

The router answers two separate questions:

1. **Actionability:** Does the email require or suggest user action?
2. **Knowledge sufficiency:** Can the Action Plan be grounded in the email alone?

### Route decision formula

```text
RETRIEVE_RAG =
    actionability is actionable
    AND email_is_sufficient = false
    AND missing knowledge is likely available in company documents
```

### Route labels

```text
NO_ACTION
DIRECT_PLAN
RETRIEVE_RAG
```

### Classifier contract

```yaml
actionability:
  enum:
    - action_required
    - action_suggested
    - informational
    - unclear
    - irrelevant

route:
  enum:
    - no_action
    - direct_plan
    - retrieve_rag

candidate_action_item: string | null

email_is_sufficient: boolean

knowledge_gaps:
  - string

retrieval_query: string | null

expected_document_types:
  - company_policy
  - governance_document
  - procedure
  - guideline
  - template
  - product_documentation

reason_codes:
  - no_action
  - email_self_contained
  - company_procedure_required
  - governance_required
  - policy_required
  - template_required
  - internal_term_unresolved
  - domain_knowledge_required

confidence: number
```

### Conservative failure behavior

```text
Classifier timeout or invalid schema
→ retry classifier once
→ if still invalid, route conservatively to RAG
→ if RAG fails, generate a partial plan
→ expose missing context
→ never invent company procedure
```

---

# 5. Four-Type Memory System Architecture

```mermaid
flowchart TB

    subgraph CLIENT["AI CHAT CONTROLLER"]
        REQ["Memory Context Request"]
        WRITE["Memory Write Request"]
    end

    subgraph MEMORY["MEMORY SYSTEM"]
        API["Memory Gateway API"]
        NS["Namespace Resolver"]
        READPOL["Read Policy Engine"]
        WRITEPOL["Write Policy Engine"]
        PROV["Provenance & Confidence Service"]
        DELETE["Retention / Deletion Service"]

        subgraph SHORTBOX["1. SHORT-TERM MEMORY"]
            SHORT[("Redis / In-Memory Store")]
            SHORTDATA["Data:<br/>bounded chat turns<br/>session summary<br/>active tool execution state"]
            SHORTTTL["TTL:<br/>session policy<br/>plus safety expiration"]
        end

        subgraph LONGBOX["2. LONG-TERM DECLARATIVE MEMORY"]
            LONG[("PostgreSQL user_profile")]
            LONGDATA["Data:<br/>assistant persona<br/>language · tone · brevity<br/>priority rules<br/>explicit preferences"]
            LONGWRITE["Writes:<br/>manual configuration<br/>explicit user preference"]
        end

        subgraph EPIBOX["3. EPISODIC MEMORY"]
            EPISODE[("PostgreSQL task_episode")]
            EPIDATA["Data:<br/>chat summaries<br/>@Email Action Plans<br/>citations · Gmail pointer<br/>outcome/status"]
            STATUS["Status:<br/>system_generated<br/>user_approved<br/>completed<br/>rejected"]
            ELIGIBLE["Retrieval eligible only when:<br/>approved or completed"]
        end

        subgraph SEMBOX["4. SEMANTIC MEMORY"]
            SEM["SemanticMemoryPort"]
            RAG["External RAG Module"]
            SEMDATA["Data:<br/>company policies<br/>procedures<br/>governance docs<br/>templates"]
        end
    end

    REQ --> API --> NS --> READPOL

    READPOL -->|active session| SHORT
    READPOL -->|small profile each turn| LONG
    READPOL -->|validated only| EPISODE
    READPOL -->|chat intent says retrieve| SEM

    SHORT --> SHORTDATA
    SHORT --> SHORTTTL

    LONG --> LONGDATA
    LONG --> LONGWRITE

    EPISODE --> EPIDATA
    EPISODE --> STATUS
    STATUS --> ELIGIBLE

    SEM --> RAG --> SEMDATA

    WRITE --> API --> NS --> WRITEPOL
    WRITEPOL -->|session turns + active tool state| SHORT
    WRITEPOL -->|explicit/manual only| LONG
    WRITEPOL -->|store system_generated| EPISODE
    WRITEPOL -. no direct agent write .-> SEM

    WRITEPOL --> PROV
    READPOL --> PROV

    DELETE --> SHORT
    DELETE --> LONG
    DELETE --> EPISODE
```

## Memory read and write policy

| Memory type | Read policy | Write policy | Initial storage |
|---|---|---|---|
| Short-term | Active for the current `session_id`; bounded by turn and TTL policy | Chat turns and transient tool-state writes | Redis or in-process state |
| Long-term declarative | Load a compact persona/profile per relevant turn | Manual config or explicit user preference | PostgreSQL |
| Episodic | Retrieve only eligible chat/tool episodes | Store summaries and `@Email` plans as `system_generated` | PostgreSQL |
| Semantic | Retrieve when chat or tool intent requires enterprise knowledge | No direct controller write | Existing RAG module |

## Episodic decision B

The selected policy is:

```text
Generated Action Plan
→ persist as task episode
→ validation_status = system_generated
→ retrieval_eligible = false
```

Future validation:

```text
User approval or completion signal
→ validation_status = user_approved or completed
→ retrieval_eligible = true
```

## Memory namespace

Every memory operation must carry:

```yaml
tenant_id: string
user_id: string
session_id: string
feature: ai_chat
memory_type: short_term | long_term | episodic | semantic
source_id: string | null
```

Recommended logical key:

```text
tenant_id / user_id / session_id / feature: ai_chat / memory_type / record_id
```

## Provenance fields

```yaml
record_id: string
tenant_id: string
user_id: string

memory_type: string

source_type:
  - user_config
  - system_generated_chat_task
  - user_approved_task
  - company_document
  - migration

source_id: string
source_url: string | null

created_at: datetime
updated_at: datetime
expires_at: datetime | null

model_id: string | null
prompt_version: string | null
pipeline_version: string

confidence: number | null
validation_status: string
retrieval_eligible: boolean
```

---

# 6. RAG Module Architecture

```mermaid
flowchart TB

    %% =========================================================
    %% INGESTION PLANE
    %% =========================================================
    subgraph INGEST["RAG INGESTION PLANE"]
        SOURCES["Company Sources<br/>Drive · PDFs · Wiki · SOP repository"]
        INGESTAPI["Document Ingestion API"]
        INGESTQ[("Ingestion Queue")]
        PARSER["Document Parser"]
        CHUNK["Chunker"]
        META["Metadata + Provenance Enricher"]
        ACL["Tenant / Document ACL Tagger"]
        EMBED["Embedding Service"]
        OBJ[("Object / Document Store")]
        INDEX[("Vector + Keyword Index")]
        FAILED[("Failed Ingestion Queue")]
    end

    SOURCES --> INGESTAPI --> INGESTQ
    INGESTQ --> PARSER --> CHUNK --> META --> ACL --> EMBED
    PARSER --> OBJ
    EMBED --> INDEX
    PARSER -. parse failure .-> FAILED
    EMBED -. repeated failure .-> FAILED

    %% =========================================================
    %% RETRIEVAL PLANE
    %% =========================================================
    subgraph RETRIEVE["RAG RETRIEVAL PLANE"]
        PORT["SemanticMemoryPort Request"]
        RETAPI["Retrieval API"]
        AUTH["Tenant + User Authorization"]
        QUERY["Query Normalizer"]
        FILTER["Metadata / ACL Filters"]
        HYBRID["Hybrid Search<br/>vector + keyword"]
        RERANK["Reranker"]
        THRESHOLD{"Minimum relevance met?"}
        PACK["Context Pack Builder"]
        EMPTY["Empty Retrieval Result"]
        RESPONSE["Structured Retrieval Response"]
    end

    PORT --> RETAPI --> AUTH --> QUERY --> FILTER --> HYBRID
    HYBRID <--> INDEX
    HYBRID <--> OBJ
    HYBRID --> RERANK --> THRESHOLD

    THRESHOLD -->|yes| PACK --> RESPONSE
    THRESHOLD -->|no| EMPTY --> RESPONSE

    %% =========================================================
    %% OPTIONAL GENERATION
    %% =========================================================
    subgraph OPTIONAL["OPTIONAL RAG GENERATION API"]
        RAGGEN["RAG Answer Generator<br/>not used by Cowork workflow"]
    end

    RESPONSE -. optional standalone RAG use .-> RAGGEN

    %% =========================================================
    %% RESILIENCE
    %% =========================================================
    subgraph RESILIENCE["RESILIENCE"]
        TIMEOUT["Retrieval timeout budget"]
        RETRY["One technical retry"]
        FALLBACK["Return empty structured result<br/>Agent creates partial plan"]
    end

    RETAPI -. timeout .-> TIMEOUT --> RETRY
    RETRY -. exhausted .-> FALLBACK
```

## Cowork Agent integration rule

The Cowork Agent calls:

```text
retrieve(query, tenant_scope, filters)
```

It should not call:

```text
retrieve_and_answer(email)
```

The `@Email` deterministic pipeline remains responsible for the final Action
Item and Action Plan; the Chat Controller owns only session orchestration and
tool invocation.

## Retrieval response contract

```yaml
query_id: string
tenant_id: string

chunks:
  - chunk_id: string
    document_id: string
    document_title: string
    section: string | null
    text: string
    source_url: string
    document_version: string | null
    relevance_score: number
    rerank_score: number | null

retrieval_status:
  enum:
    - success
    - no_results
    - timeout
    - authorization_denied
    - partial

latency_ms: integer
```

---

# 7. Agent and Memory Interaction Flow

```mermaid
flowchart LR

    subgraph CHAT["CHAT CONTROLLER"]
        A1["1. Receive User Message"]
        A2["2. Read Memory Context"]
        A3["3. Call Chat LLM"]
        A4{"4. @Email requested?"}
        A5["5. Execute @Email Tool"]
        A6["6. Stream Response / Card"]
        A7["7. Record Turn & Episode"]
    end

    subgraph MEMORY["MEMORY SYSTEM"]
        S[("Chat Session Buffer<br/>session_id turns")]
        L[("Long-Term<br/>profile")]
        E[("Episodic<br/>chat + tool history")]
        M["Semantic Port"]
    end

    subgraph RAG["RAG MODULE"]
        R["Company Knowledge Retrieval"]
    end

    subgraph TOOL["@EMAIL TOOL"]
        P["Reader → Classifier → RAG<br/>→ Action Plan Generator"]
        RAW[("Transient raw email state")]
    end

    subgraph PRODUCT["PRODUCT & STREAM"]
        T[("Task Output DB")]
        SSE["SSE Action Plan Card"]
    end

    A1 -->|append bounded turn| S --> A2
    A2 -->|persona + preferences| L
    A2 -->|approved/completed only| E
    A2 -->|selective enterprise query| M --> R --> A3
    L --> A3
    E --> A3
    A3 --> A4
    A4 -->|no| A6
    A4 -->|yes| A5 --> P
    P --> RAW
    P -->|validated Action Plan DTO| T --> A6 --> SSE
    A6 --> A7
    A7 -->|chat turn + system_generated plan| E
    A7 -->|updated bounded context| S
    A7 -->|purge| RAW
```

---

# 8. State Ownership

| Component | Owns | Must not own |
|---|---|---|
| Chat Controller | Chat turns, context assembly, tool routing, SSE connection | Raw email persistence or memory storage internals |
| Chat Session Buffer | Bounded short-term turn history for one `session_id` | Durable profile or episodes |
| `@Email` Tool | Transient Gmail fetch and deterministic pipeline state | Chat session history or durable raw email |
| Job Queue | Run delivery | Email content |
| Email Module | OAuth, Gmail fetching, normalization | Tasks or durable memories |
| Deterministic Email Pipeline | Email routing and Action Plan generation | Chat orchestration or company document storage |
| Short-Term Memory | Active chat turns and temporary tool state | Durable user facts or raw-email retention |
| Long-Term Memory | Stable user and system configuration | Raw emails |
| Episodic Memory | Derived task history and outcome status | Raw email body |
| RAG Module | Company documents, chunks, embeddings, citations | User task history |
| Task Service | Task title, plan, citations, Gmail pointer | Full email body |
| Observability | Runtime traces and metrics | Product memory source of truth |

---

# 9. Email Content Database Trace

Raw email content never enters persistent infrastructure. The only permitted
path is transient in-process or TTL-bound tool state that is purged when the
`@Email` invocation completes or fails.

## 9.1 Development observation path

```text
Gmail API
→ Email Module
→ Agent runtime
→ Transient tool state
→ purge on completion/failure
```

Possible fields:

```yaml
run_id: string
tenant_id: string
user_id: string
gmail_message_id: string

input_payload: prohibited
normalized_email: prohibited
classifier_input: metadata_only
classifier_output: metadata_only
retrieval_query: string | null
retrieved_context: citation_ids_only
generation_input: metadata_only
generation_output: derived_action_plan_only

created_at: datetime
expires_at: datetime
environment: development
```

This metadata trace must not feed:

- long-term memory extraction;
- episodic-memory learning;
- semantic-memory ingestion;
- RAG indexing;
- training datasets;
- analytics exports without separate policy.

## 9.2 Durable task-output path

```text
Email
→ Agent generation
→ Task Output Database
→ Episodic Task Store
```

Persisted task fields:

```yaml
gmail_message_id: string
gmail_url: string

task_title: string
minimal_request_paraphrase: string

action_plan:
  - string

priority: string | null
deadline: datetime | null

rag_citations:
  - document_id: string
    document_title: string
    section: string | null
    source_url: string

missing_information:
  - string

status: system_generated

classifier_confidence: number
generation_confidence: number | null
```

Raw email content is transient `@Email` tool-execution data. It must never be
added to task rows, chat history, traces, indexes, or any durable memory type.

---

# 10. Suggested Internal Service APIs

```text
POST   /v1/cowork/chat/sessions
POST   /v1/cowork/chat/sessions/{session_id}/messages  # SSE response
POST   /v1/cowork/chat/sessions/{session_id}/task-episodes/{episode_id}/approve
POST   /v1/cowork/chat/sessions/{session_id}/task-episodes/{episode_id}/complete
POST   /v1/cowork/chat/sessions/{session_id}/task-episodes/{episode_id}/reject
DELETE /v1/cowork/chat/sessions/{session_id}/task-episodes/{episode_id}

POST /v1/cowork/tools/email
POST /v1/cowork/email-action-plan/runs
GET  /v1/cowork/email-action-plan/runs/{run_id}

POST /v1/email/messages/read
POST /v1/memory/context/read
POST /v1/memory/episodes/write
POST /v1/memory/episodes/{episode_id}/transition
POST /v1/rag/retrieve
POST /v1/tasks
```

## Suggested internal events

```text
chat.message.received
chat.tool.invoked
chat.message.completed

cowork.run.created
cowork.run.started

email.read.requested
email.read.completed
email.read.partial
email.read.failed

memory.context.requested
memory.context.loaded
memory.context.partial
memory.context.failed

route.decided
route.classifier_retried
route.fallback_to_rag

rag.retrieval.requested
rag.retrieval.completed
rag.retrieval.empty
rag.retrieval.failed

action_plan.generation.started
action_plan.generated
action_plan.validation_failed
action_plan.generation_failed

task.persisted
episode.system_generated

run.completed
run.failed
run.ephemeral_state_deleted
```

---

# 11. Failure and Fallback Paths

## Gmail failures

```text
429 or 5xx
→ exponential backoff
→ maximum three attempts
→ fail message or batch after exhaustion
```

```text
Expired token
→ refresh once
→ retry request
```

```text
Revoked permission
→ fail run
→ mark reauthorization required
```

```text
Partial batch read
→ continue with available emails
→ mark run incomplete
```

## Long-term memory failure

```text
Profile load failure
→ use default profile
→ continue run
→ emit warning event
```

## Episodic memory failure

```text
Episode retrieval failure
→ skip episodic context
→ continue generation
```

Episodic memory is optional context and should not block the core email workflow.

## RAG failure

```text
RAG timeout or module failure
→ retry once
→ return structured empty result
→ generate partial Action Plan
→ expose missing context
```

## Classifier failure

```text
Invalid structured output or timeout
→ retry once
→ if still invalid, route conservatively to RAG
```

## Generation failure

```text
Invalid Action Plan schema
→ repair prompt once
→ if still invalid, fail run or emit degraded informational output
```

## Persistence failure

Use one of:

- transactional write;
- transactional outbox;
- idempotency key based on `run_id + gmail_message_id`;
- retry queue for failed task or episode writes.

---

# 12. Retries, Timeouts, and Idempotency

These are baseline defaults to tune through testing.

| Operation | Baseline timeout | Retry behavior | Blocking? |
|---|---:|---|---|
| Gmail fetch | 10 seconds | Up to 3 retries for transient errors | Yes |
| OAuth refresh | 5 seconds | One attempt | Yes |
| Long-term profile read | 1 second | One fast retry | No |
| Episodic retrieval | 1–2 seconds | No retry or one fast retry | No |
| Intent classifier | 10 seconds | One retry for timeout or schema failure | Yes |
| RAG retrieval | 3–5 seconds | One retry | No; partial-plan fallback |
| Action Plan generation | 20–30 seconds | One repair retry | Yes |
| Task persistence | 3 seconds | Outbox or queue retry | Yes for final success |
| Episode persistence | 3 seconds | Async retry allowed | No |
| Cleanup | Background TTL plus finalizer | Repeated until success | No |

## Idempotency keys

```text
run_id
run_id + gmail_message_id
run_id + operation_name
```

Task writes should use:

```text
idempotency_key = tenant_id:user_id:gmail_message_id:pipeline_version
```

---

# 13. Human Approval

Human approval is not implemented in the current feature.

Current behavior:

```text
Generated task
→ status = system_generated
→ visible in Cowork Daily Brief
→ episodic retrieval disabled
```

Future behavior:

```text
User approves or completes task
→ status = user_approved or completed
→ episodic retrieval enabled
```

Potential future transitions:

```text
system_generated
→ user_approved
→ in_progress
→ completed
```

or:

```text
system_generated
→ rejected
```

High-impact external actions—sending email, changing company systems, purchasing, deleting, or creating calendar events on behalf of the user—must remain outside this baseline unless a human approval gate is added.

---

# 14. Observability and Evaluation

## Development tracing

Development tracing is metadata-only. It may include identifiers, route
labels, latency, citations, and derived output status, but never raw email,
normalized bodies, or full prompts.

Required controls:

- development environment only;
- restricted access;
- automatic TTL;
- environment-level guard preventing accidental production enablement;
- no memory consolidation;
- no semantic indexing;
- no training export.

## Production tracing

Metadata only:

```yaml
run_id: string
tenant_id: string
user_id: string
gmail_message_id: string

route: no_action | direct_plan | retrieve_rag
reason_codes:
  - string

classifier_confidence: number
rag_result_count: integer
retrieval_status: string

generation_status: string
validation_status: string

latency_ms:
  email: integer
  memory: integer
  classifier: integer
  rag: integer
  generation: integer
  persistence: integer

token_usage:
  classifier_input: integer
  classifier_output: integer
  generation_input: integer
  generation_output: integer
```

## Metrics

- Email fetch success rate.
- Run completion rate.
- Actionable-email precision and recall.
- RAG-route precision and recall.
- Unnecessary retrieval rate.
- Missed retrieval rate.
- No-result RAG rate.
- Citation coverage.
- Output-schema success rate.
- Partial-plan rate.
- Classifier latency.
- RAG latency.
- End-to-end latency.
- Cost per processed email.
- Episodic validation and retrieval-eligibility rate.

## Highest-risk classifier error

```text
False negative retrieval:
The email requires company knowledge,
but the classifier routes directly to generation.
```

This should be measured separately.

---

# 15. Output Contract

```yaml
task:
  task_id: string
  run_id: string

  gmail_message_id: string
  gmail_url: string
  source_message_ids:
    - string
  incident_key: string | null

  title: string
  request_summary: string

  actionability: action_required | action_suggested | informational
  route: no_action | direct_plan | retrieve_rag

  priority: low | medium | high | urgent | null
  deadline: datetime | null

  action_plan:
    - step: integer
      instruction: string
      supporting_citation_ids:
        - string

  supporting_documents:
    - citation_id: string
      document_id: string
      title: string
      section: string | null
      url: string
      relevance_score: number

  missing_information:
    - string

  classifier_confidence: number
  generation_confidence: number | null

  validation_status: system_generated
  created_at: datetime
```

---

# 16. Architecture Principles

1. **The Chat Controller owns session orchestration.**<br>
   `@Email` is an executable skill backed by a deterministic tool pipeline;
   Email and RAG remain external modules.

2. **RAG is semantic memory, not the Agent itself.**<br>
   It retrieves company knowledge; it does not own the final task-generation policy.

3. **Memory reads are selective.**<br>
   The bounded session buffer and compact profile are loaded by default;
   episodic and semantic context are conditional on chat intent.

4. **Memory writes are typed and controlled.**<br>
   Long-term writes are explicit. Episodic writes are allowed as `system_generated`, but retrieval remains disabled until validation.

5. **Raw email is transient tool-execution data.**<br>
   It is never durable memory; only minimal derived task output and references may persist.

6. **No unsupported company-specific steps.**<br>
   RAG failure produces a partial plan, not a hallucinated procedure.

7. **Use strict schemas.**<br>
   Classifier, RAG, memory, and task outputs must be machine-validated.

8. **Treat retries as infrastructure behavior.**<br>
   No Reflexion or autonomous reasoning loop is included.

9. **Namespacing and provenance are mandatory.**<br>
   Every memory operation is scoped by tenant, user, mandatory `session_id`,
   `feature: ai_chat`, memory type, and provenance.

10. **Optional context must degrade gracefully.**<br>
    Long-term, episodic, and RAG failures have explicit fallback paths.

---

# 17. Initial Implementation Order

1. Retain the completed V1-M1..M4 stateless Email RAG pipeline.
2. Define chat session, SSE event, memory-context, and tool-result contracts.
3. Implement the Memory Gateway namespace and Chat Session Buffer.
4. Implement compact declarative profile loading for chat turns.
5. Implement chat-summary and `@Email` Action Plan episodic writes.
6. Enforce `retrieval_eligible = false` for unvalidated episodes.
7. Implement Chat API session endpoints and the SSE Streaming Handler.
8. Implement the Chat Controller event loop and allow-listed tool routing.
9. Wrap the Email RAG pipeline behind the `@Email` Skill / Tool Adapter.
10. Render Action Plan cards and idempotent inline lifecycle controls.
11. Add selective episodic and semantic retrieval for chat intent.
12. Add metadata-only events, evaluation, deletion, and governance gates.

---

# 18. Out of Scope

- Reflexion.
- Multi-agent architecture.
- Autonomous ReAct tool loop.
- Automatic email replies.
- External task execution.
- Email attachment processing.
- Automatic long-term preference extraction from emails.
- Automatic semantic-memory ingestion from emails.
- Retrieval of unvalidated system-generated episodes.
- RAG-owned Action Plan generation.
- High-impact writes without approval.

---

# 19. Baseline Summary

```text
AI Chat client creates or resumes a session
→ user sends a message through the Chat API
→ Chat Controller assembles session, profile, eligible episode, and RAG context
→ SSE handler streams assistant deltas
→ explicit @Email request invokes the stateless Email RAG tool
→ Gmail is read into transient tool state
→ deterministic routing produces a grounded Action Plan
→ Action Plan card streams into the active chat thread
→ minimal task output and a retrieval-ineligible episode persist
→ chat turn is recorded and raw email state is purged
→ inline approval or completion may enable later episodic retrieval
```

This document is the baseline target architecture for the Cowork AI Chat
Assistant, its four-type memory engine, and the executable `@Email` tool.

---

# 20. Accepted ADR-004 chat-native target

ADR-004 replaces the superseded AI Chat design above. The target has no
executable in-chat tool and does not turn a standalone PRD-v1 Email run into
an episode. The standalone PRD-v1 Email Agent remains available through its
own APIs, is memory-free, and is not callable from AI Chat.

```mermaid
flowchart TB
    CLIENT["AI Chat client"] --> API["Chat API / SSE"] --> CHAT["Chat Controller"]
    CHAT --> GATE["Memory Gateway"]
    GATE --> SHORT[("bounded session memory")]
    GATE --> PROFILE[("explicit profile")]
    GATE --> EPISODES[("chat summaries + TaskEpisodes")]
    GATE --> RAG["Company RAG"]
    CHAT -->|explicit task/action-plan request only| PROPOSAL["bounded task proposal"]
    PROPOSAL --> EPISODES
```

## 20.1 Chat Controller and task creation

1. Validate tenant, user, and mandatory `session_id`.
2. Assemble bounded session context, explicit profile, eligible episodes, and
   selective company-RAG context through the Memory Gateway.
3. Stream the assistant response.
4. Only after an explicit user request to create a task or action plan, render
   one bounded proposal and persist a TaskEpisode.
5. Record the chat turn and update the session buffer.

Ordinary chat, assistant suggestions, classifier output, background work, and
model-only inference must not create a TaskEpisode. The accepted request schema
has no tool field: strict deserialization rejects retired `tool_choices` as an
unexpected field, before it could select a mailbox, run Gmail work, or write a
PRD-v1 task row.

## 20.2 TaskEpisode lifecycle and access policy

```text
explicit user task request
→ system_generated / retrieval_eligible=false
→ user_approved or completed / retrieval_eligible=true
→ rejected / retrieval_eligible=false
```

Allowed transitions are:

```text
system_generated → user_approved | completed | rejected
user_approved → completed | rejected
```

Storage derives `retrieval_eligible` atomically from the resulting
`validation_status`; callers cannot supply it independently. Approval,
completion, rejection, and single-record deletion require the originating chat
session. Eligible retrieval may cross sessions only for the same tenant, user,
and `feature: ai_chat` scope. User-wide deletion spans that user's AI Chat
sessions and never deletes semantic company RAG.

## 20.3 TaskEpisode contract

```yaml
episode_id: string
record_id: string
tenant_id: string
user_id: string
chat_session_id: string
chat_turn_id: string

task_title: string
minimal_request_paraphrase: string
action_plan:
  - string
rag_citations:
  - document_id: string
    document_title: string
    section: string | null
    source_url: string
missing_information:
  - string

validation_status: system_generated | user_approved | completed | rejected
retrieval_eligible: boolean
source_type: system_generated_chat_task
creation_reason: explicit_user_task_request

created_at: datetime
updated_at: datetime
pipeline_version: string
model_id: string | null
prompt_version: string | null
confidence: number | null
```

`record_id` is an opaque, stable idempotency key derived deterministically
from tenant, user, originating chat session, and originating chat turn. The
derivation must not expose raw user text. The compact payload contains no raw
email, attachment content, full chat transcript, copied RAG chunk, run field,
tool field, Gmail field, mailbox identifier, or foreign key to a standalone
PRD-v1 task row. Optional citations are company-RAG coordinates only.

## 20.4 Four-type memory policy

| Memory type | Read policy | Write policy | Initial storage |
|---|---|---|---|
| Short-term | Bounded active context for its `session_id` | Chat turns only | Redis or in-process state |
| Long-term declarative | Compact profile per relevant turn | Explicit preference or trusted configuration only | PostgreSQL |
| Episodic | Eligible summaries and TaskEpisodes for the same tenant, user, and `feature: ai_chat` | Summaries; TaskEpisodes after explicit user request only | PostgreSQL |
| Semantic | Selective company-knowledge retrieval | No direct Chat Controller write | Company RAG |

Every memory operation carries `tenant_id`, `user_id`, `session_id`,
`feature: ai_chat`, `memory_type`, and `source_id`, and fails closed when the
namespace is missing or inconsistent. The recommended logical key is
`tenant_id / user_id / session_id / feature: ai_chat / memory_type / record_id`.

## 20.5 Privacy, observability, and implementation order

TaskEpisodes, logs, telemetry, fixtures, and semantic indexing must exclude
raw email bodies, attachment content, full chat transcripts, copied RAG chunks,
and full assembled prompts. Metadata-only safety counters for unvalidated
retrieval, cross-tenant access, raw-email violations, rejected-episode
retrieval, and expired-record retrieval must remain zero under test.

Implement the accepted target in this order:

1. Retain the completed standalone PRD-v1 Email Agent without memory changes.
2. Define Chat Controller, session, SSE, Memory Gateway, and TaskEpisode
   contracts against ADR-004.
3. Implement bounded session memory and explicit-only declarative profiles.
4. Implement body-free TaskEpisode persistence with deterministic `record_id`
   and atomic lifecycle-derived eligibility.
5. Implement originating-session mutation/deletion and same-tenant/user
   cross-session eligible retrieval.
6. Implement selective episodic and company-RAG retrieval, then evaluation,
   retention, deletion-audit, and governance gates.


---

# 21. Accepted extension — Projects and AI Chat with user documents ("chat with the PDF")

**Status:** Accepted<br>
**Decision authority:** [ADR-005 — Project-scoped chat documents](../adr/ADR-005-project-scoped-chat-documents.md)<br>
**Extends:** §20 accepted ADR-004 chat-native target<br>
**Does not change:** the standalone PRD-v1 Email Agent, the company RAG corpus,
the declarative profile, or the TaskEpisode trust boundary

This extension lets a user create a **Project**, upload documents into it, open
one or many chat sessions inside it, and ask grounded questions answered from
those documents with page-level citations. It adds a project container and a
second semantic retrieval **plane** — not a fifth memory type.

## 21.1 The Project container

A Project is a user-owned workspace that holds documents and chat sessions.

```text
tenant → user → project → { documents, chat sessions }
```

```yaml
project_id: string
tenant_id: string
user_id: string
name: string
created_at: datetime
updated_at: datetime
```

Rules:

- Every chat session belongs to exactly one project. `project_id` becomes a
  mandatory field of the chat session scope.
- A user always has a default project, created on first use, so an existing
  session flow keeps working without asking the user to choose one.
- Documents are members of a project, not attachments of a session. Upload once,
  every session in that project can ground on it. There is no per-session
  attach/detach step.
- Deleting a project deletes its documents (bytes, extracted text, and vector
  points) and its session state.
- A project never spans users or tenants, and a document is never visible from
  another project.

## 21.2 Source classes and the boundary between them

| Property | Company semantic corpus (existing) | Project document (new) |
|---|---|---|
| Owner | Workspace administrator | The uploading user |
| Provenance | Curated, approved, `document_status: ready` | Self-service upload, unreviewed |
| Ingestion | Offline CLI into `data/extracted/` | Runtime ingestion job |
| Durability | Rebuildable from the repo corpus | User data; not rebuildable |
| Scope key | `tenant_id` | `tenant_id` + `user_id` + `project_id` + `document_id` |
| Store | Company Qdrant collection | Separate project-document Qdrant collection |
| Deletion | Corpus re-index | Explicit deletion + 30-day TTL purge |
| Retrieval trigger | Selective, cue-driven | Deterministic when the project has ready documents |

Both planes are `memory_type: semantic` and are read through retrieval-only
ports. They are never merged: a user upload cannot enter the company corpus,
and company documents are never re-scoped to a project. The namespace carries
`document_scope: company | project_document`; a request that omits or
mismatches it fails closed.

Raw email remains excluded from both planes. Gmail attachment processing stays
out of scope under ADR-003: a document enters this plane only through an
explicit user upload into a project.

## 21.3 Architecture

```mermaid
flowchart TB
    subgraph INGEST["PROJECT DOCUMENT INGESTION PLANE"]
        UP["Project Document API<br/>multipart upload"]
        VALID["Validator<br/>type · size · pages · quota · encryption"]
        QUAR[("Document object store<br/>encrypted · TTL")]
        JOB["Ingestion job<br/>off the request path"]
        DETECT["PdfInspector · DocxExtractor<br/>native text per page"]
        OCR["Mistral OCR<br/>scanned and mixed pages"]
        PCHUNK["Page-aware chunker"]
        UEMBED["Embedding service"]
        UINDEX[("Qdrant project-document collection<br/>tenant · user · project · document filters")]
        UFAIL["failed(reason_code)"]
    end

    UP --> VALID --> QUAR --> JOB --> DETECT
    DETECT -->|native pages| PCHUNK
    DETECT -->|pages needing OCR| OCR --> PCHUNK
    PCHUNK --> UEMBED --> UINDEX
    VALID -. rejected .-> UFAIL
    DETECT -. encrypted · no text .-> UFAIL
    OCR -. attempts or page cap exhausted .-> UFAIL
    UEMBED -. attempts exhausted .-> UFAIL

    subgraph CHATTURN["CHAT TURN INSIDE A PROJECT"]
        CHAT["Chat Controller"]
        GATE["Memory Gateway"]
        DOCPORT["ProjectDocumentPort<br/>retrieval-only"]
        DACL["ACL filter built before embedding<br/>tenant · user · project · ready · unexpired"]
        CTX["Context assembler<br/>labeled sections"]
    end

    CHAT --> GATE --> DOCPORT --> DACL --> UINDEX
    GATE --> COMPANY["Company RAG"]
    DOCPORT --> CTX
    COMPANY --> CTX --> CHAT
```

## 21.4 Ingestion contract and status machine

```text
received → extracting → indexing → ready
any state → failed(reason_code)
ready|failed → deleted
```

```yaml
document_id: string          # opaque; derived from tenant, user, project, content sha256
tenant_id: string
user_id: string
project_id: string

filename: string
media_type: application/pdf | application/vnd.openxmlformats-officedocument.wordprocessingml.document
byte_size: integer
page_count: integer | null
ocr_page_count: integer | null
content_sha256: string

status: received | extracting | indexing | ready | failed | deleted
reason_code: string | null
chunk_count: integer | null

created_at: datetime
updated_at: datetime
expires_at: datetime         # created_at + retention, default 30 days
```

Reason codes reuse the existing ingestion vocabulary and add the cases a
runtime upload introduces:

```text
file_too_large · pdf_page_limit_exceeded · empty_extraction
unsupported_media_type · encrypted_document
ocr_page_limit_exceeded · ocr_failed
quota_exceeded · embedding_unavailable · index_unavailable
```

Rules:

- Validation runs on sniffed content type, not on the filename extension.
- `document_id` is derived from `tenant_id`, `user_id`, `project_id`, and the
  content digest, so re-uploading identical bytes into the same project returns
  the existing record instead of indexing a second copy. The derivation never
  encodes filename or document text.
- Extraction reuses the PRD-v1 `PdfInspector` and `DocxExtractor` and their
  size, page, and encryption guards. Because `PdfInspector` shells out to local
  commands, extraction runs in the job, never on the request path.
- **OCR is enabled.** Pages that `PdfInspector` reports as needing OCR are sent
  to the configured Mistral OCR provider, bounded by the existing
  `max_ocr_pages`, `timeout_seconds`, and `max_attempts` settings. Native pages
  are never re-OCR'd. A document that exceeds the OCR page cap fails as
  `ocr_page_limit_exceeded`; a provider failure after bounded retries fails as
  `ocr_failed`. Partial or empty extraction output is never indexed.
- The upload responds `202` and the job runs off the request path. A chat turn
  never blocks on ingestion.
- Chunking is page-aware: every chunk carries `page_start` and `page_end`
  derived from the extractor's `<!-- Page N -->` markers, then splits on
  paragraph boundaries under the existing size cap.

## 21.5 Retrieval contract

Qdrant is the store for this plane. Unlike the company corpus, there is no
in-repo fallback index: a project document exists only in Qdrant, so an
unavailable vector store degrades the plane explicitly rather than silently
substituting other evidence.

```yaml
# request
tenant_id: string
user_id: string
project_id: string
session_id: string
feature: ai_chat
document_scope: project_document

query: string
document_ids:                 # optional narrowing; default is every ready document in the project
  - string

limits:
  top_k: integer
  min_score: number
  timeout_ms: integer
```

```yaml
# response
chunks:
  - chunk_id: string
    document_id: string
    document_title: string
    section: string | null
    page_start: integer
    page_end: integer
    text: string
    relevance_score: number
    rerank_score: number | null

retrieval_status: success | no_results | timeout | authorization_denied | partial
degraded: boolean
latency_ms: integer
```

ACL is applied first: the `tenant_id`, `user_id`, `project_id`, `ready`-status,
and unexpired conditions are assembled **before** the query is embedded, so a
chunk from another user or another project is never scored. A project with no
ready documents is not an error; it disables the plane for that turn.

**Trigger policy.** When the session's project holds at least one ready
document, the plane is queried on every turn. This is deterministic and does
not depend on cue phrases: the user put the document in the project in order to
ask about it. Company-RAG retrieval keeps its existing selective cue policy and
is unchanged.

## 21.6 Context assembly and conflict precedence

The assembler gains one labeled section, `project_document_evidence`:

```text
current_instruction
> project_document_evidence
> current_company_evidence
> stored_preference
> advisory_episode
```

Scope of authority is explicit, because rank alone is not the whole rule:

- A project document is authoritative for **its own content** — what it says,
  on which page.
- Company RAG remains authoritative for **company procedure and policy**.
- When a project document contradicts current company policy on a procedure,
  both are surfaced with their citations and the conflict is stated. It is
  never silently resolved in favour of the higher rank.
- When no chunk clears the score threshold, the assistant states that the
  answer is not present in the project documents and lists what is missing.
  Invention from parametric knowledge is a validation failure, as in §11.

## 21.7 Memory interaction

| Memory type | Change |
|---|---|
| Short-term | None in content. The session scope gains `project_id`. |
| Long-term declarative | None. Documents are never a preference source. |
| Episodic | Records `project_id`; citations may carry document coordinates. |
| Semantic (company) | None. |
| Semantic (project document) | New plane defined here. |

Episodic **retrieval** scope is unchanged: eligible episodes are still selected
by tenant, user, and `feature: ai_chat` as accepted in PRD-v2 FR-09. Episodes
persist `project_id` so a stricter project-scoped retrieval can be enabled later
without a data migration, but this extension does not change the rule.

A TaskEpisode may cite a project document as coordinates only:

```yaml
rag_citations:
  - citation_scope: company | project_document
    document_id: string
    document_title: string
    section: string | null
    page_start: integer | null
    page_end: integer | null
    source_url: string | null
```

Copied document text, extracted page text, and full chat transcripts remain
banned from episodes, logs, telemetry, and fixtures. Deleting a document does
not delete episodes that cite it; such a citation renders as unavailable.

## 21.8 Suggested internal API surface

```text
POST   /v1/cowork/chat/projects                                        → 201 {project_id}
GET    /v1/cowork/chat/projects                                        list for tenant+user
DELETE /v1/cowork/chat/projects/{project_id}                           204, cascades to documents and sessions
POST   /v1/cowork/chat/projects/{project_id}/documents                 multipart → 202 {document_id, status}
GET    /v1/cowork/chat/projects/{project_id}/documents                 list with status
GET    /v1/cowork/chat/projects/{project_id}/documents/{document_id}   status + reason_code
DELETE /v1/cowork/chat/projects/{project_id}/documents/{document_id}   204, purges index + object + text
POST   /v1/cowork/chat/sessions                                        body gains optional {project_id}
GET    /v1/cowork/chat/sessions?project_id=...                         sessions of one project
```

`POST /sessions` without a `project_id` resolves to the user's default project,
so the existing client contract keeps working. No new SSE event type is
introduced: document evidence is disclosed through the existing
`memory_citation` event, discriminated by `citation_scope`. Ingestion progress
is polled through the document status endpoint, not streamed.

## 21.9 Failure and fallback paths

| Failure | Behavior |
|---|---|
| Validation rejection | `failed(reason_code)` at upload; no job, no retained bytes beyond the failure record |
| Extraction failure | `failed`, document never indexed, chat unaffected |
| OCR provider outage | bounded retries, then `failed(ocr_failed)`; native-text pages are not indexed alone |
| Embedding provider outage | remain `indexing`, bounded retries with backoff, then `failed(embedding_unavailable)` |
| Qdrant unavailable at query time | one retry, then empty result with `degraded: true`; the turn states that document evidence is unavailable |
| Retrieval timeout | one retry, then `timeout` + `degraded: true` |
| Document deleted or expired mid-session | excluded by the retrieval filter; the turn proceeds without it |
| No chunk above threshold | `no_results`; the answer states the documents do not cover the question |

A degraded document plane never falls back to unsourced generation, and never
affects the standalone PRD-v1 Email Agent.

## 21.10 Privacy, retention, and deletion

- Uploaded bytes, extracted text, and OCR output are user-owned durable data:
  encrypted at rest, access-checked on every read, and excluded from logs,
  production telemetry, traces, and test fixtures.
- OCR sends page images to an external provider. That transfer is part of the
  documented upload path and must be disclosed in product copy; OCR output is
  never retained by the pipeline outside the project's own storage.
- Document text never enters the company corpus, TaskEpisodes, the declarative
  profile, or any PRD-v1 Email path.
- **Retention defaults to 30 days** from upload, configurable per tenant.
  Expired documents are excluded from retrieval before ranking and purged by
  the existing background purge mechanism.
- Deletion is supported per document, per project, per user, and feature-wide.
  It purges the object store, the extracted text, and the Qdrant points, and is
  repeatable until every store confirms.
- These metadata-only safety counters must remain zero under test: cross-tenant
  document retrieval, cross-user document retrieval, cross-project document
  retrieval, retrieval of an expired or deleted document, and document text
  appearing in an episode, log, or telemetry field.

## 21.11 Implementation order

1. Project container: contract, storage, default project, and `project_id` on
   the chat session scope.
2. Ingestion contracts and job: validation, extraction, Mistral OCR, page-aware
   chunking, and the status machine — no retrieval yet.
3. Qdrant project-document collection with ACL-first filtering and deletion
   propagation.
4. Deterministic per-turn retrieval and the `project_document_evidence` context
   section with conflict precedence.
5. Grounded page-level citation rendering and `citation_scope` on episodes.
6. Retention, deletion audit, safety counters, and evaluation gates.

## 21.12 Out of scope for this extension

- sharing a project or document with another user or at workspace level;
- promoting a project document into the company corpus;
- image, chart, and table-structure understanding beyond OCR text;
- document editing, annotation, or re-generation;
- scheduled or automatic re-ingestion;
- ingesting Gmail attachments (remains out of scope under ADR-003);
- project-scoped episodic retrieval (deferred; `project_id` is recorded now);
- any executable in-chat tool, including `@Email`.
