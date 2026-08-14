-- ADR-008: the project-document plane leaves Qdrant. Extracted chunk text,
-- page coordinates, and the lexical retrieval leg become durable in Postgres;
-- only the quantized vectors stay outside, in a per-project Turbovec .tvim.
-- This reverses ADR-007 clause 5 deliberately.
--
-- Tenant scope is not duplicated here: workspace_id, user_id, ready status and
-- expiry are joined from project_documents, so the six-condition ACL stays in
-- one place and cannot drift between two tables.
CREATE TABLE project_document_chunks (
    vector_id bigserial PRIMARY KEY,
    chunk_id uuid NOT NULL,
    document_id uuid NOT NULL REFERENCES project_documents(id) ON DELETE CASCADE,
    project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    chunk_index integer NOT NULL CHECK (chunk_index >= 0),
    text text NOT NULL CHECK (btrim(text) <> ''),
    page_start integer NOT NULL CHECK (page_start >= 1),
    page_end integer NOT NULL CHECK (page_end >= page_start),
    section text,
    fts tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED,
    created_at timestamptz NOT NULL DEFAULT now(),
    -- chunk_id is a deterministic uuid5 of the document coordinates and text,
    -- so a retried ingestion re-uses the same row and keeps vector_id stable.
    UNIQUE (document_id, chunk_id)
);
CREATE INDEX project_document_chunks_fts_idx
    ON project_document_chunks USING gin (fts);
CREATE INDEX project_document_chunks_scope_idx
    ON project_document_chunks (project_id, document_id, chunk_index);

-- The vector store is no longer Qdrant-specific, and chunk rows are now a
-- second deletion target that the audit must account for.
ALTER TABLE document_deletion_audits RENAME COLUMN qdrant_outcome TO vector_store_outcome;
ALTER TABLE document_deletion_audits
    DROP CONSTRAINT IF EXISTS document_deletion_audits_qdrant_outcome_check;
ALTER TABLE document_deletion_audits
    ADD CONSTRAINT document_deletion_audits_vector_store_outcome_check
    CHECK (vector_store_outcome IN ('pending', 'deleted', 'failed'));
ALTER TABLE document_deletion_audits
    ADD COLUMN chunks_outcome text NOT NULL DEFAULT 'pending'
    CHECK (chunks_outcome IN ('pending', 'deleted', 'failed'));
