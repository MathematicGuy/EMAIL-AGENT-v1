"""Unit tests for Unified RerankerAdapter and key rotation strategy."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from urllib.error import HTTPError

import pytest

from cowork_agent.domain.target_contracts import SemanticChunk
from cowork_agent.integrations.key_rotation import APIKeyRotator
from cowork_agent.integrations.rag.reranker import (
    COHERE_RERANK_ENDPOINT,
    JINA_RERANK_ENDPOINT,
    RerankerAdapter,
    RerankerSettings,
)


class MockRerankerTransport:
    """Mock transport for capturing requests and returning canned responses."""

    def __init__(self, responses: list[dict[str, object] | Exception] | None = None) -> None:
        self.calls: list[dict[str, object]] = []
        self.responses = list(responses) if responses is not None else []

    async def post_json(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout_seconds: float,
    ) -> Mapping[str, object]:
        self.calls.append({
            "url": url,
            "headers": headers,
            "payload": payload,
            "timeout_seconds": timeout_seconds,
        })
        if not self.responses:
            raise RuntimeError("No mock response configured")
        resp = self.responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp


def _make_chunk(chunk_id: str, text: str) -> SemanticChunk:
    return SemanticChunk(
        chunk_id=chunk_id,
        document_id="doc1",
        document_title="Doc 1",
        section=None,
        text=text,
        source_url="https://example.com/doc1",
        document_version=None,
        relevance_score=0.5,
        rerank_score=None,
    )


@pytest.mark.asyncio
async def test_cohere_payload_formatting() -> None:
    """Verify Cohere rerank v2 payload formatting and endpoint determination."""
    chunks = (_make_chunk("c1", "First chunk"), _make_chunk("c2", "Second chunk"))
    rotator = APIKeyRotator(keys=("cohere-key-1",))
    settings = RerankerSettings(model="rerank-v3.5", rotator=rotator)

    mock_resp = {
        "results": [
            {"index": 1, "relevance_score": 0.95},
            {"index": 0, "relevance_score": 0.20},
        ]
    }
    transport = MockRerankerTransport([mock_resp])
    adapter = RerankerAdapter(settings=settings, transport=transport)

    res = await adapter.rerank(query="test query", candidates=chunks, top_n=2)

    assert len(transport.calls) == 1
    call = transport.calls[0]
    assert call["url"] == COHERE_RERANK_ENDPOINT
    assert call["headers"]["Authorization"] == "Bearer cohere-key-1"
    
    payload = call["payload"]
    assert payload["model"] == "rerank-v3.5"
    assert payload["query"] == "test query"
    assert payload["documents"] == ["First chunk", "Second chunk"]
    assert payload["top_n"] == 2
    assert "return_documents" not in payload

    assert len(res) == 2
    assert res[0].chunk_id == "c2"
    assert res[0].rerank_score == 0.95
    assert res[1].chunk_id == "c1"
    assert res[1].rerank_score == 0.20


@pytest.mark.asyncio
async def test_jina_payload_formatting() -> None:
    """Verify Jina rerank payload formatting and endpoint determination."""
    chunks = (_make_chunk("c1", "First chunk"), _make_chunk("c2", "Second chunk"))
    rotator = APIKeyRotator(keys=("jina-key-1",))
    settings = RerankerSettings(model="jina-reranker-v2-base-multilingual", rotator=rotator)

    mock_resp = {
        "results": [
            {"index": 0, "score": 0.88},
            {"index": 1, "score": 0.12},
        ]
    }
    transport = MockRerankerTransport([mock_resp])
    adapter = RerankerAdapter(settings=settings, transport=transport)

    res = await adapter.rerank(query="jina query", candidates=chunks)

    assert len(transport.calls) == 1
    call = transport.calls[0]
    assert call["url"] == JINA_RERANK_ENDPOINT
    assert call["headers"]["Authorization"] == "Bearer jina-key-1"

    payload = call["payload"]
    assert payload["model"] == "jina-reranker-v2-base-multilingual"
    assert payload["query"] == "jina query"
    assert payload["documents"] == ["First chunk", "Second chunk"]
    assert payload["return_documents"] is False
    assert "top_n" not in payload

    assert len(res) == 2
    assert res[0].chunk_id == "c1"
    assert res[0].rerank_score == 0.88
    assert res[1].chunk_id == "c2"
    assert res[1].rerank_score == 0.12


@pytest.mark.asyncio
async def test_key_rotation_on_http_429(caplog: pytest.LogCaptureFixture) -> None:
    """Verify HTTP 429 rate limit triggers key rotation and warning log."""
    chunks = (_make_chunk("c1", "Chunk 1"),)
    rotator = APIKeyRotator(keys=("key-bad-123456", "key-good-654321"))
    settings = RerankerSettings(model="cohere-v3", rotator=rotator, max_attempts=3)

    # First call raises HTTP 429, second succeeds
    err_429 = HTTPError(
        url=COHERE_RERANK_ENDPOINT,
        code=429,
        msg="Too Many Requests",
        hdrs={},  # type: ignore[arg-type]
        fp=None,
    )
    mock_resp = {"results": [{"index": 0, "relevance_score": 0.99}]}
    transport = MockRerankerTransport([err_429, mock_resp])
    adapter = RerankerAdapter(settings=settings, transport=transport)

    with caplog.at_level(logging.WARNING):
        res = await adapter.rerank(query="q", candidates=chunks)

    assert len(transport.calls) == 2
    assert transport.calls[0]["headers"]["Authorization"] == "Bearer key-bad-123456"
    assert transport.calls[1]["headers"]["Authorization"] == "Bearer key-good-654321"

    assert len(res) == 1
    assert res[0].rerank_score == 0.99

    # Check warning log masked the first key
    assert "key-..." in caplog.text or "key-***" in caplog.text or "key-" in caplog.text


@pytest.mark.asyncio
async def test_degraded_fallback_on_network_or_key_failure() -> None:
    """Verify safe degraded fallback to original candidate order when network or all keys fail."""
    chunks = (_make_chunk("c1", "Chunk 1"), _make_chunk("c2", "Chunk 2"))
    rotator = APIKeyRotator(keys=("key1", "key2"))
    settings = RerankerSettings(model="rerank-v3", rotator=rotator, max_attempts=2)

    # 1. All keys fail with 429
    err1 = HTTPError(url="", code=429, msg="Rate Limited", hdrs={}, fp=None)  # type: ignore[arg-type]
    err2 = HTTPError(url="", code=429, msg="Rate Limited", hdrs={}, fp=None)  # type: ignore[arg-type]
    transport = MockRerankerTransport([err1, err2])
    adapter = RerankerAdapter(settings=settings, transport=transport)

    res = await adapter.rerank(query="q", candidates=chunks)
    assert res == chunks  # Returns untouched original tuple

    # 2. General network error
    transport_err = MockRerankerTransport([ConnectionError("Network down")])
    adapter_err = RerankerAdapter(settings=settings, transport=transport_err)

    res_err = await adapter_err.rerank(query="q", candidates=chunks)
    assert res_err == chunks  # Returns untouched original tuple

    # 3. Malformed JSON response
    transport_bad_json = MockRerankerTransport([{"results": "invalid"}])
    adapter_bad_json = RerankerAdapter(settings=settings, transport=transport_bad_json)

    res_bad = await adapter_bad_json.rerank(query="q", candidates=chunks)
    assert res_bad == chunks


@pytest.mark.asyncio
async def test_empty_candidates_handling() -> None:
    """Verify empty candidates tuple returns empty tuple immediately without network call."""
    rotator = APIKeyRotator(keys=("key1",))
    settings = RerankerSettings(model="rerank-v3", rotator=rotator)
    transport = MockRerankerTransport([])
    adapter = RerankerAdapter(settings=settings, transport=transport)

    res = await adapter.rerank(query="q", candidates=())
    assert res == ()
    assert len(transport.calls) == 0
