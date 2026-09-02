-- The project-document corpus is Vietnamese, and the lexical leg was indexing
-- it with the English text search configuration: English stemming and an
-- English stopword list applied to Vietnamese legal prose. "được" and "quy
-- định" are not English words, so nothing useful was stemmed, while genuine
-- query terms were silently dropped whenever they collided with an English
-- stopword ("a", "the", "no", "so", "it").
--
-- 'simple' folds case and nothing else, which is the honest treatment for a
-- language this deployment has no dictionary for. It is what the knowledge
-- plane already uses (repositories/postgres.py builds its tsquery that way),
-- so the two lexical legs now agree.
--
-- The column is GENERATED, so dropping and re-adding it rewrites the table and
-- every existing chunk is re-indexed under the new configuration. No
-- re-ingestion is required: the source text column is untouched.
ALTER TABLE project_document_chunks DROP COLUMN fts;
ALTER TABLE project_document_chunks
    ADD COLUMN fts tsvector GENERATED ALWAYS AS (to_tsvector('simple', text)) STORED;
CREATE INDEX project_document_chunks_fts_idx
    ON project_document_chunks USING gin (fts);
