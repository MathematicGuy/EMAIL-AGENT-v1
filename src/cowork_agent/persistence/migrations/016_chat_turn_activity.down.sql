ALTER TABLE chat_turns
    DROP COLUMN IF EXISTS completed_at,
    DROP COLUMN IF EXISTS activities;
