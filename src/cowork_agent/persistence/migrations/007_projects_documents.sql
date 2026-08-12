-- Private project-document control plane. Source bytes remain in private
-- object storage; extracted text and prompts are intentionally absent.
CREATE TABLE projects (
    id uuid PRIMARY KEY,
    workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    owner_user_id uuid NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    name text NOT NULL CHECK (btrim(name) <> '' AND char_length(name) <= 200),
    is_default boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (id, workspace_id, owner_user_id)
);
CREATE UNIQUE INDEX projects_one_default_per_owner_idx
    ON projects (workspace_id, owner_user_id) WHERE is_default;

-- Deterministic backfill requires no extension and is only used for pre-existing
-- owners. New project IDs are application-generated UUIDs.
INSERT INTO projects (id, workspace_id, owner_user_id, name, is_default)
SELECT (
    substr(md5(members.workspace_id::text || members.user_id::text || 'default-project'), 1, 8)
    || '-' || substr(md5(members.workspace_id::text || members.user_id::text || 'default-project'), 9, 4)
    || '-' || substr(md5(members.workspace_id::text || members.user_id::text || 'default-project'), 13, 4)
    || '-' || substr(md5(members.workspace_id::text || members.user_id::text || 'default-project'), 17, 4)
    || '-' || substr(md5(members.workspace_id::text || members.user_id::text || 'default-project'), 21, 12)
)::uuid, members.workspace_id, members.user_id, 'Default project', true
FROM workspace_members AS members
WHERE members.role = 'owner'
ON CONFLICT (workspace_id, owner_user_id) WHERE is_default DO NOTHING;

ALTER TABLE chat_sessions ADD COLUMN project_id uuid REFERENCES projects(id);
UPDATE chat_sessions AS sessions
SET project_id = projects.id
FROM projects
WHERE projects.workspace_id = sessions.workspace_id
  AND projects.owner_user_id = sessions.user_id
  AND projects.is_default;
ALTER TABLE chat_sessions ALTER COLUMN project_id SET NOT NULL;
ALTER TABLE chat_sessions
    ADD CONSTRAINT chat_sessions_project_owner_fk
    FOREIGN KEY (project_id, workspace_id, user_id)
    REFERENCES projects (id, workspace_id, owner_user_id) ON DELETE CASCADE;

ALTER TABLE task_episodes ADD COLUMN project_id uuid REFERENCES projects(id);

CREATE TABLE project_documents (
    id uuid PRIMARY KEY,
    workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    user_id uuid NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    filename text NOT NULL CHECK (btrim(filename) <> '' AND char_length(filename) <= 255),
    media_type text NOT NULL CHECK (media_type IN (
        'application/pdf',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    )),
    byte_size bigint NOT NULL CHECK (byte_size > 0),
    content_sha256 char(64) NOT NULL,
    storage_key text NOT NULL UNIQUE,
    status text NOT NULL CHECK (status IN (
        'received', 'extracting', 'indexing', 'ready', 'failed', 'deleting', 'deleted'
    )),
    page_count integer CHECK (page_count IS NULL OR page_count > 0),
    ocr_page_count integer CHECK (ocr_page_count IS NULL OR ocr_page_count >= 0),
    chunk_count integer CHECK (chunk_count IS NULL OR chunk_count >= 0),
    error_code text,
    expires_at timestamptz NOT NULL,
    deleted_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (project_id, content_sha256),
    FOREIGN KEY (project_id, workspace_id, user_id)
        REFERENCES projects (id, workspace_id, owner_user_id) ON DELETE CASCADE
);
CREATE INDEX project_documents_ready_scope_idx
    ON project_documents (workspace_id, user_id, project_id, expires_at)
    WHERE status = 'ready' AND deleted_at IS NULL;

CREATE TABLE document_ingestion_jobs (
    id uuid PRIMARY KEY,
    document_id uuid NOT NULL UNIQUE REFERENCES project_documents(id) ON DELETE CASCADE,
    status text NOT NULL CHECK (status IN ('queued', 'extracting', 'indexing', 'completed', 'failed')),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    available_at timestamptz NOT NULL DEFAULT now(),
    claimed_at timestamptz,
    completed_at timestamptz,
    error_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE document_deletion_audits (
    id uuid PRIMARY KEY,
    document_id uuid NOT NULL,
    postgres_outcome text NOT NULL CHECK (postgres_outcome IN ('hidden', 'failed')),
    qdrant_outcome text NOT NULL CHECK (qdrant_outcome IN ('pending', 'deleted', 'failed')),
    storage_outcome text NOT NULL CHECK (storage_outcome IN ('pending', 'deleted', 'failed')),
    created_at timestamptz NOT NULL DEFAULT now()
);
