CREATE TABLE IF NOT EXISTS project_documents (
    document_id text PRIMARY KEY,
    project_id text NOT NULL,
    tenant_id text NOT NULL,
    user_id text NOT NULL,
    title text NOT NULL CHECK (char_length(title) BETWEEN 1 AND 300),
    media_type text NOT NULL CHECK (media_type IN (
        'application/pdf',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    )),
    size_bytes bigint NOT NULL CHECK (size_bytes > 0),
    sha256 text NOT NULL CHECK (char_length(sha256) = 64),
    status text NOT NULL CHECK (status IN (
        'received', 'extracting', 'indexing', 'ready', 'failed', 'deleted'
    )),
    reason_code text,
    page_count integer NOT NULL DEFAULT 0 CHECK (page_count >= 0),
    chunk_count integer NOT NULL DEFAULT 0 CHECK (chunk_count >= 0),
    ocr_page_count integer NOT NULL DEFAULT 0 CHECK (ocr_page_count >= 0),
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    lease_owner text,
    lease_expires_at timestamptz,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    deleted_at timestamptz,
    FOREIGN KEY (tenant_id, user_id, project_id)
        REFERENCES chat_projects (tenant_id, user_id, project_id),
    CONSTRAINT ck_project_document_failure_reason CHECK (
        (status = 'failed' AND reason_code IS NOT NULL)
        OR (status <> 'failed' AND reason_code IS NULL)
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_project_documents_owner_hash
    ON project_documents (tenant_id, user_id, project_id, sha256)
    WHERE status <> 'deleted';

CREATE INDEX IF NOT EXISTS ix_project_documents_ready_scope
    ON project_documents (tenant_id, user_id, project_id, expires_at)
    WHERE status = 'ready';

CREATE INDEX IF NOT EXISTS ix_project_documents_recovery
    ON project_documents (status, lease_expires_at, updated_at)
    WHERE status IN ('received', 'extracting', 'indexing');

CREATE TABLE IF NOT EXISTS project_document_chunks (
    chunk_id text PRIMARY KEY,
    document_id text NOT NULL REFERENCES project_documents(document_id) ON DELETE CASCADE,
    page_start integer NOT NULL CHECK (page_start >= 1),
    page_end integer NOT NULL CHECK (page_end >= page_start),
    section text,
    created_at timestamptz NOT NULL,
    UNIQUE (document_id, chunk_id)
);
