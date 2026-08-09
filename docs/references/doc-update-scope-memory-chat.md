# Comprehensive Document Update Scope: Memory System Realignment to AI Chat Assistant & `@Email` Skill Tool

> **Target File**: `docs/references/doc-update-scope-memory-chat.md`
> **Audit Status**: Completed
> **Author**: Research Subagent & Antigravity AI Team
> **Date**: 2026-08-09

---

## 1. Core Architecture Realignment Vision

The project documentation across `PRD-v2-Memory-Extension.md`, `TARGET-ARCHITECTURE.md`, `SPEC-Demo-Frontend.md`, and `master-comparison.md` currently binds the 4-Type Memory System directly to a standalone, asynchronous Email-to-Action-Plan batch pipeline. 

Following team alignment and analysis:
1. **Email RAG is Standalone & Memory-Free**: The Email RAG pipeline (V1-M1..V1-M4) is mostly completed, frontend-functional, and operates as a single-turn, stateless execution. It does NOT require or consume the 4-Type Memory System.
2. **Memory System Powers AI Chat**: The 4-Type Memory System (Working Memory, Declarative Profile, Episodic Memory, Semantic RAG) is decoupled from standalone email runs and reassigned to power a primary **multi-turn AI Chat Assistant**.
3. **`@Email` as an Executable Skill/Tool**: Inside the AI Chat Assistant interface, `@Email` is defined as an executable skill/tool. When a user invokes `@Email` (or requests email action plans), the Chat Controller executes the Email RAG pipeline and renders the generated Action Plan directly into the active chat thread as a rich component.
4. **Automatic Memory Recording**: The Chat Memory Engine automatically records conversation turns (including the `@Email` Action Plan outputs) as part of session **Working Memory** and **Episodic Memory** (defaulting to `validation_status = system_generated` and `retrieval_eligible = false` until human-validated in chat), while strictly preserving raw email ephemerality.

---

## 2. File-by-File Exact Modification Scope

### File 1: `PRD-v2-Memory-Extension.md`

