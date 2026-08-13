ALTER TABLE project_documents
    ADD COLUMN IF NOT EXISTS cleanup_completed_at timestamptz;

CREATE INDEX IF NOT EXISTS ix_project_documents_cleanup_pending
    ON project_documents (deleted_at, document_id)
    WHERE status = 'deleted' AND cleanup_completed_at IS NULL;
