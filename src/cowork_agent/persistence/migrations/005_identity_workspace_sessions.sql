-- Supabase/PostgreSQL identity foundation. Gmail remains the external identity
-- provider; these IDs are application-owned and authorization never uses an
-- email address directly. Session plaintext is intentionally absent: only the
-- SHA-256 token hash can enter app_sessions.
CREATE TABLE app_users (
    id uuid PRIMARY KEY,
    primary_email text NOT NULL UNIQUE CHECK (primary_email = lower(primary_email)),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE workspaces (
    id uuid PRIMARY KEY,
    name text NOT NULL CHECK (btrim(name) <> '' AND char_length(name) <= 200),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE workspace_members (
    workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    user_id uuid NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    role text NOT NULL CHECK (role IN ('owner', 'member')),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, user_id)
);

CREATE TABLE app_sessions (
    token_hash char(64) PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (expires_at > created_at)
);

ALTER TABLE mailbox_connections ADD COLUMN workspace_id uuid REFERENCES workspaces(id);

CREATE INDEX workspace_members_user_idx ON workspace_members (user_id, workspace_id);
CREATE INDEX app_sessions_live_token_idx ON app_sessions (token_hash, expires_at)
    WHERE revoked_at IS NULL;
CREATE INDEX mailbox_connections_workspace_user_idx
    ON mailbox_connections (workspace_id, user_id, created_at);
