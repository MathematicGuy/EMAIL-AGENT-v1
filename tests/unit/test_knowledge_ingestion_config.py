import pytest

from cowork_agent.config import KnowledgeIngestionSettings


def test_settings_require_key_only_when_ocr_enabled() -> None:
    settings = KnowledgeIngestionSettings.from_env(
        {"KNOWLEDGE_INGEST_OCR_ENABLED": "false"}
    )

    assert settings.ocr_enabled is False
    assert settings.api_key == ""

    with pytest.raises(ValueError, match="MISTRAL_API_KEY"):
        KnowledgeIngestionSettings.from_env({})


def test_settings_use_knowledge_ingestion_defaults() -> None:
    settings = KnowledgeIngestionSettings.from_env(
        {"MISTRAL_API_KEY": "secret"}
    )

    assert settings.model == "mistral-ocr-latest"
    assert settings.timeout_seconds == 60
    assert settings.max_attempts == 3
    assert settings.max_bytes == 26_214_400
    assert settings.max_pdf_pages == 100
    assert settings.max_ocr_pages == 100


def test_settings_hide_secret() -> None:
    settings = KnowledgeIngestionSettings.from_env(
        {"MISTRAL_API_KEY": "secret"}
    )

    assert "secret" not in repr(settings)


def test_settings_supports_extraction_mode_env() -> None:
    adaptive_settings = KnowledgeIngestionSettings.from_env(
        {"EXTRACTION_MODE": "adaptive"}
    )
    assert adaptive_settings.extraction_mode == "adaptive"
    assert adaptive_settings.ocr_enabled is False

    basic_settings = KnowledgeIngestionSettings.from_env(
        {"EXTRACTION_MODE": "basic"}
    )
    assert basic_settings.extraction_mode == "adaptive"
    assert basic_settings.ocr_enabled is False

    adv_settings = KnowledgeIngestionSettings.from_env(
        {"EXTRACTION_MODE": "advance", "MISTRAL_API_KEY": "secret"}
    )
    assert adv_settings.extraction_mode == "advance"
    assert adv_settings.ocr_enabled is True

    with pytest.raises(ValueError, match="Invalid EXTRACTION_MODE"):
        KnowledgeIngestionSettings.from_env(
            {"EXTRACTION_MODE": "invalid"}
        )


