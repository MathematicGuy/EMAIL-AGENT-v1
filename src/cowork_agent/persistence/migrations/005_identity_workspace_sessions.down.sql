-- Manual rollback companion for migration 005. Apply only after stopping
-- application processes that issue or resolve opaque sessions.
DROP TABLE app_sessions;
DROP TABLE workspace_members;
ALTER TABLE mailbox_connections DROP COLUMN workspace_id;
DROP TABLE workspaces;
DROP TABLE app_users;
