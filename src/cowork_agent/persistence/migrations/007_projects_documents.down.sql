ALTER TABLE task_episodes DROP COLUMN IF EXISTS project_id;
ALTER TABLE chat_sessions DROP CONSTRAINT IF EXISTS chat_sessions_project_owner_fk;
ALTER TABLE chat_sessions DROP COLUMN IF EXISTS project_id;
DROP TABLE IF EXISTS document_deletion_audits;
DROP TABLE IF EXISTS document_ingestion_jobs;
DROP TABLE IF EXISTS project_documents;
DROP TABLE IF EXISTS projects;
