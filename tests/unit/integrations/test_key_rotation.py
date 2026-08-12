from __future__ import annotations

import asyncio

import pytest

from cowork_agent.integrations.key_rotation import (
    APIKeyRotator,
    mask_api_key,
    parse_api_keys_from_env,
)


def test_parse_api_keys_from_env_flexible_patterns() -> None:
    env = {
        "COHERE_API_KEY": "key1",
        "COHERE_API_KEY2": "key2",
        "COHERE_API_KEY3": "key3",
        "GEMINI_API_KEY_1": "gkey1",
        "GEMINI_API_KEY_2": "gkey2",
    }
    cohere_keys = parse_api_keys_from_env(env, "COHERE_API_KEY")
    assert cohere_keys == ("key1", "key2", "key3")

    gemini_keys = parse_api_keys_from_env(env, "GEMINI_API_KEY")
    assert gemini_keys == ("gkey1", "gkey2")


def test_parse_api_keys_from_env_placeholders_and_duplicates() -> None:
    env = {
        "COHERE_API_KEY": "replace-with-your-key",
        "COHERE_API_KEY2": "valid_key_1",
        "COHERE_API_KEY3": "valid_key_1",  # duplicate
        "COHERE_API_KEY4": "valid_key_2",
    }
    keys = parse_api_keys_from_env(env, "COHERE_API_KEY")
    assert keys == ("valid_key_1", "valid_key_2")


def test_parse_api_keys_from_env_no_valid_keys_raises() -> None:
    env = {
        "COHERE_API_KEY": "replace-with-key",
        "COHERE_API_KEY2": "   ",
    }
    with pytest.raises(ValueError, match="At least one API key"):
        parse_api_keys_from_env(env, "COHERE_API_KEY")


def test_parse_api_keys_from_env_numerical_sorting() -> None:
    env = {
        "COHERE_API_KEY10": "key10",
        "COHERE_API_KEY2": "key2",
        "COHERE_API_KEY_1": "key1",
    }
    keys = parse_api_keys_from_env(env, "COHERE_API_KEY")
    assert keys == ("key1", "key2", "key10")


def test_mask_api_key() -> None:
    assert mask_api_key("cohere_1234567890abcdef") == "cohe...cdef"
    assert mask_api_key("short") == "sh***"
    assert mask_api_key("abc") == "***"
    assert mask_api_key("") == "***"


@pytest.mark.asyncio
async def test_key_rotator_round_robin() -> None:
    rotator = APIKeyRotator(["k1", "k2", "k3"], provider_name="Test")
    assert rotator.keys == ("k1", "k2", "k3")
    assert rotator.provider_name == "Test"

    c1 = await rotator.candidates(max_attempts=2)
    assert c1 == ("k1", "k2")

    c2 = await rotator.candidates(max_attempts=2)
    assert c2 == ("k2", "k3")

    c3 = await rotator.candidates(max_attempts=2)
    assert c3 == ("k3", "k1")


@pytest.mark.asyncio
async def test_key_rotator_concurrent_access() -> None:
    rotator = APIKeyRotator(["k1", "k2", "k3"], provider_name="Test")

    async def worker() -> tuple[str, ...]:
        return await rotator.candidates(max_attempts=1)

    results = await asyncio.gather(*(worker() for _ in range(6)))
    # 6 calls should rotate through k1, k2, k3 twice
    flattened = [r[0] for r in results]
    assert len(flattened) == 6
    assert set(flattened) == {"k1", "k2", "k3"}


def test_key_rotator_from_env() -> None:
    env = {
        "COHERE_API_KEY_1": "key1",
        "COHERE_API_KEY_2": "key2",
    }
    rotator = APIKeyRotator.from_env("COHERE_API_KEY", environ=env)
    assert rotator.keys == ("key1", "key2")
    assert rotator.provider_name == "Cohere"


def test_key_rotator_empty_keys_raises() -> None:
    with pytest.raises(ValueError, match="At least one"):
        APIKeyRotator([])