| Section Citation | Current State | Modification Instructions |
|---|---|---|
| **Title & Metadata** (`L1-16`) | "Cowork Agent — Long-Term and Episodic Memory Extension" (Version 2.0, centered on Email Action Plan). | **Modify**: Rename title to *"Cowork Agent — Memory Extension for AI Chat Assistant & Executable `@Email` Tool"*. Update metadata table to reflect AI Chat Assistant as the primary memory target. |
| **§1. Executive Summary** (`L19-60`) | Describes extending the deterministic Email-to-Action-Plan workflow with memory. | **Modify**: Reframe Executive Summary. State explicitly that PRD-v2 decouples Memory from standalone Email RAG and reassigns it to the multi-turn AI Chat Assistant. Define `@Email` as an in-chat executable skill. Update lifecycle diagram to: `User Chat Turn → Memory Engine Context Assembly → Response & Tool Execution (@Email) → Action Plan Rendered in Chat → Record Turn & Episode in Memory → Purge Ephemeral Raw Email`. |
| **§2. Product Hypothesis** (`L62-79`) | Focuses on memory improving email prioritization and plan consistency across email runs. | **Modify**: Shift hypothesis to multi-turn conversational AI Chat. Memory improves chat continuity, preference adherence, persona alignment, and cross-session task context. Retain raw email ephemerality rule. |
| **§3. Problem Statement** (`L80-96`) | States that every email run starts without persistent knowledge of the user. | **Modify**: Reframe problem statement: a stateless chat assistant forgets user preferences, previous chat decisions, enterprise RAG documents, and past `@Email` tool execution plans across chat sessions. |
| **§4. V2 Goal & Value Loop** (`L97-114`) | Details `@Email` invocation leading to email processing, profile loading, episode write, and task persistence. | **Modify**: Replace value loop diagram and flow with: `Chat Message Received → Chat Controller → Memory Gateway (Working + Profile + Episodic + RAG) → LLM Response / Tool Trigger → Execute @Email Skill (if invoked) → Render Action Plan Card in Chat Thread → Record Chat Turn & Episode → Delete Ephemeral Email Payload`. |
| **§5. Goals** (`L116-136`) | 13 goals focused on Email Action Plan runs reading/writing memory. | **Modify**: Re-scope goals: (1) Memory Gateway for AI Chat, (2) Namespace supporting `feature: ai_chat` and `session_id`, (3) Multi-turn Chat Working Memory, (4) Long-term profile for chat persona & user preferences, (5) Store `@Email` Action Plans as Chat Episodic records, (6) In-chat task validation controls (`Approve`/`Complete`/`Reject`), (7) Preserve raw email ephemerality during tool execution. |
| **§6. Non-Goals** (`L137-158`) | Lists out-of-scope items. | **Modify**: Add: standalone Email pipeline memory integration (Email pipeline remains memory-free); background auto-ingestion of emails into memory; autonomous email sending. |
| **§7. Memory Types Table** (`L159-169`) | Short-term = Current email run state; Long-term = Profile; Episodic = Task history; Semantic = RAG. | **Modify**: Update table definitions: <br>• **Short-term**: Active chat session turn history + transient `@Email` tool run state.<br>• **Long-term Declarative**: User persona, language, tone, explicit preferences.<br>• **Episodic**: Chat conversation thread summaries + derived `@Email` Action Plan outputs.<br>• **Semantic**: Enterprise document corpus accessible via RAG in chat. |
| **§8. Core User Stories** (`US-01..US-08`, `L170-205`) | Stories framed as email action plan runs remembering preferences and past tasks. | **Modify**: Re-write US-01..US-08 for AI Chat: <br>• *US-01/02*: As a user, I want the AI Chat Assistant to follow my stored preferences across chat sessions.<br>• *US-03/04/05/06*: As a user, I want to execute `@Email` inside chat, see the rendered Action Plan, and approve/complete it so it becomes retrievable episodic memory for future chat questions.<br>• *US-08*: As a user, I want raw emails fetched via `@Email` tool to stay strictly ephemeral and never be saved as durable memory. |
| **§9. Memory Architecture** (`L207-230`) | Mermaid diagram showing "Agent Core (Email)" reading/writing Memory Gateway. | **Modify**: Replace "Agent Core" block with **"AI Chat Controller"**. Add **"`@Email` Executable Skill / Tool"** block connected as a tool called by Chat Controller. |
| **§10. Memory Principles** (`L234-248`) | Principle 6: Raw emails are not durable memory. Principle 8: Tenant/user scoped. | **Modify**: Expand Principle 6: Raw email content accessed via `@Email` tool is strictly transient; only derived Action Plans rendered in chat are stored as episodes. Principle 8: Scoped to `tenant_id`, `user_id`, `session_id`, and `feature: ai_chat`. |
| **§11. Functional Requirements** (`FR-01..FR-18`, `L249-623`) | Detailed requirements bound to `feature: email_action_plan`. | **Modify/Add**: <br>• **FR-01 (Memory Gateway)**: Serve Chat Controller.<br>• **FR-02 (Namespace)**: Change `feature: email_action_plan` to `feature: ai_chat`, add mandatory `session_id`.<br>• **FR-03/04/05 (Declarative Profile)**: Add chat style/persona fields (brevity, response tone, default tool permissions). Load profile per chat message turn.<br>• **FR-06 (Episodic Write)**: Re-target episode writes to record: (1) Chat conversation turn summaries, and (2) Action Plans generated via `@Email` tool execution in chat.<br>• **FR-07/08 (Episode Lifecycle & Eligibility)**: Maintain `system_generated -> false` rule for chat episodes until validated via in-chat UI controls.<br>• **FR-09/10/11 (Episodic Retrieval)**: Trigger selective episodic retrieval based on chat turn intent.<br>• **FR-12 (Context Integration)**: Context assembler builds prompt for Chat LLM: `System Persona + Compact Profile + Validated Episodic Hits + RAG Chunks + Active Chat Session Buffer (Working Memory)`.<br>• **FR-18 (Failure Behavior)**: Degradation in memory fallback must allow Chat Assistant to continue functioning gracefully. |
| **§12. User Approval & Completion** (`L625-664`) | Describes product controls for validating task episodes. | **Modify**: Specify that approval/completion/rejection controls are rendered directly on the `@Email` Action Plan components inside the Chat Thread UI. |
| **§16. Acceptance Criteria** (`L748-773`) | 20 criteria for memory-enabled Email Action Plans. | **Modify**: Reframe criteria to validate: (1) Chat Controller consumes Memory Gateway, (2) `@Email` executes as an in-chat tool, (3) Rendered Action Plans are saved as `system_generated` episodes, (4) In-chat approval enables episodic retrieval, (5) Raw emails remain absent from durable storage. |
| **§17. Delivery Milestones** (`L775-824`) | Milestones 1–6 for memory extension. | **Modify**: Update Milestones `V2-M1` through `V2-M6` to align with Chat Memory Engine and `@Email` tool integration. |
| **§21. Baseline Summary** (`L883-900`) | Step-by-step summary of `@Email` memory run. | **Modify**: Replace summary with AI Chat Assistant execution sequence including `@Email` tool execution and turn recording. |

