-- V2-M3.4a: durable, chat-native task episodes.  The compact fields below are
-- deliberately derived-task metadata only; raw email, attachments, transcripts,
-- tool payloads, and semantic-RAG content do not have a storage location here.
-- Public contract caps: title 200; request paraphrase 1000; collection count
-- 20; action/missing item 500; citation count 20; citation id 256, title and
-- section 300, URL 2048.  Regex groups keep PostgreSQL's repetition limit.
CREATE TABLE task_episodes (
    tenant_id text NOT NULL,
    user_id text NOT NULL,
    feature text NOT NULL CHECK (feature = 'ai_chat'),
    chat_session_id text NOT NULL,
    record_id text NOT NULL,
    episode_id text NOT NULL,
    chat_turn_id text NOT NULL,
    creation_reason text NOT NULL CHECK (creation_reason = 'explicit_user_task_request'),
    task_title text NOT NULL CHECK (btrim(task_title) <> '' AND char_length(task_title) <= 200),
    minimal_request_paraphrase text NOT NULL CHECK (
        btrim(minimal_request_paraphrase) <> '' AND char_length(minimal_request_paraphrase) <= 1000
    ),
    action_plan jsonb NOT NULL CHECK (
        jsonb_typeof(action_plan) = 'array'
        AND jsonb_array_length(action_plan) <= 20
        AND NOT jsonb_path_exists(action_plan, '$[*] ? (@.type() != "string" || !(@ like_regex "^(?:.{1,250}){1,2}$" flag "s"))')
    ),
    rag_citations jsonb NOT NULL CHECK (
        jsonb_typeof(rag_citations) = 'array'
        AND jsonb_array_length(rag_citations) <= 20
        AND NOT jsonb_path_exists(rag_citations, '$[*] ? (@.type() != "object")')
        AND NOT jsonb_path_exists(rag_citations, '$[*].keyvalue() ? (@.key != "document_id" && @.key != "document_title" && @.key != "section" && @.key != "source_url")')
        AND NOT jsonb_path_exists(rag_citations, '$[*] ? (!exists(@.document_id) || @.document_id.type() != "string" || !(@.document_id like_regex "^(?:.{1,128}){1,2}$" flag "s"))')
        AND NOT jsonb_path_exists(rag_citations, '$[*] ? (!exists(@.document_title) || @.document_title.type() != "string" || !(@.document_title like_regex "^(?:.{1,150}){1,2}$" flag "s"))')
        AND NOT jsonb_path_exists(rag_citations, '$[*] ? (!exists(@.source_url) || @.source_url.type() != "string" || !(@.source_url like_regex "^(?:.{1,128}){1,16}$" flag "s"))')
        AND NOT jsonb_path_exists(rag_citations, '$[*] ? (exists(@.section) && @.section.type() != "null" && (@.section.type() != "string" || !(@.section like_regex "^(?:.{1,150}){1,2}$" flag "s")))')
    ),
    missing_information jsonb NOT NULL CHECK (
        jsonb_typeof(missing_information) = 'array'
        AND jsonb_array_length(missing_information) <= 20
        AND NOT jsonb_path_exists(missing_information, '$[*] ? (@.type() != "string" || !(@ like_regex "^(?:.{1,250}){1,2}$" flag "s"))')
    ),
    validation_status text NOT NULL CHECK (
        validation_status IN ('system_generated', 'user_approved', 'completed', 'rejected')
    ),
    retrieval_eligible boolean GENERATED ALWAYS AS (
        validation_status IN ('user_approved', 'completed')
    ) STORED,
    source_type text NOT NULL CHECK (source_type = 'system_generated_chat_task'),
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL CHECK (updated_at >= created_at),
    expires_at timestamptz CHECK (expires_at IS NULL OR expires_at > created_at),
    pipeline_version text NOT NULL,
    model_id text,
    prompt_version text,
    confidence double precision CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    search_vector tsvector GENERATED ALWAYS AS (
        setweight(to_tsvector('simple', task_title), 'A')
        || setweight(to_tsvector('simple', minimal_request_paraphrase), 'B')
        || setweight(to_tsvector('simple', action_plan::text), 'C')
        || setweight(to_tsvector('simple', missing_information::text), 'C')
    ) STORED,
    PRIMARY KEY (tenant_id, user_id, feature, chat_session_id, record_id),
    UNIQUE (tenant_id, user_id, feature, chat_session_id, chat_turn_id),
    UNIQUE (tenant_id, user_id, feature, chat_session_id, episode_id)
);

CREATE INDEX task_episodes_retrieval_idx ON task_episodes
    (tenant_id, user_id, feature, updated_at DESC, record_id DESC)
    WHERE validation_status IN ('user_approved', 'completed');
CREATE INDEX task_episodes_search_idx ON task_episodes USING GIN (search_vector);
CREATE INDEX task_episodes_expires_idx ON task_episodes (expires_at) WHERE expires_at IS NOT NULL;
