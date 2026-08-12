-- Durable ownership metadata for AI Chat sessions. Short-term turns stay
-- in process and prompts/replies never enter this table.
CREATE TABLE chat_sessions (
    id text PRIMARY KEY,
    workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    user_id uuid NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    feature text NOT NULL CHECK (feature = 'ai_chat'),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX chat_sessions_owner_idx
    ON chat_sessions (workspace_id, user_id, created_at, id);
