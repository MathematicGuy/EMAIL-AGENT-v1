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


def test_chat_turn_lifecycle_migration_supports_idempotent_pending_turns() -> None:
    sql = (MIGRATIONS / "014_chat_turn_lifecycle.sql").read_text(encoding="utf-8")

    assert "ALTER COLUMN assistant_message DROP NOT NULL" in sql
    assert "ADD COLUMN status text NOT NULL DEFAULT 'completed'" in sql
    assert "'generating'" in sql
    assert "'usage_limited'" in sql
    assert "'rate_limited'" in sql
    assert "ADD COLUMN idempotency_key text" in sql
    assert "UNIQUE (session_id, idempotency_key)" in sql
    assert "ADD COLUMN error_code text" in sql


def test_chat_turn_lifecycle_down_migration_restores_completed_turn_shape() -> None:
    sql = (MIGRATIONS / "014_chat_turn_lifecycle.down.sql").read_text(encoding="utf-8")

    assert "DELETE FROM chat_turns WHERE assistant_message IS NULL" in sql
    assert "ALTER COLUMN assistant_message SET NOT NULL" in sql
    assert "DROP COLUMN IF EXISTS status" in sql
    assert "DROP COLUMN IF EXISTS idempotency_key" in sql
    assert "DROP COLUMN IF EXISTS error_code" in sql