---

### File 2: `TARGET-ARCHITECTURE.md`

| Section Citation | Current State | Modification Instructions |
|---|---|---|
| **Header & §1. Product Hypothesis** (`L1-46`) | Describes deterministic single-agent workflow for Email Action Plan with embedded Memory & RAG. | **Modify**: Reframe Level 2 target architecture to be **AI Chat Assistant with Executable `@Email` Tool & 4-Type Memory System**. Update primary use case to multi-turn conversational chat with memory and skill invocation. |
| **§2. Overall Production Architecture** (`L48-230`) | Mermaid diagram and description with `@Email Command` entering `Cowork Feature API` -> `Agent Core`. | **Modify**: Update Architecture Diagram (§2):<br>1. **Entry Plane**: Replace `@Email Command` entry with **`AI Chat Client / UI`** connecting to **`Chat API Controller`** via **`Streaming SSE Handler`**.<br>2. **Control Plane**: Introduce **`Chat Controller & Orchestrator`** that owns chat session state, calls Memory Gateway, and invokes LLMs/Tools.<br>3. **Tool Plane**: Add **"`@Email` Skill / Tool Adapter"**. When invoked, it executes the standalone Email RAG pipeline (Email Reader -> Classifier -> Email RAG -> Action Plan Generator) statelessly.<br>4. **Memory Plane**: Connect Memory Gateway to `Chat Controller`. Short-Term Memory stores `Chat Session Buffer` (`session_id`). Episodic Memory stores validated chat turns & approved Action Plans. |
| **§3. Email Module Architecture** (`L231-341`) | Email Module as standalone primary workflow driver. | **Modify**: Re-position Email Module as a backend component wrapped by the `@Email` Skill Tool. Emphasize that execution is triggered via Chat Controller and returns formatted Action Plan DTO to the active chat session. |
| **§4. Agent Core & Intent Classifier** (`L342-502`) | Describes Email Agent Core running classifier and router for email batch. | **Modify**: Divide into two distinct operational modes:<br>1. **Chat Controller Event Loop**: Multi-turn dialogue handling, context assembly, tool routing (detecting `@Email` invocation), SSE token streaming.<br>2. **`@Email` Tool Pipeline**: The existing deterministic email classification, RAG retrieval, and Action Plan generation pipeline (retained from V1-M4). |
| **§5. Four-Type Memory System Architecture** (`L503-659`) | 4-Type Memory System diagram and policies tied to Email runs. | **Modify**: <br>• Re-draw diagram showing Memory Gateway serving `Chat Controller`.<br>• **Short-Term Memory**: Redis / In-memory TTL storing `session_id` turn history + active tool execution state.<br>• **Long-Term Profile**: `user_profile` table storing user persona and assistant rules.<br>• **Episodic Memory**: `task_episode` table storing chat summaries and tool-generated Action Plans (`system_generated`).<br>• Update namespace key format: `tenant_id / user_id / session_id / feature: ai_chat / memory_type / record_id`. |
| **§7. Agent and Memory Interaction Flow** (`L785-840`) | Diagram showing step-by-step email run reading profile, classifying, calling RAG, generating, writing episode. | **Modify**: Replace with Chat Interaction Flow:<br>`User Chat Message → Chat Controller → Read Memory (Profile + Episodic + RAG) → LLM Assistant → [Detect @Email Tool Call] → Execute @Email Pipeline → Render Action Plan Card to SSE Stream → Write Chat Turn & Task Episode (system_generated) → Purge Raw Email State`. |
| **§8. State Ownership Table** (`L843-857`) | Table listing component state ownership. | **Modify**: Add rows for `Chat Controller` (owns chat session turns, SSE connection), `Chat Session Buffer` (owns short-term turn history), and `@Email Tool` (owns transient email fetch state). |
| **§10. Suggested Internal Service APIs** (`L950-1001`) | Endpoints: `POST /v1/cowork/email-action-plan/runs`, `POST /v1/memory/...` | **Add/Modify**: Add Chat API Endpoints:<br>• `POST /v1/cowork/chat/sessions` (Create chat session)<br>• `POST /v1/cowork/chat/sessions/{session_id}/messages` (SSE streaming chat message)<br>• `GET /v1/cowork/chat/sessions/{session_id}/messages` (Get conversation history)<br>• `POST /v1/cowork/tools/email` (Internal execution endpoint for `@Email` tool)<br>Update internal events to include `chat.message.received`, `chat.tool.invoked`, `chat.message.completed`. |
| **§16. Architecture Principles** (`L1293-1325`) | Principle 1: Agent Core owns orchestration; Email & RAG are tools. | **Modify**: Update Principle 1: Chat Controller owns session orchestration; `@Email` is an executable tool skill. Principle 5: Raw email remains transient tool execution data, never durable memory. |
| **§17 & §19. Implementation Order & Baseline Summary** (`L1327-1384`) | Step 1-15 implementation order centered on Email Agent. | **Modify**: Update summary and order to reflect Chat Controller, SSE Streaming, Chat Memory Engine, and `@Email` Tool integration. |

