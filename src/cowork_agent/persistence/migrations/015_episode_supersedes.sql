-- Concern D: a corrected task episode names the episode it replaces, so
-- retrieval can drop the superseded ancestor instead of handing the model two
-- contradicting approved facts. retrieval_eligible is a generated column
-- derived from validation_status and deliberately stays that way; supersession
-- is resolved at read time, not by making the ancestor ineligible.
ALTER TABLE task_episodes
    ADD COLUMN supersedes text;

CREATE INDEX IF NOT EXISTS idx_task_episodes_supersedes
    ON task_episodes (tenant_id, user_id, feature, supersedes)
    WHERE supersedes IS NOT NULL;
