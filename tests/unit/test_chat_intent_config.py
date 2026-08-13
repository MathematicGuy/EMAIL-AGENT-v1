import pytest

from cowork_agent.config import ChatIntentSettings


def test_chat_intent_settings_use_safe_document_routing_defaults() -> None:
    settings = ChatIntentSettings.from_env({}, default_model="provider-model", load_env_file=False)

    assert settings.enabled is True
    assert settings.model == "provider-model"
    assert settings.timeout_ms == 10_000
    assert settings.max_attempts == 2
    assert settings.tool_axis_enabled is False
    assert settings.company_rag_enabled is False


def test_chat_intent_settings_allow_kill_switch_and_model_override() -> None:
    settings = ChatIntentSettings.from_env(
        {
            "CHAT_INTENT_CLASSIFIER_ENABLED": "false",
            "CHAT_INTENT_CLASSIFIER_MODEL": "routing-model",
            "CHAT_INTENT_CLASSIFIER_TIMEOUT_MS": "1500",
            "USER_DOCUMENTS_TOOL_AXIS_ENABLED": "true",
            "CHAT_COMPANY_RAG_ENABLED": "true",
        },
        default_model="provider-model",
        load_env_file=False,
    )

    assert settings.enabled is False
    assert settings.model == "routing-model"
    assert settings.timeout_ms == 1500
    assert settings.tool_axis_enabled is True
    assert settings.company_rag_enabled is True


def test_chat_intent_settings_reject_invalid_timeout() -> None:
    with pytest.raises(ValueError):
        ChatIntentSettings.from_env(
            {"CHAT_INTENT_CLASSIFIER_TIMEOUT_MS": "120001"},
            default_model="provider-model",
            load_env_file=False,
        )
