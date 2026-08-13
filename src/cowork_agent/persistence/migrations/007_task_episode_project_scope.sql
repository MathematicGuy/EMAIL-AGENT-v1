ALTER TABLE task_episodes ADD COLUMN IF NOT EXISTS project_id text;
ALTER TABLE task_episodes ADD COLUMN IF NOT EXISTS citation_scope text;
ALTER TABLE task_episodes ADD COLUMN IF NOT EXISTS citation_page_start integer;
ALTER TABLE task_episodes ADD COLUMN IF NOT EXISTS citation_page_end integer;

CREATE INDEX IF NOT EXISTS ix_task_episodes_project_provenance
    ON task_episodes (tenant_id, user_id, project_id)
    WHERE project_id IS NOT NULL;
