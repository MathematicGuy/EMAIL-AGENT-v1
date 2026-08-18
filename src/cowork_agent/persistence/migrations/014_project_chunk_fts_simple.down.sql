-- Restores the English configuration. Queries must go back to
-- websearch_to_tsquery('english', ...) in the same change, or the tsquery and
-- the tsvector will disagree and the lexical leg will match nothing.
ALTER TABLE project_document_chunks DROP COLUMN fts;
ALTER TABLE project_document_chunks
    ADD COLUMN fts tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED;
CREATE INDEX project_document_chunks_fts_idx
    ON project_document_chunks USING gin (fts);
