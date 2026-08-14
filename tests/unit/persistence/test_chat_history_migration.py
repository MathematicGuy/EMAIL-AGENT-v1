from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[3] / "src/cowork_agent/persistence/migrations"


def test_chat_history_schema_stores_completed_turns_and_a_bounded_title() -> None:
    sql = (MIGRATIONS / "011_chat_history.sql").read_text(encoding="utf-8")

    assert "ALTER TABLE chat_sessions" in sql
    assert "ADD COLUMN title text" in sql
    assert "CREATE TABLE chat_turns" in sql
    assert "PRIMARY KEY (session_id, turn_id)" in sql
    assert "REFERENCES chat_sessions(id) ON DELETE CASCADE" in sql


def test_chat_history_down_migration_removes_the_new_storage() -> None:
    sql = (MIGRATIONS / "011_chat_history.down.sql").read_text(encoding="utf-8")

    assert "DROP TABLE IF EXISTS chat_turns" in sql
    assert "DROP COLUMN IF EXISTS title" in sql
