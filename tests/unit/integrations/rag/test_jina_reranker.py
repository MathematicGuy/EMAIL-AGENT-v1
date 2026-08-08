"""Offline unit tests for the Jina reranker adapter boundary."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping

from cowork_agent.domain.target_contracts import SemanticChunk
from cowork_agent.integrations.rag.jina_reranker import FakeJinaReranker, JinaRerankerAdapter


def _chunk(chunk_id: str, relevance_score: float) -> SemanticChunk:
    return SemanticChunk(
        chunk_id=chunk_id,
        document_id=f"document-{chunk_id}",
        document_title=f"Document {chunk_id}",
        section=None,
        text=f"Text for {chunk_id}",
        source_url=f"https://example.test/{chunk_id}",
        document_version=None,
        relevance_score=relevance_score,
        rerank_score=None,
    )


class RecordingTransport:
    def __init__(self, response: Mapping[str, object] | Exception) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    async def post_json(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout_seconds: float,
    ) -> Mapping[str, object]:
        self.calls.append(
            {
                "url": url,
                "headers": dict(headers),
                "payload": dict(payload),
                "timeout_seconds": timeout_seconds,
            }
        )
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def test_jina_adapter_maps_valid_scores_and_response_order() -> None:
    candidates = (_chunk("first", 0.7), _chunk("second", 0.6))
    transport = RecordingTransport(
        {
            "results": [
                {"index": 1, "relevance_score": 0.98},
                {"index": 0, "relevance_score": 0.83},
            ]
        }
    )
    adapter = JinaRerankerAdapter(api_key="test-key", transport=transport, timeout_seconds=3.5)

    reranked = asyncio.run(adapter.rerank(query="find procedure", candidates=candidates))

    assert [chunk.chunk_id for chunk in reranked] == ["second", "first"]
    assert [chunk.rerank_score for chunk in reranked] == [0.98, 0.83]
    assert [chunk.relevance_score for chunk in reranked] == [0.6, 0.7]
    assert transport.calls == [
        {
            "url": "https://api.jina.ai/v1/rerank",
            "headers": {
                "Authorization": "Bearer test-key",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            "payload": {
                "model": "jina-reranker-v2-base-multilingual",
                "query": "find procedure",
                "documents": ["Text for first", "Text for second"],
                "return_documents": False,
            },
            "timeout_seconds": 3.5,
        }
    ]


def test_jina_adapter_without_api_key_returns_candidates_without_calling_transport() -> None:
    candidates = (_chunk("first", 0.7),)
    transport = RecordingTransport({"results": []})
    adapter = JinaRerankerAdapter(api_key=None, transport=transport)

    reranked = asyncio.run(adapter.rerank(query="find procedure", candidates=candidates))

    assert reranked == candidates
    assert transport.calls == []


def test_jina_adapter_timeout_returns_original_candidates_without_scores() -> None:
    candidates = (_chunk("first", 0.7), _chunk("second", 0.6))
    transport = RecordingTransport(TimeoutError("request timed out"))
    adapter = JinaRerankerAdapter(api_key="test-key", transport=transport)

    reranked = asyncio.run(adapter.rerank(query="find procedure", candidates=candidates))

    assert reranked == candidates
    assert all(chunk.rerank_score is None for chunk in reranked)


def test_jina_adapter_malformed_response_returns_original_candidates_without_scores() -> None:
    candidates = (_chunk("first", 0.7), _chunk("second", 0.6))
    transport = RecordingTransport({"results": [{"index": 3, "relevance_score": 0.98}]})
    adapter = JinaRerankerAdapter(api_key="test-key", transport=transport)

    reranked = asyncio.run(adapter.rerank(query="find procedure", candidates=candidates))

    assert reranked == candidates
    assert all(chunk.rerank_score is None for chunk in reranked)


def test_fake_jina_reranker_is_deterministic() -> None:
    candidates = (_chunk("first", 0.7), _chunk("second", 0.6))
    reranker = FakeJinaReranker(scores={"first": 0.2, "second": 0.9})

    first_result = asyncio.run(reranker.rerank(query="one query", candidates=candidates))
    second_result = asyncio.run(reranker.rerank(query="another query", candidates=candidates))

    assert first_result == second_result
    assert [chunk.chunk_id for chunk in first_result] == ["second", "first"]
    assert [chunk.rerank_score for chunk in first_result] == [0.9, 0.2]
