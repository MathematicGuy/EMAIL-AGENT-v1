import pytest

from cowork_agent.config import ChatMemorySettings


def test_chat_memory_settings_use_bounded_local_defaults() -> None:
    settings = ChatMemorySettings.from_env({}, load_env_file=False)

    assert settings.max_turns == 20
    assert settings.ttl_seconds == 1800
    assert settings.profile_retention_seconds is None
    assert settings.episode_retention_seconds is None


def test_chat_memory_settings_load_explicit_positive_values() -> None:
    settings = ChatMemorySettings.from_env(
        {"CHAT_MEMORY_MAX_TURNS": "8", "CHAT_MEMORY_TTL_SECONDS": "900"},
        load_env_file=False,
    )

    assert settings.max_turns == 8
    assert settings.ttl_seconds == 900


def test_chat_memory_retention_is_optional_bounded_and_constructor_compatible() -> None:
    assert ChatMemorySettings(8, 60).profile_retention_seconds is None
    settings = ChatMemorySettings.from_env(
        {"CHAT_PROFILE_RETENTION_SECONDS": "60", "CHAT_EPISODE_RETENTION_SECONDS": "3600"},
        load_env_file=False,
    )
    assert settings.profile_retention_seconds == 60
    assert settings.episode_retention_seconds == 3600


@pytest.mark.parametrize("value", ["0", "-1", "31536001"])
def test_chat_memory_retention_rejects_invalid_values(value: str) -> None:
    with pytest.raises(ValueError):
        ChatMemorySettings.from_env({"CHAT_PROFILE_RETENTION_SECONDS": value}, load_env_file=False)


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
