-- Rollback cannot represent unfinished turns in the previous completed-only schema.
DELETE FROM chat_turns WHERE assistant_message IS NULL;

ALTER TABLE chat_turns
    ALTER COLUMN assistant_message SET NOT NULL,
    DROP CONSTRAINT IF EXISTS chat_turns_session_idempotency_key_unique,
    DROP COLUMN IF EXISTS status,
    DROP COLUMN IF EXISTS idempotency_key,
    DROP COLUMN IF EXISTS error_code;
