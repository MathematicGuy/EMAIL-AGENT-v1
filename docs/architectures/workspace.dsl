workspace "Cowork Agent" "C4 model of the Cowork Agent system as it is implemented on the dev branch." {

    !identifiers flat
    !impliedRelationships false

    model {

        # ------------------------------------------------------------------
        # Level 1 - People
        # ------------------------------------------------------------------

        knowledgeWorker = person "Knowledge Worker" "Signs in with Google, scans unread mail into body-free action plans, chats with the assistant, and uploads project documents."
        corpusAdmin = person "Corpus Administrator" "Curates the committed company knowledge corpus by running the offline ingestion CLI."

        # ------------------------------------------------------------------
        # Level 1 - External software systems
        # ------------------------------------------------------------------

        googleIdentity = softwareSystem "Google Identity Platform" "Issues the OAuth 2.0 authorization codes and refresh tokens that own user identity." "External"
        microsoftIdentity = softwareSystem "Microsoft Identity Platform" "Issues OAuth 2.0 + PKCE tokens for linked Outlook mailboxes." "External"
        gmail = softwareSystem "Gmail API" "Read-only mailbox source (gmail.readonly). Bodies are read transiently and never persisted." "External"
        microsoftGraph = softwareSystem "Microsoft Graph" "Read-only Outlook mailbox source (Mail.Read) for mailboxes linked to a Google-owned identity." "External"
        googleCalendar = softwareSystem "Google Calendar API" "Creates calendar events for the single registered chat tool, under a per-user grant kept separate from the Gmail grant." "External"
        llmProviders = softwareSystem "LLM Providers" "Gemini, OpenRouter, Mimo and Mistral chat-completion endpoints reached through one provider factory with fallback ordering." "External"
        retrievalProviders = softwareSystem "Embedding & Reranking Providers" "Jina AI embeddings and cross-encoder reranker, Gemini embeddings, and Cohere reranking." "External"
        mistralOcr = softwareSystem "Mistral OCR API" "Extracts text from scanned PDF and OOXML pages that carry no native text layer." "External"
        langfuse = softwareSystem "Langfuse" "Receives span and generation traces. Raw email bodies, chunk text and prompts are prohibited fields." "External"
        threatIntel = softwareSystem "Threat Intelligence Services" "ClamAV, VirusTotal, Google Web Risk and abuse.ch lookups used to screen attachments and links." "External"

        # ------------------------------------------------------------------
        # Level 2 - The Cowork Agent system and its containers
        # ------------------------------------------------------------------

        coworkAgent = softwareSystem "Cowork Agent" "Turns unread mail into structured, body-free action plans and sustains grounded multi-turn chat over typed memory and enterprise knowledge." {

            spa = container "Web Application" "Single-page client: dashboard, mail scan protocol, chat with live reasoning and execution-trace drawer, project documents, DOCX/PDF viewer." "React 19 / TypeScript / Vite / Tailwind 4" "WebBrowser"

            api = container "Control Plane API" "Composition root and HTTP/SSE surface. Owns identity, routing, both product workflows, retrieval and persistence access." "Python 3 / FastAPI (mail-todo-api)" {

                group "Email Action Plan" {
                    mailApi = component "Mail-Todo API" "Digest run lifecycle, mailbox connections and OAuth callbacks on /v1/mail-todo." "FastAPI routers (api/digest_runs.py, api/mailboxes.py)"
                    emailWorkflow = component "Email Action Plan Workflow" "Single-turn, memory-free pipeline: classify, route, retrieve, generate, validate, persist." "features/email_action_plan/workflow.py"
                    routeResolver = component "Route Resolver & Policy Guards" "Resolves NO_ACTION, DIRECT_PLAN or RETRIEVE_RAG from the classifier decision and forced policy guards." "features/email_action_plan/routing.py"
                    planValidator = component "Action Plan Validator" "Rejects plans that leak body text or fail the body-free output contract." "features/email_action_plan/validation.py"
                    mailboxAdapter = component "Provider-Routing Mailbox Adapter" "Resolves the stored connection provider and dispatches reads to one mailbox adapter." "integrations/mailbox/router.py"
                    gmailAdapter = component "Gmail Adapter" "Normalizes Gmail threads into EphemeralEmailEnvelope with five-message reply-chain aggregation." "integrations/gmail/provider.py"
                    outlookAdapter = component "Outlook Adapter" "Normalizes Microsoft Graph messages into the same envelope. SQLite mode only." "integrations/outlook/provider.py"
                    securityScanner = component "Email Security Scanner" "Screens attachments and links: magic-byte inspection, hash lookup, redirect resolution, sandboxed extraction." "integrations/security"
                }

                group "AI Chat" {
                    chatApi = component "Chat API & SSE Stream" "Multi-turn chat endpoints, SSE token/reasoning/citation events and mail-scan submission on /v1/cowork/chat." "api/chat.py"
                    chatController = component "Chat Controller" "Owns the turn: classify, retrieve, assemble context, generate, persist, and emit the bounded execution trace." "features/ai_chat/controller.py"
                    intentClassifier = component "Intent Classifier & Route Resolver" "Resolves CHAT, RAG, TOOL, RAG_TOOL or CLARIFY, with deterministic downgrades when a capability is disabled." "features/ai_chat/intent"
                    memoryGateway = component "Memory Gateway" "Namespace-checked read/write across short-term, declarative, episodic and semantic memory." "features/ai_chat/memory_gateway.py"
                    mailScanRecon = component "Mail-Scan Reconciliation" "Transport-free reconciliation of DesiredMailActivity into durable history and the session buffer." "features/ai_chat/mail_scan_reconciliation.py"
                    episodeSettlement = component "Task Episode Settlement" "Approve/reject/complete transitions and retrieval-eligibility for chat-native TaskEpisodes." "features/ai_chat/task_episode_settlement.py"
                    toolRegistry = component "Chat Tool Registry" "One specs()/run() boundary and a per-turn argument fill. run() never raises: unknown name, schema violation, handler error and timeout all return ToolResult(ok=False)." "features/ai_chat/tools"
                    calendarAdapter = component "Google Calendar Adapter" "The single registered tool, plus its per-user OAuth connection store. Event ids derive from the turn idempotency key, so a retried turn returns the existing event." "integrations/google_calendar"
                }

                group "Retrieval" {
                    hybridRetriever = component "Hybrid Retriever" "Dense cosine search fused with Okapi BM25 through Reciprocal Rank Fusion (k=60)." "integrations/rag/hybrid.py, bm25.py, rrf.py"
                    queryTransform = component "Query Transform & Diversification" "Query guard, domain prefix expansion, HyDE hypothetical documents and MMR diversification." "integrations/rag/query_transform.py, mmr.py"
                    reranker = component "Cross-Encoder Reranker" "Precision reranking of retrieval candidates before the evidence gate." "integrations/rag/jina_reranker.py, reranker.py"
                    embeddingAdapter = component "Embedding Adapter" "Provider-neutral embedding calls with dimension and model configuration." "integrations/rag/embeddings.py"
                    chunker = component "Structure-Aware Chunker" "Heading-promoting hierarchical chunking with atomic tables and page coordinates carried through." "integrations/rag/markdown_chunking.py"
                    turbovecStore = component "Turbovec Store Adapter" "In-process 4-bit TurboQuant index load, query and persistence." "integrations/rag/turbovec_memory.py"
                    projectDocRetriever = component "Project Document Retriever" "Workspace/user/project-isolated retrieval over per-project indexes. Never falls back to the company index." "integrations/rag/project_documents.py"
                }

                group "Documents & Reports" {
                    projectApi = component "Project & Document API" "Project CRUD, document register/upload/complete, status polling and document-health." "api/projects.py"
                    knowledgeApi = component "Knowledge & Raw Document API" "Company knowledge readiness and the editable raw-document surface on /api/v1/raw-documents." "api/knowledge.py"
                    reportApi = component "Report Artifact API" "Lists, downloads and PDF-exports generated report artifacts on /api/v1/reports." "api/reports.py"
                    reportPdfRenderer = component "Report PDF Renderer" "Renders the explicit Markdown subset to PDF with bundled Noto Sans and extractable Vietnamese text." "integrations/report_pdf (fpdf2)"
                }

                group "Platform" {
                    compositionRoot = component "Composition Root" "Builds one frozen CoworkRuntime value read through runtime(request)." "composition.py"
                    identity = component "Identity & Session Security" "Google-owned identity, opaque sessions, encrypted OAuth token storage and PKCE state." "identity.py, api/dependencies.py, security"
                    settings = component "Runtime Settings" "Pure parsers over a supplied mapping or os.environ; .env is loaded once at executable boundaries." "config.py"
                    llmFactory = component "LLM Provider Factory" "Selects and orders chat-completion providers, shares prompts and parsers, and degrades on provider failure." "integrations/llm"
                    repositories = component "Persistence Repositories" "Postgres and SQLite implementations behind one repository port set, plus migrations." "persistence/repositories"
                    observability = component "Observability" "@observe instrumentation of controller, provider and memory operations." "observability.py"
                    evaluationApi = component "Evaluation Job API" "Queues and reports batch evaluation jobs. Registered only when evaluation is enabled." "api/evaluation_jobs.py"
                }
            }

            worker = container "Background Worker" "Out-of-process poller that claims queued digest and document jobs, sweeps retention, and recovers interrupted runs." "Python 3 asyncio (mail-todo-worker)" {
                digestPoller = component "Digest Poller" "Claims queued mail digest runs and executes the same email workflow out of the request path." "orchestration/worker.py"
                documentWorker = component "Project Document Worker" "Extraction, OCR, page-aware chunking and per-project index writes with a liveness heartbeat." "orchestration/project_document_worker.py"
                recoverySweeper = component "Run & Document Recovery" "Re-queues runs and documents left in-flight by a crashed process." "orchestration/recovery.py, document_recovery.py"
                retentionSweeper = component "Retention Sweeper" "Purges expired documents, chunks and index ids past the configured retention window." "features/ai_chat/retention.py"
            }

            ingestionCli = container "Knowledge Ingestion CLI" "Offline batch tool that converts administrator-supplied documents into the committed Markdown corpus." "Python 3 CLI (mail-todo-ingest-knowledge)" {
                ingestionService = component "Ingestion Service" "Discovers files, enforces symlink and directory isolation, resolves slug collisions and gates on content hash." "integrations/knowledge_ingestion/service.py"
                extractors = component "Document Extractors" "DOCX, PDF and plain-text extraction with page inspection and binary date harvesting." "integrations/knowledge_ingestion (docx_extractor, pdf_inspector, text_extractor)"
                sanitizer = component "Sanitizer & Frontmatter Writer" "Strips unsafe constructs and writes closed six-field YAML frontmatter atomically." "integrations/knowledge_ingestion/text_sanitizer.py"
                manifest = component "SHA-256 Manifest" "Tracks per-source content hashes so unchanged documents are skipped." "integrations/knowledge_ingestion/manifest.py"
            }

            controlPlaneDb = container "Control-Plane Database" "Identity, mailbox and calendar connections, digest runs, tasks, chat sessions, turns, task episodes, projects and document chunks." "PostgreSQL 16 (local Docker or hosted Supabase) or SQLite" "Database"
            vectorIndex = container "Turbovec Vector Index" "In-process 4-bit TurboQuant indexes: one company index plus one index per project." "TurboQuant .tvim files" "Database"
            knowledgeCorpus = container "Company Knowledge Corpus" "The authoritative ground-truth knowledge base. Committed to the repository; email content never enters it." "Committed Markdown (data/extracted/*.md)" "FileStore"
            documentStore = container "Private Document Store" "User-owned uploaded project documents. Encrypted at rest, access-checked on every read, retention-bounded." "Supabase Storage or local filesystem" "FileStore"
            reportStore = container "Report Artifact Store" "Generated report artifacts reached only through the ReportFilename rule." "Local filesystem (data/reports/)" "FileStore"
        }

        # ------------------------------------------------------------------
        # Level 1 relationships - people and external systems
        # ------------------------------------------------------------------

        knowledgeWorker -> coworkAgent "Scans mail into action plans, chats over typed memory, and uploads project documents using"
        corpusAdmin -> coworkAgent "Curates the committed company knowledge corpus for"

        coworkAgent -> googleIdentity "Authenticates users and refreshes mailbox grants against" "HTTPS / OAuth 2.0"
        coworkAgent -> microsoftIdentity "Links Outlook mailboxes against" "HTTPS / OAuth 2.0 + PKCE"
        coworkAgent -> gmail "Reads unread mail and threads from" "HTTPS / REST"
        coworkAgent -> microsoftGraph "Reads unread mail and threads from" "HTTPS / REST"
        coworkAgent -> googleCalendar "Creates calendar events through the per-user-granted chat tool in" "HTTPS / REST"
        coworkAgent -> llmProviders "Classifies intent, generates plans and streams chat replies with" "HTTPS / REST"
        coworkAgent -> retrievalProviders "Embeds and reranks retrieval candidates with" "HTTPS / REST"
        coworkAgent -> mistralOcr "Extracts text from image-only document pages with" "HTTPS / REST"
        coworkAgent -> langfuse "Emits metadata-only spans and generation traces to" "HTTPS"
        coworkAgent -> threatIntel "Screens attachments and links against" "HTTPS / TCP"

        # ------------------------------------------------------------------
        # Level 2 relationships - containers
        # ------------------------------------------------------------------

        knowledgeWorker -> spa "Runs mail scans, chats, and manages project documents using" "HTTPS"
        corpusAdmin -> ingestionCli "Ingests approved company documents using" "CLI"

        spa -> api "Calls REST endpoints and consumes the chat stream" "HTTPS / REST / SSE"

        api -> controlPlaneDb "Reads and writes identity, connections, runs, turns, episodes and chunk rows" "asyncpg / sqlite3"
        api -> vectorIndex "Loads and queries the company and per-project indexes" "In-process file I/O"
        api -> knowledgeCorpus "Chunks and indexes the committed corpus at bootstrap" "File I/O"
        api -> documentStore "Registers, stores and serves uploaded project documents" "Storage SDK / File I/O"
        api -> reportStore "Writes and serves generated report artifacts" "File I/O"
        api -> gmail "Reads unread mail and threads from" "HTTPS / REST"
        api -> microsoftGraph "Reads unread mail and threads from" "HTTPS / REST"
        api -> googleIdentity "Runs the authorization code flow and refreshes tokens against" "HTTPS / OAuth 2.0"
        api -> microsoftIdentity "Runs the authorization code + PKCE flow against" "HTTPS / OAuth 2.0"
        api -> googleCalendar "Creates calendar events through the per-user-granted chat tool in" "HTTPS / REST"
        api -> llmProviders "Classifies, plans and streams replies with" "HTTPS / REST"
        api -> retrievalProviders "Embeds queries and reranks candidates with" "HTTPS / REST"
        api -> langfuse "Emits spans and generation traces to" "HTTPS"
        api -> threatIntel "Screens attachments and links against" "HTTPS / TCP"

        worker -> controlPlaneDb "Claims queued jobs from and writes results back to" "asyncpg / sqlite3"
        worker -> documentStore "Reads uploaded documents for extraction from" "Storage SDK / File I/O"
        worker -> vectorIndex "Writes page-bounded project chunk vectors to" "In-process file I/O"
        worker -> gmail "Executes queued mailbox digests against" "HTTPS / REST"
        worker -> microsoftGraph "Executes queued mailbox digests against" "HTTPS / REST"
        worker -> llmProviders "Classifies and generates action plans with" "HTTPS / REST"
        worker -> retrievalProviders "Embeds project document chunks with" "HTTPS / REST"
        worker -> mistralOcr "Extracts text from image-only pages with" "HTTPS / REST"
        worker -> langfuse "Emits spans and generation traces to" "HTTPS"

        ingestionCli -> knowledgeCorpus "Writes sanitized Markdown and a SHA-256 manifest to" "File I/O"
        ingestionCli -> mistralOcr "Extracts text from image-only pages with, in advanced mode" "HTTPS / REST"

        # ------------------------------------------------------------------
        # Level 3 relationships - Control Plane API components
        # ------------------------------------------------------------------

        spa -> mailApi "Creates digest runs and polls run results" "HTTPS / REST"
        spa -> chatApi "Streams chat turns and submits aggregate mail-scan summaries" "HTTPS / SSE"
        spa -> projectApi "Uploads and polls project documents" "HTTPS / REST"
        spa -> knowledgeApi "Reads knowledge readiness and edits raw documents" "HTTPS / REST"
        spa -> reportApi "Lists, downloads and exports report artifacts" "HTTPS / REST"

        compositionRoot -> settings "Reads validated runtime configuration from"
        mailApi -> identity "Resolves the caller and the owning connection through"
        chatApi -> identity "Resolves the caller and session ownership through"
        projectApi -> identity "Resolves workspace, user and project scope through"

        mailApi -> emailWorkflow "Executes single-turn digest runs through"
        emailWorkflow -> mailboxAdapter "Reads unread mail and threads through"
        mailboxAdapter -> gmailAdapter "Dispatches Gmail-backed connections to"
        mailboxAdapter -> outlookAdapter "Dispatches Outlook-backed connections to"
        gmailAdapter -> gmail "Reads messages and threads from" "HTTPS / REST"
        outlookAdapter -> microsoftGraph "Reads messages and threads from" "HTTPS / REST"
        emailWorkflow -> routeResolver "Resolves the per-email route with"
        emailWorkflow -> securityScanner "Screens attachment presence and links with"
        securityScanner -> threatIntel "Looks up hashes, URLs and signatures in" "HTTPS / TCP"
        emailWorkflow -> hybridRetriever "Retrieves company evidence on the RETRIEVE_RAG route through"
        emailWorkflow -> llmFactory "Classifies and generates action plans through"
        emailWorkflow -> planValidator "Enforces the body-free output contract with"
        emailWorkflow -> repositories "Persists runs, tasks and plan outcomes through"

        chatApi -> chatController "Delegates the turn to"
        chatApi -> mailScanRecon "Reconciles submitted mail-scan summaries through"
        mailScanRecon -> repositories "Merges durable turn history through"
        chatController -> intentClassifier "Resolves the turn route with"
        chatController -> memoryGateway "Reads and writes the four typed memory scopes through"
        chatController -> hybridRetriever "Retrieves company evidence through, when the flag is on"
        chatController -> projectDocRetriever "Retrieves project document evidence through"
        chatController -> toolRegistry "Runs at most one server-chosen tool per turn through"
        chatController -> llmFactory "Streams reasoning and reply tokens through"
        chatController -> episodeSettlement "Settles chat-native TaskEpisode transitions through"
        chatController -> reportPdfRenderer "Generates report artifacts through"
        toolRegistry -> calendarAdapter "Executes the single registered tool through"
        calendarAdapter -> googleCalendar "Creates events in" "HTTPS / REST"
        memoryGateway -> repositories "Reads and writes declarative and episodic memory through"
        episodeSettlement -> repositories "Persists episode state transitions through"

        queryTransform -> llmFactory "Generates HyDE hypothetical documents through"
        hybridRetriever -> queryTransform "Expands and diversifies the query with"
        hybridRetriever -> embeddingAdapter "Embeds the query with"
        hybridRetriever -> reranker "Reranks fused candidates with"
        hybridRetriever -> turbovecStore "Searches dense vectors through"
        projectDocRetriever -> turbovecStore "Searches the per-project index through"
        projectDocRetriever -> repositories "Reads ACL-filtered chunk rows through"
        embeddingAdapter -> retrievalProviders "Requests embeddings from" "HTTPS / REST"
        reranker -> retrievalProviders "Requests cross-encoder scores from" "HTTPS / REST"
        turbovecStore -> vectorIndex "Loads and persists" "In-process file I/O"
        chunker -> knowledgeCorpus "Chunks the committed corpus at bootstrap from" "File I/O"
        chunker -> turbovecStore "Feeds chunks and coordinates to"

        projectApi -> documentStore "Registers and reads uploaded documents in" "Storage SDK / File I/O"
        projectApi -> repositories "Tracks document status and chunk rows through"
        knowledgeApi -> knowledgeCorpus "Reports readiness over" "File I/O"
        reportApi -> reportStore "Lists and serves artifacts from" "File I/O"
        reportApi -> reportPdfRenderer "Exports PDF through"
        reportPdfRenderer -> reportStore "Writes rendered PDFs to" "File I/O"
        evaluationApi -> repositories "Queues and reports evaluation jobs through"

        identity -> googleIdentity "Exchanges authorization codes and refresh tokens with" "HTTPS / OAuth 2.0"
        identity -> microsoftIdentity "Exchanges authorization codes with, using PKCE" "HTTPS / OAuth 2.0"
        identity -> repositories "Stores encrypted tokens and opaque sessions through"
        llmFactory -> llmProviders "Calls chat completions on, with configured fallback ordering" "HTTPS / REST"
        repositories -> controlPlaneDb "Reads from and writes to" "asyncpg / sqlite3"
        observability -> langfuse "Emits spans and generations to" "HTTPS"
        compositionRoot -> observability "Installs tracing through"

        # ------------------------------------------------------------------
        # Level 3 relationships - Background Worker components
        # ------------------------------------------------------------------

        digestPoller -> controlPlaneDb "Claims queued digest runs from" "asyncpg / sqlite3"
        digestPoller -> gmail "Reads unread mail from" "HTTPS / REST"
        digestPoller -> microsoftGraph "Reads unread mail from" "HTTPS / REST"
        digestPoller -> llmProviders "Classifies and generates action plans with" "HTTPS / REST"
        documentWorker -> controlPlaneDb "Claims queued documents from and writes chunk rows to" "asyncpg / sqlite3"
        documentWorker -> documentStore "Reads uploaded bytes from" "Storage SDK / File I/O"
        documentWorker -> mistralOcr "Extracts image-only pages with" "HTTPS / REST"
        documentWorker -> retrievalProviders "Embeds page-bounded chunks with" "HTTPS / REST"
        documentWorker -> vectorIndex "Writes per-project vectors to" "In-process file I/O"
        recoverySweeper -> controlPlaneDb "Re-queues in-flight runs and documents in" "asyncpg / sqlite3"
        retentionSweeper -> controlPlaneDb "Purges expired rows from" "asyncpg / sqlite3"
        retentionSweeper -> documentStore "Purges expired objects from" "Storage SDK / File I/O"
        retentionSweeper -> vectorIndex "Purges expired vector ids from" "In-process file I/O"

        # ------------------------------------------------------------------
        # Level 3 relationships - Knowledge Ingestion CLI components
        # ------------------------------------------------------------------

        corpusAdmin -> ingestionService "Runs mail-todo-ingest-knowledge against" "CLI"
        ingestionService -> manifest "Gates unchanged sources on content hash through"
        ingestionService -> extractors "Converts source documents through"
        extractors -> mistralOcr "Extracts image-only pages with, in advanced mode" "HTTPS / REST"
        ingestionService -> sanitizer "Sanitizes and stamps frontmatter through"
        sanitizer -> knowledgeCorpus "Atomically writes Markdown to" "File I/O"
        manifest -> knowledgeCorpus "Records per-source hashes alongside" "File I/O"

        # ------------------------------------------------------------------
        # Deployment
        # ------------------------------------------------------------------

        deploymentEnvironment "Local" {
            deploymentNode "Developer Workstation" "Windows or Linux development machine" "OS" {
                deploymentNode "Browser" "" "Chromium / Firefox" {
                    containerInstance spa
                }
                deploymentNode "Python Virtual Environment" "Managed by uv" "CPython 3" {
                    containerInstance api
                    containerInstance worker
                    containerInstance ingestionCli
                }
                deploymentNode "Repository Working Tree" "" "Local filesystem" {
                    containerInstance knowledgeCorpus
                    containerInstance reportStore
                    containerInstance documentStore
                    containerInstance vectorIndex
                }
                deploymentNode "Docker Engine" "POSTGRES_MODE=local" "Docker Compose" {
                    deploymentNode "cowork-pg" "" "postgres:16-alpine" {
                        containerInstance controlPlaneDb
                    }
                    infrastructureNode "cowork-clamav" "Attachment signature scanning" "clamav/clamav"
                }
            }
        }

        deploymentEnvironment "Cloud" {
            deploymentNode "End User Device" "" "OS" {
                deploymentNode "Browser" "" "Chromium / Firefox / Safari" {
                    containerInstance spa
                }
            }
            deploymentNode "Application Host" "Runs the API and worker processes" "Linux / CPython 3" {
                containerInstance api
                containerInstance worker
                deploymentNode "Application Filesystem" "" "Local disk" {
                    containerInstance knowledgeCorpus
                    containerInstance reportStore
                    containerInstance vectorIndex
                }
            }
            deploymentNode "Supabase" "Managed platform. POSTGRES_MODE=cloud" "Supabase" {
                deploymentNode "Supabase Postgres" "" "PostgreSQL 16" {
                    containerInstance controlPlaneDb
                }
                deploymentNode "Supabase Storage" "" "Object storage" {
                    containerInstance documentStore
                }
            }
        }
    }

    views {

        systemContext coworkAgent "c1-system-context" "Level 1. Cowork Agent, the people who use it, and the external systems it depends on." {
            include *
            autoLayout lr
        }

        container coworkAgent "c2-containers" "Level 2. The deployable and storage units inside Cowork Agent." {
            include *
            autoLayout lr
        }

        component api "c3-api-email-action-plan" "Level 3. The single-turn, memory-free email pipeline inside the Control Plane API." {
            include mailApi emailWorkflow routeResolver planValidator mailboxAdapter gmailAdapter outlookAdapter securityScanner
            include hybridRetriever llmFactory repositories identity
            include spa gmail microsoftGraph threatIntel controlPlaneDb
            autoLayout lr
        }

        component api "c3-api-ai-chat" "Level 3. The multi-turn chat controller, typed memory, and the flag-off tool axis." {
            include chatApi chatController intentClassifier memoryGateway mailScanRecon episodeSettlement toolRegistry calendarAdapter
            include hybridRetriever projectDocRetriever llmFactory repositories reportPdfRenderer identity
            include spa googleCalendar llmProviders controlPlaneDb reportStore
            autoLayout lr
        }

        component api "c3-api-retrieval" "Level 3. Hybrid retrieval over the company corpus and per-project document indexes." {
            include hybridRetriever queryTransform reranker embeddingAdapter chunker turbovecStore projectDocRetriever
            include llmFactory repositories emailWorkflow chatController
            include retrievalProviders vectorIndex knowledgeCorpus controlPlaneDb
            autoLayout lr
        }

        component api "c3-api-platform" "Level 3. Composition, configuration, identity, persistence and observability." {
            include compositionRoot settings identity llmFactory repositories observability evaluationApi
            include projectApi knowledgeApi reportApi reportPdfRenderer
            include googleIdentity microsoftIdentity llmProviders langfuse controlPlaneDb documentStore reportStore knowledgeCorpus
            autoLayout lr
        }

        component worker "c3-worker" "Level 3. Out-of-process pollers, recovery and retention." {
            include *
            autoLayout lr
        }

        component ingestionCli "c3-ingestion-cli" "Level 3. Offline conversion of administrator documents into the committed corpus." {
            include *
            autoLayout lr
        }

        dynamic coworkAgent "flow-mail-scan" "A mail scan: the client drives the run and submits one body-free summary card into chat." {
            knowledgeWorker -> spa "Starts a mail scan"
            spa -> api "Creates a digest run"
            api -> controlPlaneDb "Queues the run"
            worker -> controlPlaneDb "Claims the queued run"
            worker -> gmail "Reads unread mail and threads"
            worker -> llmProviders "Classifies and generates the action plan"
            worker -> controlPlaneDb "Writes body-free tasks and the run result"
            spa -> api "Polls the run until it settles"
            spa -> api "Submits one aggregate MailScanSummary to the chat session"
            api -> controlPlaneDb "Reconciles the summary into durable turn history"
            autoLayout tb
        }

        dynamic coworkAgent "flow-chat-turn" "A grounded chat turn: classify, retrieve, assemble, generate, persist." {
            knowledgeWorker -> spa "Sends a message"
            spa -> api "Opens the SSE turn"
            api -> llmProviders "Classifies the turn intent"
            api -> controlPlaneDb "Reads the session buffer and eligible memory"
            api -> vectorIndex "Retrieves evidence from the company or project index"
            api -> retrievalProviders "Embeds the query and reranks candidates"
            api -> llmProviders "Streams reasoning and reply tokens"
            api -> controlPlaneDb "Persists the turn, execution trace and any TaskEpisode"
            api -> langfuse "Emits metadata-only spans"
            autoLayout tb
        }

        deployment coworkAgent "Local" "deployment-local" "Local development: Docker Postgres or SQLite, everything on one workstation." {
            include *
            autoLayout tb
        }

        deployment coworkAgent "Cloud" "deployment-cloud" "Hosted deployment: Supabase Postgres and Storage." {
            include *
            autoLayout tb
        }

        styles {
            element "Person" {
                shape Person
                background #08427b
                color #ffffff
            }
            element "Software System" {
                background #1168bd
                color #ffffff
            }
            element "Container" {
                background #438dd5
                color #ffffff
            }
            element "Component" {
                background #85bbf0
                color #000000
            }
            element "Database" {
                shape Cylinder
            }
            element "FileStore" {
                shape Folder
            }
            element "WebBrowser" {
                shape WebBrowser
            }
            element "External" {
                background #8c8c8c
                color #ffffff
            }
            element "Infrastructure Node" {
                background #ffffff
                color #000000
            }
        }
    }
}
