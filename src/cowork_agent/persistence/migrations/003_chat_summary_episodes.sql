-- V2-M3: retry-safe, bounded system-generated chat-summary episodes.
--
-- The contract's MAX_CHAT_SUMMARY_LENGTH is 500; keep the storage cap in
-- sync. This table intentionally contains no raw email, attachment, chat
-- transcript, message, or tool-payload columns.
CREATE TABLE chat_summary_episodes (
    episode_key text PRIMARY KEY,
    episode_id text NOT NULL,
    record_id text NOT NULL,
    tenant_id text NOT NULL,
    user_id text NOT NULL,
    feature text NOT NULL CHECK (feature = 'ai_chat'),
    chat_session_id text NOT NULL,
    chat_turn_id text NOT NULL,
    summary text NOT NULL CHECK (btrim(summary) <> '' AND char_length(summary) <= 500),
    validation_status text NOT NULL CHECK (validation_status = 'system_generated'),
    retrieval_eligible boolean NOT NULL CHECK (retrieval_eligible = false),
    source_type text NOT NULL CHECK (source_type = 'system_generated_chat_summary'),
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL CHECK (updated_at >= created_at),
    expires_at timestamptz CHECK (expires_at IS NULL OR expires_at > created_at),
    pipeline_version text NOT NULL,
    model_id text,
    prompt_version text,
    confidence double precision CHECK (
        confidence IS NULL OR (confidence >= 0 AND confidence <= 1)
    ),
    UNIQUE (tenant_id, user_id, feature, chat_session_id, chat_turn_id)
);

CREATE INDEX chat_summary_episodes_expires_idx ON chat_summary_episodes (expires_at)
    WHERE expires_at IS NOT NULL;
