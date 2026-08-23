DROP INDEX IF EXISTS idx_task_episodes_supersedes;

ALTER TABLE task_episodes
    DROP COLUMN IF EXISTS supersedes;
