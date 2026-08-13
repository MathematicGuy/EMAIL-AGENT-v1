from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[3] / "src/cowork_agent/persistence/migrations"


def _migration(name: str) -> str:
    return (MIGRATIONS / name).read_text(encoding="utf-8")


def test_durable_chat_session_schema_stores_only_ownership_metadata() -> None:
    sql = _migration("006_durable_chat_sessions.sql")

    assert "CREATE TABLE chat_sessions" in sql
    assert "workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE" in sql
    assert "user_id uuid NOT NULL REFERENCES app_users(id) ON DELETE CASCADE" in sql
    assert "feature text NOT NULL CHECK (feature = 'ai_chat')" in sql
    assert "user_message" not in sql
    assert "assistant_message" not in sql


def test_durable_chat_session_down_migration_removes_the_ownership_table() -> None:
    sql = _migration("006_durable_chat_sessions.down.sql")

    assert "DROP TABLE IF EXISTS chat_sessions" in sql
