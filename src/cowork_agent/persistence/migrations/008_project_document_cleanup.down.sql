DROP INDEX IF EXISTS ix_project_documents_cleanup_pending;
ALTER TABLE project_documents DROP COLUMN IF EXISTS cleanup_completed_at;
