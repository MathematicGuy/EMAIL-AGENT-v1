-- Chunk text discarded here can only be restored by re-ingesting the source
-- PDFs from private object storage.
ALTER TABLE document_deletion_audits DROP COLUMN IF EXISTS chunks_outcome;
ALTER TABLE document_deletion_audits
    DROP CONSTRAINT IF EXISTS document_deletion_audits_vector_store_outcome_check;
ALTER TABLE document_deletion_audits RENAME COLUMN vector_store_outcome TO qdrant_outcome;
ALTER TABLE document_deletion_audits
    ADD CONSTRAINT document_deletion_audits_qdrant_outcome_check
    CHECK (qdrant_outcome IN ('pending', 'deleted', 'failed'));

DROP INDEX IF EXISTS project_document_chunks_scope_idx;
DROP INDEX IF EXISTS project_document_chunks_fts_idx;
DROP TABLE IF EXISTS project_document_chunks;
