CREATE TABLE IF NOT EXISTS service_heartbeats (
    service_name text PRIMARY KEY,
    heartbeat_at timestamptz NOT NULL
);
