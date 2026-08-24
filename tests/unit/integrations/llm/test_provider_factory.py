"""Provider factory name normalization."""

import pytest

from cowork_agent.integrations.llm.provider_factory import normalize_llm_provider


def test_normalize_maps_mimo_and_rejects_unknown() -> None:
    assert normalize_llm_provider("Gemini") == "gemini"
    assert normalize_llm_provider("MIMO") == "mimo"
    assert normalize_llm_provider("mimo") == "mimo"
    with pytest.raises(ValueError, match="LLM_PROVIDER must be"):
        normalize_llm_provider("unknown")
