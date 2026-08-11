"""Static contract checks for the body-free TaskEpisode migration (V2-M3.4a)."""

from pathlib import Path

from cowork_agent.domain.chat_contracts import (
    MAX_EPISODE_CITATION_DOCUMENT_ID_LENGTH,
    MAX_EPISODE_CITATION_DOCUMENT_TITLE_LENGTH,
    MAX_EPISODE_CITATION_SECTION_LENGTH,
    MAX_EPISODE_CITATION_SOURCE_URL_LENGTH,
    MAX_TASK_ACTION_PLAN_ITEM_LENGTH,
    MAX_TASK_ACTION_PLAN_ITEMS,
    MAX_TASK_MISSING_INFORMATION_ITEM_LENGTH,
    MAX_TASK_MISSING_INFORMATION_ITEMS,
    MAX_TASK_RAG_CITATIONS,
    MAX_TASK_REQUEST_PARAPHRASE_LENGTH,
    MAX_TASK_TITLE_LENGTH,
)

MIGRATIONS = (
    Path(__file__).resolve().parents[3] / "src" / "cowork_agent" / "persistence" / "migrations"
)


def test_task_episode_migration_binds_all_public_compact_limits_and_privacy_constraints() -> None:
    migration = (MIGRATIONS / "004_task_episodes.sql").read_text(encoding="utf-8")
    schema_sql = "\n".join(line for line in migration.splitlines() if not line.startswith("--"))

    def regex_limit(chunk_size: int, groups: int) -> str:
        return f'^(?:.{{1,{chunk_size}}}){{1,{groups}}}$'

    assert f"char_length(task_title) <= {MAX_TASK_TITLE_LENGTH}" in schema_sql
    assert (
        f"char_length(minimal_request_paraphrase) <= {MAX_TASK_REQUEST_PARAPHRASE_LENGTH}"
        in schema_sql
    )
    assert f"jsonb_array_length(action_plan) <= {MAX_TASK_ACTION_PLAN_ITEMS}" in schema_sql
    assert regex_limit(MAX_TASK_ACTION_PLAN_ITEM_LENGTH // 2, 2) in schema_sql
    assert (
        f"jsonb_array_length(missing_information) <= {MAX_TASK_MISSING_INFORMATION_ITEMS}"
        in schema_sql
    )
    assert regex_limit(MAX_TASK_MISSING_INFORMATION_ITEM_LENGTH // 2, 2) in schema_sql
    assert f"jsonb_array_length(rag_citations) <= {MAX_TASK_RAG_CITATIONS}" in schema_sql
    assert regex_limit(MAX_EPISODE_CITATION_DOCUMENT_ID_LENGTH // 2, 2) in schema_sql
    assert regex_limit(MAX_EPISODE_CITATION_DOCUMENT_TITLE_LENGTH // 2, 2) in schema_sql
    assert regex_limit(MAX_EPISODE_CITATION_SECTION_LENGTH // 2, 2) in schema_sql
    assert regex_limit(MAX_EPISODE_CITATION_SOURCE_URL_LENGTH // 16, 16) in schema_sql

    assert "CREATE TABLE task_episodes" in migration
    assert "GENERATED ALWAYS AS" in migration
    assert "search_vector tsvector GENERATED ALWAYS AS" in schema_sql
    assert (
        "CREATE INDEX task_episodes_search_idx ON task_episodes USING GIN (search_vector)"
        in schema_sql
    )
    assert "keyvalue()" in schema_sql
    for citation_key in ("document_id", "document_title", "section", "source_url"):
        assert citation_key in schema_sql
    assert "task_json" not in migration
    for forbidden in (
        "raw_email",
        "attachment",
        "transcript",
        "message_body",
        "tool_payload",
        "gmail_message",
        "mailbox_connection",
        "run_id",
        "source_tool",
        "gmail_url",
        "task_id",
        "copied_chunk",
        "chunk_content",
        "rag_content",
    ):
        assert forbidden not in schema_sql
    assert "REFERENCES" not in schema_sql


def test_task_episode_down_migration_drops_only_its_table() -> None:
    down = (MIGRATIONS / "004_task_episodes.down.sql").read_text(encoding="utf-8").strip()

    assert down == "DROP TABLE IF EXISTS task_episodes;"


def test_task_episode_retrieval_uses_explicit_fts_matching() -> None:
    repository = (MIGRATIONS.parent / "repositories" / "postgres.py").read_text(
        encoding="utf-8"
    )

    assert "search_vector @@ terms.tsquery" in repository
