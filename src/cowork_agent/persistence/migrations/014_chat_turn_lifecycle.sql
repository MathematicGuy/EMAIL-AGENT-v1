-- Persist a user submission before generation and update that same turn in place.
ALTER TABLE chat_turns
    ALTER COLUMN assistant_message DROP NOT NULL,
    ADD COLUMN status text NOT NULL DEFAULT 'completed' CHECK (status IN (
        'generating', 'completed', 'failed', 'cancelled',
        'usage_limited', 'rate_limited'
    )),
    ADD COLUMN idempotency_key text,
    ADD COLUMN error_code text;

UPDATE chat_turns
SET idempotency_key = turn_id
WHERE idempotency_key IS NULL;

ALTER TABLE chat_turns
    ALTER COLUMN idempotency_key SET NOT NULL,
    ADD CONSTRAINT chat_turns_session_idempotency_key_unique
        UNIQUE (session_id, idempotency_key);
