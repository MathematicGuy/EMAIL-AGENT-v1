-- Durable, user-visible AI Chat history. Email content is never stored here.
ALTER TABLE chat_sessions
    ADD COLUMN title text CHECK (title IS NULL OR (
        btrim(title) <> '' AND char_length(title) <= 120
    ));

CREATE TABLE chat_turns (
    session_id text NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    turn_id text NOT NULL,
    user_message text NOT NULL,
    assistant_message text NOT NULL,
    citation_coordinates jsonb NOT NULL DEFAULT '[]'::jsonb,
    rag_evidence jsonb NOT NULL DEFAULT '[]'::jsonb,
    retrieval_status text CHECK (retrieval_status IN (
        'success', 'no_results', 'timeout', 'unavailable'
    )),
    created_at timestamptz NOT NULL,
    PRIMARY KEY (session_id, turn_id)
);

CREATE INDEX chat_turns_session_created_idx
    ON chat_turns (session_id, created_at, turn_id);
