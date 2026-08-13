-- Data discarded by the canonical cutover cannot be restored automatically.
DROP TABLE IF EXISTS document_cleanup_jobs;
DROP INDEX IF EXISTS project_documents_active_content_idx;
ALTER TABLE project_documents
    ADD CONSTRAINT project_documents_project_id_content_sha256_key
    UNIQUE (project_id, content_sha256);
DROP INDEX IF EXISTS projects_active_owner_idx;
DROP INDEX IF EXISTS projects_one_default_per_owner_idx;
CREATE UNIQUE INDEX projects_one_default_per_owner_idx
    ON projects (workspace_id, owner_user_id) WHERE is_default;
ALTER TABLE projects DROP COLUMN IF EXISTS deleted_at;
