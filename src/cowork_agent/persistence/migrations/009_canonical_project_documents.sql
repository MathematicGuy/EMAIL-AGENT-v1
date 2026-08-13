-- Irreversible cutover to the Supabase/Postgres project-document plane.
ALTER TABLE projects ADD COLUMN IF NOT EXISTS deleted_at timestamptz;

DROP INDEX IF EXISTS projects_one_default_per_owner_idx;
CREATE UNIQUE INDEX projects_one_default_per_owner_idx
    ON projects (workspace_id, owner_user_id)
    WHERE is_default AND deleted_at IS NULL;
CREATE INDEX projects_active_owner_idx
    ON projects (workspace_id, owner_user_id, created_at)
    WHERE deleted_at IS NULL;

ALTER TABLE project_documents
    DROP CONSTRAINT IF EXISTS project_documents_project_id_content_sha256_key;
CREATE UNIQUE INDEX project_documents_active_content_idx
    ON project_documents (project_id, content_sha256)
    WHERE status <> 'deleted';

CREATE TABLE document_cleanup_jobs (
    id uuid PRIMARY KEY,
    document_id uuid NOT NULL UNIQUE REFERENCES project_documents(id) ON DELETE CASCADE,
    status text NOT NULL CHECK (status IN ('queued', 'running', 'completed', 'failed')),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    available_at timestamptz NOT NULL DEFAULT now(),
    claimed_at timestamptz,
    completed_at timestamptz,
    error_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

DROP TABLE IF EXISTS user_chat_sessions;
DROP TABLE IF EXISTS user_project_document_chunks;
DROP TABLE IF EXISTS user_project_documents;
DROP TABLE IF EXISTS user_chat_projects;
