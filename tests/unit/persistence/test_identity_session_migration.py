from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[3] / "src/cowork_agent/persistence/migrations"


def _migration(name: str) -> str:
    return (MIGRATIONS / name).read_text(encoding="utf-8")


def test_identity_session_schema_stores_only_a_token_hash() -> None:
    sql = _migration("005_identity_workspace_sessions.sql")

    assert "CREATE TABLE app_users" in sql
    assert "CREATE TABLE workspaces" in sql
    assert "CREATE TABLE workspace_members" in sql
    assert "CREATE TABLE app_sessions" in sql
    assert "token_hash char(64) PRIMARY KEY" in sql
    assert "session_token" not in sql


def test_identity_session_schema_adds_a_workspace_to_mailbox_connections() -> None:
    sql = _migration("005_identity_workspace_sessions.sql")

    assert "ALTER TABLE mailbox_connections ADD COLUMN workspace_id uuid" in sql
    assert "REFERENCES workspaces(id)" in sql
    assert "expires_at > created_at" in sql


def test_identity_session_down_migration_reverses_new_schema_in_dependency_order() -> None:
    sql = _migration("005_identity_workspace_sessions.down.sql")

    assert sql.index("DROP TABLE app_sessions") < sql.index("DROP TABLE app_users")
    assert "ALTER TABLE mailbox_connections DROP COLUMN workspace_id" in sql
