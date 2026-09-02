-- Per-user Google Calendar grants (SPEC-per-user-google-calendar-oauth).
-- Deliberately not a row in mailbox_connections: mail routing iterates that
-- table, and a calendar row there becomes a mail-routing bug (J7). The refresh
-- token arrives already encrypted by TokenCipher.
CREATE TABLE IF NOT EXISTS calendar_connections (
    id text PRIMARY KEY,
    user_id uuid NOT NULL UNIQUE REFERENCES app_users(id) ON DELETE CASCADE,
    provider text NOT NULL CHECK (provider = 'google_calendar'),
    external_account_id text NOT NULL,
    calendar_id text NOT NULL DEFAULT 'primary',
    encrypted_refresh_token text NOT NULL,
    scopes text[] NOT NULL,
    timezone text NOT NULL,
    status text NOT NULL CHECK (status IN ('active', 'revoked')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
