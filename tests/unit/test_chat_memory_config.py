import pytest

from cowork_agent.config import ChatMemorySettings


def test_chat_memory_settings_use_bounded_local_defaults() -> None:
    settings = ChatMemorySettings.from_env({}, load_env_file=False)

    assert settings.max_turns == 20
    assert settings.ttl_seconds == 1800


def test_chat_memory_settings_load_explicit_positive_values() -> None:
    settings = ChatMemorySettings.from_env(
        {"CHAT_MEMORY_MAX_TURNS": "8", "CHAT_MEMORY_TTL_SECONDS": "900"},
        load_env_file=False,
    )

    assert settings.max_turns == 8
    assert settings.ttl_seconds == 900


@pytest.mark.parametrize(
    "environment",
    [
        {"CHAT_MEMORY_MAX_TURNS": "0"},
        {"CHAT_MEMORY_MAX_TURNS": "-1"},
        {"CHAT_MEMORY_TTL_SECONDS": "0"},
        {"CHAT_MEMORY_TTL_SECONDS": "-1"},
    ],
)
def test_chat_memory_settings_reject_non_positive_bounds(
    environment: dict[str, str],
) -> None:
    with pytest.raises(ValueError, match="must be positive"):
        ChatMemorySettings.from_env(environment, load_env_file=False)
