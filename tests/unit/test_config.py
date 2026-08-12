import pytest

from cowork_agent.config import QdrantSettings, SessionSettings, SupabaseStorageSettings

CLOUD_URL = "https://example.us-west-1-0.aws.cloud.qdrant.io"


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
    assert settings.vector_size == 768


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
