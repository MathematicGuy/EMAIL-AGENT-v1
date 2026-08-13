DROP INDEX IF EXISTS ix_task_episodes_project_provenance;
ALTER TABLE task_episodes DROP COLUMN IF EXISTS citation_page_end;
ALTER TABLE task_episodes DROP COLUMN IF EXISTS citation_page_start;
ALTER TABLE task_episodes DROP COLUMN IF EXISTS citation_scope;
ALTER TABLE task_episodes DROP COLUMN IF EXISTS project_id;
