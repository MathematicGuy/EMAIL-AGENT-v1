-- PostgreSQL baseline for the mail-to-do bounded module.
--
-- Evolution record (V1-H T5.1, 2026-08-08): this baseline has never been
-- deployed, so it is edited in place instead of layered with ALTER scripts.
-- - digest_schedules / schedule_occurrences dropped: scheduling is a PRD-v1
--   non-goal and §15 criterion 19 requires no scheduler to be present.
-- - digest_runs."trigger" loses the 'scheduled' value; schedule_id removed.
-- - action_items evolved into tasks (§6.6 Task contract) plus
--   task_run_links, evolving the SQLite lineage shape from V1-M4: the
--   idempotent key tenant_id:user_id:gmail_message_id:pipeline_version is
--   the primary key, the Task contract is stored as jsonb, and every
--   producing run stays linked with its save-time freshness. Run
--   attribution lives solely in task_run_links (the lineage's per-row
--   run_id is dropped).
-- - "trigger" is quoted because TRIGGER is a reserved keyword.
-- - No foreign keys to mailbox_connections yet: the mailbox connection
--   store still lives in SQLite (V1-M1 lineage) and migrates separately;
--   the columns are kept so the FK can be added once it lands.
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

CREATE TABLE digest_runs (
    id text PRIMARY KEY,
    user_id text NOT NULL,
    mailbox_connection_id text NOT NULL,
    "trigger" text NOT NULL CHECK ("trigger" IN ('on_demand')),
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

CREATE TABLE tasks (
    task_key text PRIMARY KEY,
    task_id text NOT NULL,
    tenant_id text NOT NULL,
    user_id text NOT NULL,
    pipeline_version text NOT NULL,
    gmail_message_id text NOT NULL,
    mailbox_connection_id text NOT NULL,
    provider_thread_id text NOT NULL,
    sender_name text,
    sender_address text NOT NULL,
    email_subject text NOT NULL,
    email_received_at timestamptz NOT NULL,
    fingerprint char(64) NOT NULL,
    task_json jsonb NOT NULL,
    created_at timestamptz NOT NULL
);

CREATE TABLE task_run_links (
    task_key text NOT NULL REFERENCES tasks(task_key) ON DELETE CASCADE,
    run_id text NOT NULL REFERENCES digest_runs(id) ON DELETE CASCADE,
    freshness text NOT NULL CHECK (freshness IN ('new', 'seen')),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (task_key, run_id)
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
CREATE INDEX tasks_mailbox_fingerprint_idx
    ON tasks (mailbox_connection_id, fingerprint);
CREATE INDEX task_run_links_run_idx ON task_run_links (run_id);
CREATE INDEX outbox_events_unpublished_idx
    ON outbox_events (id) WHERE published_at IS NULL;
