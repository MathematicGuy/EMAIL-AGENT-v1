-- Safe aggregate @mail result metadata for durable chat history. No email content is stored.
ALTER TABLE chat_turns
    ADD COLUMN mail_scan jsonb CHECK (
        mail_scan IS NULL OR jsonb_typeof(mail_scan) = 'object'
    );