---

### File 3: `SPEC-Demo-Frontend.md`

| Section Citation | Current State | Modification Instructions |
|---|---|---|
| **Header & §1. Purpose** (`L1-29`) | Demo value loop: `Connect Gmail → @Email → watch the Run → see Tasks → approve/complete → see memory improve next Run`. | **Modify**: Re-align purpose and value loop to:<br>`Connect Gmail → Open AI Chat Assistant → Multi-turn Chat / Invoke @Email Tool → See rendered Action Plan in Chat Thread → Approve/Complete in Chat → Observe Memory Context active in Chat`. |
| **§2. Positioning and Hard Rules** (`L30-46`) | Rule 1: Client of FastAPI backend. Rule 3: Raw email bodies never rendered. | **Modify**: Retain all hard privacy rules. Add: AI Chat UI uses standard streaming SSE / polling client logic and renders tool execution outputs as structured embedded components. |
| **§3. Delivery Structure (Increments A & B)** (`L47-71`) | Table of screens: Connect, Run, Tasks, Task detail, Knowledge, Run audit, Preferences, Task lifecycle, Memory insight, Deletion. | **Modify**: Restructure Increments:<br>• **Increment A (Core Chat & Email Tool)**:<br>  - `AI Chat Assistant`: Chat thread UI (`st.chat_input`), session sidebar, message history.<br>  - `@Email` Skill Execution: In-chat tool trigger that runs Email RAG and renders Action Plan cards with citation chips directly inside the chat thread.<br>  - `Connect` & `Knowledge` screens (retained for connection status & corpus inspection).<br>• **Increment B (Chat Memory & Transparency)**:<br>  - `Preferences`: Explicit user profile editor.<br>  - `In-Chat Task Controls`: Inline `Approve` / `Complete` / `Reject` buttons on `@Email` Action Plan components.<br>  - `Memory Transparency`: In-chat memory recall badges showing active profile rules and retrieved episodic hits used by the assistant. |
| **§5. Information Architecture** (`L85-95`) | 6-screen navigation hierarchy (Connect, Run, Tasks, Knowledge, Memory, Run audit). | **Modify**: Re-organize Navigation Hierarchy:<br>```text<br>Cowork Demo<br>├── 1. AI Chat Assistant  (Primary Screen: Multi-turn Chat, @Email tool trigger, inline Action Plan cards)<br>├── 2. Connect            (Gmail OAuth & Mailbox Connection management)<br>├── 3. Knowledge          (RAG Corpus Readiness & Document Inspection)<br>├── 4. Memory             (Preferences Profile Editor, Episode Provenance, Deletion)<br>└── 5. Run audit          (Chat & Tool Telemetry, Latency, SSE Stream Debug)<br>``` |
| **§6. UX and Quality Bar** (`L97-132`) | Streamlit UX guidelines, loading/empty/error states. | **Add/Modify**: Add Chat UX guidelines: (1) `st.chat_message` for user and assistant, (2) Smooth streaming token updates or `st.status` tool execution indicators, (3) Formatted card container for `@Email` Action Plan outputs with expandable citation details, (4) In-thread memory recall indicators. |
| **§7. Backend API Contract Assumptions** (`L133-152`) | API table: `/v1/mail-todo/runs`, `/v1/tasks`, `/v1/mail-todo/knowledge/chat`, etc. | **Modify**: Add Chat API Endpoints:<br>• `POST /v1/cowork/chat/sessions`<br>• `POST /v1/cowork/chat/sessions/{session_id}/messages` (SSE)<br>• `GET /v1/cowork/chat/sessions/{session_id}/messages`<br>• In-chat Task transition endpoints. |
| **§8. Acceptance Criteria** (`L153-186`) | Increment A & B acceptance criteria for email runs and task detail screens. | **Modify**: Reframe Acceptance Criteria:<br>• *Increment A*: User can chat with AI Assistant; invoking `@Email` triggers tool pipeline and renders Action Plan with citation chips in chat thread; duplicate clicks send idempotency key; raw email never rendered.<br>• *Increment B*: Preferences profile editable; in-chat approval/completion updates episode status and enables episodic memory recall in subsequent chat messages; memory transparency badges display active context sources. |
| **§9. Live Verification Plan** (`L187-212`) | Verification steps walking `@Email` run creation. | **Modify**: Update verification steps to test multi-turn chat session creation, `@Email` tool invocation inside chat, rendering Action Plan card, approving task in chat, and verifying episodic memory recall in next message turn. |

