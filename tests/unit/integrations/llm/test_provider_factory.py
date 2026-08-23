"""Provider factory name normalization."""

import pytest

from cowork_agent.integrations.llm.provider_factory import normalize_llm_provider


def test_normalize_maps_vyne_alias_and_rejects_unknown() -> None:
    assert normalize_llm_provider("Gemini") == "gemini"
    assert normalize_llm_provider("vyne") == "vyce"
    with pytest.raises(ValueError, match="LLM_PROVIDER must be"):
        normalize_llm_provider("unknown")
