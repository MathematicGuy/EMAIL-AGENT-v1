from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[3] / "src/cowork_agent/persistence/migrations"


def _migration(name: str) -> str:
    return (MIGRATIONS / name).read_text(encoding="utf-8")


def test_project_document_schema_has_private_metadata_but_no_document_content() -> None:
    sql = _migration("007_projects_documents.sql")

    assert "CREATE TABLE projects" in sql
    assert "CREATE TABLE project_documents" in sql
    assert "CREATE TABLE document_ingestion_jobs" in sql
    assert "CREATE TABLE document_deletion_audits" in sql
    assert "content_sha256 char(64) NOT NULL" in sql
    assert "storage_key text NOT NULL" in sql
    assert "ALTER TABLE chat_sessions ADD COLUMN project_id uuid" in sql
    documents_table = sql.split("CREATE TABLE project_documents", maxsplit=1)[1].split(
        "\n);", maxsplit=1
    )[0]
    assert "raw_content" not in documents_table
    assert "extracted_text" not in documents_table
    assert "prompt text" not in documents_table


def test_project_document_down_migration_removes_dependents_before_projects() -> None:
    sql = _migration("007_projects_documents.down.sql")

    assert sql.index("DROP TABLE IF EXISTS document_deletion_audits") < sql.index(
        "DROP TABLE IF EXISTS projects"
    )
    assert "ALTER TABLE chat_sessions DROP COLUMN IF EXISTS project_id" in sql