---

### File 4: `master-comparison.md`

| Section Citation | Current State | Modification Instructions |
|---|---|---|
| **§0. Alignment Decision** (`L20-77`) | Describes corrected execution shape for Email Action Plan workflow. | **Modify**: Add Alignment Decision section updating the architectural target: Standalone Email RAG is completed as a stateless service (V1-M1..M4); PRD-v2 Memory System is assigned to power the **AI Chat Assistant**, with `@Email` integrated as an executable chat skill. |
| **Step 2. Current vs Authoritative Target Gap Table** (`L201-236`) | 25 comparison rows mapping email workflow gaps. | **Modify/Add Rows**: <br>• Add row: `AI Chat Controller` (Missing -> Add Chat Controller & SSE streaming handler).<br>• Add row: `Chat Session Working Memory` (Missing -> Add Redis/In-memory Chat Session Buffer).<br>• Add row: `@Email Chat Skill Tool` (Missing -> Wrap Email RAG pipeline as executable chat tool).<br>• Modify row: `Memory Gateway` (Re-route namespace and policy engine to serve Chat Controller). |
| **Step 4. Recommended Changes (Keep/Modify/Add/Remove)** (`L374-437`) | Lists components to Keep, Modify, Add, Remove. | **Modify**: <br>• **Keep**: Standalone Email RAG pipeline, Gmail OAuth adapter, Hybrid Semantic RAG.<br>• **Modify**: Memory Gateway (serve Chat API), Episodic Store (store chat turns & tool outputs).<br>• **Add**: Chat API Controller, SSE Streaming Handler, Chat Session Working Memory (`session_id`), `@Email` Skill Tool Wrapper, In-Chat Task Validation UI. |
| **Step 5. Target Diagrams** (`L440-600`) | Diagrams 1–5 showing Email execution, Agent state machine, 4-Type Memory, Content boundaries, Migration order. | **Modify Diagrams**: <br>• **Diagram 1 (Control & Execution Flow)**: Show Chat Client → Chat Controller → Memory Gateway & LLM → `@Email` Tool Execution → Render Action Plan Card.<br>• **Diagram 3 (Four-Type Memory)**: Show Memory Gateway serving Chat Controller with `Chat Session Buffer` as Working Memory.<br>• **Diagram 5 (Migration Order)**: Show V1-M1..M4 (Email RAG baseline) → V2-M1..M6 (Chat Memory Engine & Tool Integration) → DEMO (Streamlit Chat Frontend). |
| **Step 6. Target-Aligned Contracts** (`L602-915`) | Contracts for `EphemeralEmailEnvelope`, `EmailRouteDecision`, `MemoryContextRequest`, `ActionPlanOutput`, `TaskEpisode`, `TraceEvent`. | **Add/Modify Contracts**: <br>• **Add**: `ChatMessageRequest` (session_id, user_message, tool_choices).<br>• **Add**: `ChatMessageStreamEvent` (SSE event shape: delta, tool_call, tool_output, memory_citation).<br>• **Modify `MemoryContextRequest`**: Add `session_id: string`, update `feature: ai_chat`.<br>• **Modify `TaskEpisode`**: Add `chat_session_id`, `chat_turn_id`, `source_tool: "@Email"`. |
| **Step 7. Final Change Plan & Milestones** (`L918-1307`) | Decomposed milestones `V1-M1..M4`, `V1-H`, `V2-M1..M6`, `DEMO`. | **Modify Milestone Definitions**: <br>• `V1-M1..M4` & `V1-H`: Retain as completed/hardened standalone Email RAG baseline.<br>• `V2-M1`: Memory Gateway & Chat Session Working Memory (`feature: ai_chat`, `session_id`).<br>• `V2-M2`: Long-Term Declarative Profile Store for AI Chat persona & user config.<br>• `V2-M3`: Chat Turn & `@Email` Action Plan Episodic Persistence (`system_generated`, `retrieval_eligible=false`).<br>• `V2-M4`: AI Chat Controller, SSE Streaming Handler, and `@Email` Skill Tool Execution.<br>• `V2-M5`: Selective Episodic & RAG Retrieval for Multi-Turn AI Chat Dialogue.<br>• `V2-M6`: Memory Evaluation & Governance for AI Chat.<br>• `DEMO`: Streamlit AI Chat Showcase with embedded `@Email` Tool Execution & Memory Panels. |

