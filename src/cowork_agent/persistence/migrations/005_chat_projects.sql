CREATE TABLE IF NOT EXISTS user_chat_projects (
    project_id text PRIMARY KEY,
    tenant_id text NOT NULL,
    user_id text NOT NULL,
    name text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 200),
    is_default boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    deleted_at timestamptz,
    UNIQUE (tenant_id, user_id, project_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_chat_projects_default_owner
    ON user_chat_projects (tenant_id, user_id)
    WHERE is_default = true AND deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS ix_chat_projects_owner
    ON user_chat_projects (tenant_id, user_id, created_at)
    WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS user_chat_sessions (
    session_id text PRIMARY KEY,
    tenant_id text NOT NULL,
    user_id text NOT NULL,
    project_id text NOT NULL,
    created_at timestamptz NOT NULL,
    deleted_at timestamptz,
    FOREIGN KEY (tenant_id, user_id, project_id)
        REFERENCES user_chat_projects (tenant_id, user_id, project_id)
);

CREATE INDEX IF NOT EXISTS ix_chat_sessions_owner_project
    ON user_chat_sessions (tenant_id, user_id, project_id, created_at)
    WHERE deleted_at IS NULL;
