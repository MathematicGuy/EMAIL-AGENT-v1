from pathlib import Path

import pytest

from cowork_agent.config import (
    LOCAL_POSTGRES_DEFAULT_URL,
    EmailRagQualitySettings,
    GeminiEmbeddingSettings,
    OpenRouterSettings,
    RerankerSettings,
    SessionSettings,
    SupabaseStorageSettings,
    UserDocumentsSettings,
    database_url,
    load_runtime_environment,
    postgres_mode,
)


def test_load_runtime_environment_reads_feature_flags_from_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / ".env").write_text("DATABASE_URL=postgresql://example/db\n")
    (tmp_path / "config").write_text("USER_DOCUMENTS_ENABLED=false\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("POSTGRES_MODE", raising=False)
    monkeypatch.delenv("USER_DOCUMENTS_ENABLED", raising=False)

    load_runtime_environment()

    assert database_url() == "postgresql://example/db"
    assert postgres_mode() == ""
    assert UserDocumentsSettings.from_env().enabled is False


def test_settings_parser_does_not_read_dotenv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Implicit dotenv I/O can turn an offline settings read into a billed call."""
    (tmp_path / ".env").write_text("OPENROUTER_API_KEY=fake-provider-key\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
        OpenRouterSettings.from_env()


def test_database_url_follows_postgres_mode_local_and_ignores_legacy_url() -> None:
    assert (
        database_url(
            {
                "POSTGRES_MODE": "local",
                "DATABASE_URL_LOCAL": "postgresql://cowork:dev@127.0.0.1:5432/cowork",
                "DATABASE_URL": "postgresql://ignored/db",
            }
        )
        == "postgresql://cowork:dev@127.0.0.1:5432/cowork"
    )
    assert postgres_mode({"POSTGRES_MODE": "local"}) == "local"


def test_database_url_local_mode_defaults_to_compose_app_db() -> None:
    assert database_url({"POSTGRES_MODE": "local"}) == LOCAL_POSTGRES_DEFAULT_URL


def test_database_url_follows_postgres_mode_cloud() -> None:
    cloud = "postgresql://postgres:secret@db.example:5432/postgres?sslmode=require"
    assert (
        database_url(
            {
                "POSTGRES_MODE": "cloud",
                "DATABASE_URL_CLOUD": cloud,
                "DATABASE_URL": "postgresql://ignored/db",
            }
        )
        == cloud
    )


def test_database_url_cloud_mode_requires_cloud_url() -> None:
    with pytest.raises(ValueError, match="DATABASE_URL_CLOUD"):
        database_url({"POSTGRES_MODE": "cloud"})


def test_database_url_cloud_mode_rejects_transaction_pooler_port() -> None:
    with pytest.raises(ValueError, match="5432"):
        database_url(
            {
                "POSTGRES_MODE": "cloud",
                "DATABASE_URL_CLOUD": (
                    "postgresql://postgres:secret@aws-0.pooler.supabase.com:6543/postgres"
                ),
            }
        )


def test_database_url_off_mode_is_the_sqlite_fallback() -> None:
    assert (
        database_url({"POSTGRES_MODE": "off", "DATABASE_URL": "postgresql://ignored/db"})
        == ""
    )


def test_database_url_without_mode_keeps_legacy_database_url() -> None:
    assert database_url({"DATABASE_URL": "postgresql://example/db"}) == "postgresql://example/db"


def test_postgres_mode_rejects_unknown_value() -> None:
    with pytest.raises(ValueError, match="local"):
        database_url({"POSTGRES_MODE": "supabase"})


def test_session_cookie_defaults_insecure_on_local_postgres_mode() -> None:
    settings = SessionSettings.from_env({"POSTGRES_MODE": "local"})

    assert settings.cookie_secure is False


def test_session_cookie_defaults_insecure_on_sqlite_fallback() -> None:
    settings = SessionSettings.from_env({"POSTGRES_MODE": "off"})

    assert settings.cookie_secure is False


def test_session_cookie_explicit_flag_wins_on_local_postgres_mode() -> None:
    settings = SessionSettings.from_env(
        {"POSTGRES_MODE": "local", "APP_SESSION_COOKIE_SECURE": "true"},
    )

    assert settings.cookie_secure is True


def test_project_gemini_embedding_settings_default_to_1024() -> None:
    settings = GeminiEmbeddingSettings.from_env(
        {"GEMINI_API_KEY_1": "key-1"}
    )

    assert settings.model == "gemini-embedding-2"
    assert settings.dimensions == 1024
    assert settings.batch_size == 100


def test_project_documents_read_the_turbovec_index_root() -> None:
    """ADR-008: the project plane is addressed by a directory, not a collection."""

    environ = {"USER_DOCUMENTS_INDEX_ROOT": "var/private-project-indexes"}

    assert (
        UserDocumentsSettings.from_env(environ).index_root
        == "var/private-project-indexes"
    )
    assert (
        UserDocumentsSettings.from_env({}).index_root
        == "var/project-indexes"
    )


def test_project_documents_are_enabled_by_default() -> None:
    settings = UserDocumentsSettings.from_env({})

    assert settings.enabled is True
    assert settings.retrieval_timeout_ms == 10_000


def test_session_settings_load_cookie_contract() -> None:
    settings = SessionSettings.from_env(
        {
            "APP_SESSION_TTL_SECONDS": "3600",
            "APP_SESSION_COOKIE_NAME": "cowork_session",
            "APP_SESSION_COOKIE_SECURE": "true",
        },
    )

    assert settings.session_ttl_seconds == 3600
    assert settings.cookie_name == "cowork_session"
    assert settings.cookie_secure is True


@pytest.mark.parametrize("ttl", ["0", "-1"])
def test_session_settings_reject_non_positive_ttl(ttl: str) -> None:
    with pytest.raises(ValueError, match="must be positive"):
        SessionSettings.from_env(
            {"APP_SESSION_TTL_SECONDS": ttl}
        )


def test_supabase_storage_settings_keep_the_secret_out_of_repr() -> None:
    settings = SupabaseStorageSettings.from_env(
        {
            "SUPABASE_URL": "https://project.supabase.co/",
            "SUPABASE_SECRET_KEY": "server-secret",
        },
    )

    assert settings.url == "https://project.supabase.co"
    assert settings.bucket == "project-documents"
    assert "server-secret" not in repr(settings)


def test_reranker_settings_cohere_model_from_env() -> None:
    settings = RerankerSettings.from_env(
        {
            "RERANKER_MODEL": "rerank-v4.0-fast",
            "COHERE_API_KEY": "cohere-key-1",
        },
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
    )

    assert settings.model == "jina-reranker-v2"
    assert settings.rotator.provider_name == "Jina"
    assert settings.rotator.keys == ("jina-key-1",)


_OPENROUTER_BASE = {
    "OPENROUTER_API_KEY": "test-key",
    "OPENROUTER_MODEL": "deepseek/x",
}


@pytest.mark.parametrize(
    "environ",
    [
        _OPENROUTER_BASE,
        {**_OPENROUTER_BASE, "OPENROUTER_ALLOWED_MODELS": ""},
        {**_OPENROUTER_BASE, "OPENROUTER_ALLOWED_MODELS": "   "},
    ],
)
def test_openrouter_allowed_models_missing_is_empty(environ: dict[str, str]) -> None:
    settings = OpenRouterSettings.from_env(environ)

    assert settings.allowed_models == ()
    assert settings.fallback_models() == ()


def test_openrouter_allowed_models_parses_json_list_in_order() -> None:
    settings = OpenRouterSettings.from_env(
        {
            **_OPENROUTER_BASE,
            "OPENROUTER_ALLOWED_MODELS": '["openai/gpt", "deepseek/x"]',
        },
    )

    assert settings.allowed_models == ("openai/gpt", "deepseek/x")


def test_openrouter_allowed_models_reject_invalid_json() -> None:
    with pytest.raises(ValueError, match="OPENROUTER_ALLOWED_MODELS"):
        OpenRouterSettings.from_env(
            {**_OPENROUTER_BASE, "OPENROUTER_ALLOWED_MODELS": "not-json"},
        )


def test_openrouter_allowed_models_reject_non_list() -> None:
    with pytest.raises(ValueError, match="OPENROUTER_ALLOWED_MODELS"):
        OpenRouterSettings.from_env(
            {**_OPENROUTER_BASE, "OPENROUTER_ALLOWED_MODELS": '{"openai/gpt": true}'},
        )


def test_openrouter_allowed_models_reject_empty_entry() -> None:
    with pytest.raises(ValueError, match="OPENROUTER_ALLOWED_MODELS"):
        OpenRouterSettings.from_env(
            {**_OPENROUTER_BASE, "OPENROUTER_ALLOWED_MODELS": '["openai/gpt", ""]'},
        )


def test_openrouter_allowed_models_reject_non_string() -> None:
    with pytest.raises(ValueError, match="OPENROUTER_ALLOWED_MODELS"):
        OpenRouterSettings.from_env(
            {**_OPENROUTER_BASE, "OPENROUTER_ALLOWED_MODELS": '["openai/gpt", 1]'},
        )


def test_openrouter_fallback_models_omits_primary_preserving_order() -> None:
    settings = OpenRouterSettings.from_env(
        {
            "OPENROUTER_API_KEY": "test-key",
            "OPENROUTER_MODEL": "deepseek/x",
            "OPENROUTER_ALLOWED_MODELS": '["openai/gpt", "deepseek/x"]',
        },
    )

    assert settings.fallback_models() == ("openai/gpt",)

def test_email_rag_quality_settings_default_and_bounds() -> None:
    settings = EmailRagQualitySettings.from_env({})
    assert (settings.min_rerank_score, settings.relative_cutoff_ratio) == (0.30, 0.85)
    with pytest.raises(ValueError, match="EMAIL_RAG_MIN_RERANK_SCORE"):
        EmailRagQualitySettings.from_env(
            {"EMAIL_RAG_MIN_RERANK_SCORE": "1.01"}
        )