---

## 3. Summary Table of Component Ownership & API Endpoints

### Component Ownership Realignment

| Component | Legacy Assignment | Reassigned Target Architecture |
|---|---|---|
| **Short-Term Working Memory** | Ephemeral Email Run state (`run_id`) | Active Chat Session Buffer (`session_id` + turn history) |
| **Long-Term Declarative Memory** | Email output preferences | AI Chat persona, output style, user role, and explicitly saved facts |
| **Long-Term Episodic Memory** | Email task run history | Chat conversation summaries & validated `@Email` Action Plan outputs |
| **Semantic Memory (RAG)** | Email knowledge gap retrieval | Enterprise corpus RAG accessible directly by AI Chat Assistant |
| **Email RAG Pipeline** | Primary standalone product entry point | Executable skill/tool (`@Email`) callable within AI Chat |
| **Memory Gateway** | Scoped to `feature: email_action_plan` | Scoped to `feature: ai_chat` with mandatory `session_id` |

### New API Endpoint Inventory

```text
# AI Chat Session & Messaging (SSE)
POST   /v1/cowork/chat/sessions                         # Create new chat session
GET    /v1/cowork/chat/sessions                         # List user chat sessions
GET    /v1/cowork/chat/sessions/{session_id}/messages   # Get chat turn history
POST   /v1/cowork/chat/sessions/{session_id}/messages   # Send chat message (SSE stream response)

# Executable Tool API
POST   /v1/cowork/tools/email                           # Execute @Email skill pipeline statelessly

# Chat Memory & Preference Management
GET    /v1/memory/profile                               # Read declarative profile
PUT    /v1/memory/profile                               # Update declarative profile
GET    /v1/memory/episodes                              # Query episodic memory
POST   /v1/memory/episodes/{episode_id}/transition      # Approve / Complete / Reject task episode
DELETE /v1/memory/episodes/{episode_id}                 # Delete specific episode
```

---

*Report generated and saved to docs/references/doc-update-scope-memory-chat.md.*
