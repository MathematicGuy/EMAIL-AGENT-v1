import pytest

from cowork_agent.config import KnowledgeIngestionSettings


def test_settings_require_key_only_when_ocr_enabled() -> None:
    settings = KnowledgeIngestionSettings.from_env(
        {"KNOWLEDGE_INGEST_OCR_ENABLED": "false"}, load_env_file=False
    )

    assert settings.ocr_enabled is False
    assert settings.api_key == ""

    with pytest.raises(ValueError, match="MISTRAL_API_KEY"):
        KnowledgeIngestionSettings.from_env({}, load_env_file=False)


def test_settings_use_knowledge_ingestion_defaults() -> None:
    settings = KnowledgeIngestionSettings.from_env(
        {"MISTRAL_API_KEY": "secret"}, load_env_file=False
    )

    assert settings.model == "mistral-ocr-latest"
    assert settings.timeout_seconds == 60
    assert settings.max_attempts == 3
    assert settings.max_bytes == 26_214_400
    assert settings.max_pdf_pages == 100
    assert settings.max_ocr_pages == 100


def test_settings_hide_secret() -> None:
    settings = KnowledgeIngestionSettings.from_env(
        {"MISTRAL_API_KEY": "secret"}, load_env_file=False
    )

    assert "secret" not in repr(settings)
