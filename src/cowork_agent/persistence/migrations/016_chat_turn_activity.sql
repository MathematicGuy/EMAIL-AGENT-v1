ALTER TABLE chat_turns
    ADD COLUMN activities jsonb NOT NULL DEFAULT '[]'::jsonb
        CHECK (jsonb_typeof(activities) = 'array'),
    ADD COLUMN completed_at timestamptz;
