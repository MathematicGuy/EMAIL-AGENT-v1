-- LLM-generated aggregate of non-actionable messages; never raw email content.
ALTER TABLE digest_runs
    ADD COLUMN filtered_summary text CHECK (
        filtered_summary IS NULL OR char_length(filtered_summary) <= 600
    );
