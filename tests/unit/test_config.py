from pathlib import Path

import pytest

from cowork_agent.config import (
    GeminiEmbeddingSettings,
    QdrantSettings,
    RerankerSettings,
    SessionSettings,
    SupabaseStorageSettings,
    UserDocumentsSettings,
    database_url,
    load_runtime_environment,
)

CLOUD_URL = "https://example.us-west-1-0.aws.cloud.qdrant.io"


def test_load_runtime_environment_reads_feature_flags_from_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / ".env").write_text("DATABASE_URL=postgresql://example/db\n")
    (tmp_path / "config").write_text("USER_DOCUMENTS_ENABLED=false\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("USER_DOCUMENTS_ENABLED", raising=False)

    load_runtime_environment()

    assert database_url() == "postgresql://example/db"
    assert UserDocumentsSettings.from_env(load_env_file=False).enabled is False


def test_project_gemini_embedding_settings_default_to_3072() -> None:
    settings = GeminiEmbeddingSettings.from_env(
        {"GEMINI_API_KEY_1": "key-1"}, load_env_file=False
    )

    assert settings.model == "gemini-embedding-2"
    assert settings.dimensions == 3072
    assert settings.batch_size == 100


def test_project_documents_share_the_canonical_qdrant_collection_setting() -> None:
    environ = {"QDRANT_PROJECT_COLLECTION": "private-project-documents"}

    assert UserDocumentsSettings.from_env(
        environ, load_env_file=False
    ).collection_name == "private-project-documents"
    assert QdrantSettings.from_env(
        environ, load_env_file=False
    ).project_collection_name == "private-project-documents"


def test_project_documents_are_enabled_by_default() -> None:
    settings = UserDocumentsSettings.from_env({}, load_env_file=False)

    assert settings.enabled is True
    assert settings.retrieval_timeout_ms == 10_000
    assert settings.startup_timeout_ms == 30_000


def test_project_documents_allow_a_longer_qdrant_startup_timeout() -> None:
    settings = UserDocumentsSettings.from_env(
        {"USER_DOCUMENTS_STARTUP_TIMEOUT_MS": "60000"}, load_env_file=False
    )

    assert settings.startup_timeout_ms == 60_000


def test_session_settings_load_cookie_contract() -> None:
    settings = SessionSettings.from_env(
        {
            "APP_SESSION_TTL_SECONDS": "3600",
            "APP_SESSION_COOKIE_NAME": "cowork_session",
            "APP_SESSION_COOKIE_SECURE": "true",
        },
        load_env_file=False,
    )

    assert settings.session_ttl_seconds == 3600
    assert settings.cookie_name == "cowork_session"
    assert settings.cookie_secure is True


@pytest.mark.parametrize("ttl", ["0", "-1"])
def test_session_settings_reject_non_positive_ttl(ttl: str) -> None:
    with pytest.raises(ValueError, match="must be positive"):
        SessionSettings.from_env(
            {"APP_SESSION_TTL_SECONDS": ttl}, load_env_file=False
        )


def test_supabase_storage_settings_keep_the_secret_out_of_repr() -> None:
    settings = SupabaseStorageSettings.from_env(
        {
            "SUPABASE_URL": "https://project.supabase.co/",
            "SUPABASE_SECRET_KEY": "server-secret",
        },
        load_env_file=False,
    )

    assert settings.url == "https://project.supabase.co"
    assert settings.bucket == "project-documents"
    assert "server-secret" not in repr(settings)


def test_qdrant_settings_are_disabled_without_a_url() -> None:
    settings = QdrantSettings.from_env({}, load_env_file=False)

    assert settings.enabled is False
    assert settings.url == ""
    assert settings.collection_name == "company_knowledge"
    assert settings.vector_size == 1024


def test_qdrant_settings_load_cloud_configuration_from_env() -> None:
    settings = QdrantSettings.from_env(
        {
            "QDRANT_URL": f" {CLOUD_URL} ",
            "QDRANT_API_KEY": " secret-key ",
            "QDRANT_COLLECTION": "company_knowledge",
            "QDRANT_ENABLED": "true",
            "QDRANT_VECTOR_SIZE": "768",
        },
        load_env_file=False,
    )

    assert settings.enabled is True
    assert settings.url == CLOUD_URL
    assert settings.api_key == "secret-key"
    assert settings.vector_size == 768


def test_qdrant_settings_stay_disabled_when_the_url_is_a_placeholder() -> None:
    settings = QdrantSettings.from_env(
        {"QDRANT_URL": "replace-with-your-qdrant-url", "QDRANT_ENABLED": "true"},
        load_env_file=False,
    )

    assert settings.enabled is False


def test_qdrant_settings_ignore_a_url_when_explicitly_disabled() -> None:
    settings = QdrantSettings.from_env(
        {"QDRANT_URL": CLOUD_URL, "QDRANT_ENABLED": "false"},
        load_env_file=False,
    )

    assert settings.enabled is False
    assert settings.url == CLOUD_URL


def test_qdrant_settings_never_repr_the_api_key() -> None:
    settings = QdrantSettings.from_env(
        {"QDRANT_URL": CLOUD_URL, "QDRANT_API_KEY": "secret-key", "QDRANT_ENABLED": "true"},
        load_env_file=False,
    )

    assert "secret-key" not in repr(settings)


@pytest.mark.parametrize("value", ["0", "-1"])
def test_qdrant_settings_reject_a_non_positive_vector_size(value: str) -> None:
    with pytest.raises(ValueError, match="must be positive"):
        QdrantSettings.from_env({"QDRANT_VECTOR_SIZE": value}, load_env_file=False)


def test_reranker_settings_cohere_model_from_env() -> None:
    settings = RerankerSettings.from_env(
        {
            "RERANKER_MODEL": "rerank-v4.0-fast",
            "COHERE_API_KEY": "cohere-key-1",
        },
        load_env_file=False,
    )

    assert settings.model == "rerank-v4.0-fast"
    assert settings.rotator.provider_name == "Cohere"
    assert settings.rotator.keys == ("cohere-key-1",)
    assert settings.timeout_seconds == 10.0
    assert settings.rotate_on_rate_limit is True


def test_reranker_settings_jina_model_from_env() -> None:
    settings = RerankerSettings.from_env(
        {
            "RERANKER_MODEL": "jina-reranker-v2",
            "JINA_API_KEY": "jina-key-1",
        },
        load_env_file=False,
    )

    assert settings.model == "jina-reranker-v2"
    assert settings.rotator.provider_name == "Jina"
    assert settings.rotator.keys == ("jina-key-1",)

