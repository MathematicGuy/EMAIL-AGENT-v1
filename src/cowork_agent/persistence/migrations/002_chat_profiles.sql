-- V2-M2: namespaced declarative chat profile (PRD-v2 FR-03..FR-05, FR-15/16).
--
-- One row per (tenant, user) for the ai_chat feature; profile_key is the
-- session-independent namespace key tenant/user/feature/long_term.
-- Only compact explicit preferences are stored: no email body, no chat
-- transcript, no attachment content (invariant 1). The source_type CHECK
-- makes FR-04's explicit-only rule a storage constraint, not just app code.
CREATE TABLE chat_profiles (
    profile_key text PRIMARY KEY,
    profile_id text NOT NULL,
    tenant_id text NOT NULL,
    user_id text NOT NULL,
    feature text NOT NULL CHECK (feature = 'ai_chat'),
    language text,
    timezone text,
    assistant_persona text,
    response_tone text,
    source_type text NOT NULL CHECK (source_type = 'explicit_user_config'),
    expires_at timestamptz,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    UNIQUE (tenant_id, user_id, feature)
);

-- Supports the retention purge of expired profiles (FR-16).
CREATE INDEX chat_profiles_expires_idx ON chat_profiles (expires_at)
    WHERE expires_at IS NOT NULL;
