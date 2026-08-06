-- PostgreSQL baseline for the mail-to-do bounded module.
CREATE TABLE mailbox_connections (
    id text PRIMARY KEY,
    user_id text NOT NULL,
    provider text NOT NULL CHECK (provider = 'gmail'),
    external_account_id text NOT NULL,
    email_address text NOT NULL,
    encrypted_refresh_token text NOT NULL,
    scopes text[] NOT NULL,
    status text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, provider, external_account_id)
);

CREATE TABLE digest_schedules (
    id text PRIMARY KEY,
    user_id text NOT NULL,
    mailbox_connection_id text NOT NULL REFERENCES mailbox_connections(id) ON DELETE CASCADE,
    name text NOT NULL,
    cron_expression text NOT NULL,
    timezone text NOT NULL,
    enabled boolean NOT NULL DEFAULT true,
    next_run_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE digest_runs (
    id text PRIMARY KEY,
    user_id text NOT NULL,
    mailbox_connection_id text NOT NULL REFERENCES mailbox_connections(id) ON DELETE CASCADE,
    schedule_id text REFERENCES digest_schedules(id) ON DELETE SET NULL,
    trigger text NOT NULL CHECK (trigger IN ('on_demand', 'scheduled')),
    status text NOT NULL CHECK (status IN ('queued','running','succeeded','partial','failed')),
    query text NOT NULL,
    idempotency_key text NOT NULL,
    max_emails integer NOT NULL CHECK (max_emails BETWEEN 1 AND 500),
    emails_matched integer NOT NULL DEFAULT 0,
    emails_processed integer NOT NULL DEFAULT 0,
    emails_actionable integer NOT NULL DEFAULT 0,
    action_items_count integer NOT NULL DEFAULT 0,
    ignored_emails_count integer NOT NULL DEFAULT 0,
    attachments_found integer NOT NULL DEFAULT 0,
    attachments_extracted integer NOT NULL DEFAULT 0,
    attachment_warnings_count integer NOT NULL DEFAULT 0,
    truncated boolean NOT NULL DEFAULT false,
    next_cursor text,
    error_code text,
    error_message_safe text,
    started_at timestamptz,
    completed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, idempotency_key)
);

CREATE TABLE schedule_occurrences (
    schedule_id text NOT NULL REFERENCES digest_schedules(id) ON DELETE CASCADE,
    scheduled_for timestamptz NOT NULL,
    run_id text REFERENCES digest_runs(id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (schedule_id, scheduled_for)
);

CREATE TABLE action_items (
    id text PRIMARY KEY,
    run_id text NOT NULL REFERENCES digest_runs(id) ON DELETE CASCADE,
    mailbox_connection_id text NOT NULL REFERENCES mailbox_connections(id) ON DELETE CASCADE,
    provider_message_id text NOT NULL,
    provider_thread_id text NOT NULL,
    fingerprint char(64) NOT NULL,
    freshness text NOT NULL CHECK (freshness IN ('new','seen','changed')),
    title text NOT NULL,
    summary text NOT NULL,
    sender_name text,
    sender_address text NOT NULL,
    email_subject text NOT NULL,
    email_received_at timestamptz NOT NULL,
    email_deep_link text,
    deadline_at timestamptz,
    deadline_source text NOT NULL CHECK (deadline_source IN ('explicit','inferred','none')),
    deadline_text text,
    priority text NOT NULL CHECK (priority IN ('urgent','high','medium','low')),
    priority_reason text NOT NULL,
    action_plan jsonb NOT NULL,
    evidence jsonb NOT NULL,
    confidence text NOT NULL CHECK (confidence IN ('high','medium','low')),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (run_id, fingerprint)
);

CREATE TABLE attachment_extractions (
    id bigserial PRIMARY KEY,
    run_id text NOT NULL REFERENCES digest_runs(id) ON DELETE CASCADE,
    attachment_id text NOT NULL,
    filename text NOT NULL,
    detected_mime_type text,
    sha256 char(64),
    status text NOT NULL,
    warning_code text,
    units_count integer NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE outbox_events (
    id bigserial PRIMARY KEY,
    aggregate_id text NOT NULL,
    event_type text NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    published_at timestamptz,
    UNIQUE (aggregate_id, event_type)
);

CREATE INDEX digest_runs_user_created_idx ON digest_runs (user_id, created_at DESC);
CREATE INDEX digest_runs_status_idx ON digest_runs (status, created_at);
CREATE INDEX action_items_run_priority_idx ON action_items (run_id, priority, deadline_at);
CREATE INDEX action_items_mailbox_fingerprint_idx
    ON action_items (mailbox_connection_id, fingerprint, created_at DESC);
CREATE INDEX digest_schedules_next_run_idx ON digest_schedules (enabled, next_run_at);

