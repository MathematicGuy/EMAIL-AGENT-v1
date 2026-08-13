"""Key rotation for the embedding path (mirrors the generator's behaviour)."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import pytest
from google.genai import errors

from cowork_agent.config import GeminiEmbeddingSettings, GeminiSettings, JinaEmbeddingSettings
from cowork_agent.integrations.rag.embeddings import (
    GeminiEmbeddingAdapter,
    JinaEmbeddingAdapter,
)
from cowork_agent.integrations.rag.fakes import HashingEmbedder


def _settings(*, rotate: bool = True) -> GeminiSettings:
    return GeminiSettings(
        api_keys=("key-1", "key-2", "key-3"),
        model="gemini-3.5-flash-lite",
        rotate_on_rate_limit=rotate,
        max_attempts=3,
        max_emails_per_batch=5,
        max_input_tokens=1000,
        timeout_seconds=30,
    )


def _quota_error() -> errors.APIError:
    return errors.APIError(429, {"error": {"message": "RESOURCE_EXHAUSTED"}})


class _FakeClient:
    """Stands in for genai.Client: fails for every key but the last."""

    def __init__(self, api_key: str, *, working_key: str, attempted: list[str]) -> None:
        attempted.append(api_key)
        self._ok = api_key == working_key

    @property
    def aio(self) -> _FakeClient:
        return self

    @property
    def models(self) -> _FakeClient:
        return self

    async def embed_content(self, *, model: str, contents: list[str]) -> Any:
        del model
        if not self._ok:
            raise _quota_error()
        return type(
            "Resp",
            (),
            {"embeddings": [type("E", (), {"values": [0.1, 0.2]})() for _ in contents]},
        )()


class _RecordingClient:
    def __init__(self) -> None:
        self.batches: list[list[str]] = []

    @property
    def aio(self) -> _RecordingClient:
        return self

    @property
    def models(self) -> _RecordingClient:
        return self

    async def embed_content(self, *, model: str, contents: list[str]) -> Any:
        del model
        self.batches.append(contents)
        return type(
            "Resp",
            (),
            {"embeddings": [type("E", (), {"values": [float(index)]})() for index in contents]},
        )()


def _patch(monkeypatch: pytest.MonkeyPatch, working_key: str) -> list[str]:
    attempted: list[str] = []
    monkeypatch.setattr(
        "cowork_agent.integrations.rag.embeddings.genai.Client",
        lambda *, api_key: _FakeClient(api_key, working_key=working_key, attempted=attempted),
    )
    return attempted


def test_embed_rotates_past_an_exhausted_key(monkeypatch: pytest.MonkeyPatch) -> None:
    attempted = _patch(monkeypatch, "key-3")
    adapter = GeminiEmbeddingAdapter(_settings())
    vectors = asyncio.run(adapter.embed(["xin chào"]))
    assert vectors == ((0.1, 0.2),)
    # Every key is tried until one succeeds: a single dead key must not take
    # the whole index down (the bug this covers degraded RAG to empty results).
    assert attempted == ["key-1", "key-2", "key-3"]


def test_embed_raises_when_every_key_is_exhausted(monkeypatch: pytest.MonkeyPatch) -> None:
    attempted = _patch(monkeypatch, "none-of-them")
    adapter = GeminiEmbeddingAdapter(_settings())
    with pytest.raises(Exception, match="quota exhausted"):
        asyncio.run(adapter.embed(["xin chào"]))
    assert len(attempted) == 3


def test_embed_does_not_rotate_when_rotation_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempted = _patch(monkeypatch, "key-3")
    adapter = GeminiEmbeddingAdapter(_settings(rotate=False))
    with pytest.raises(Exception, match="quota exhausted"):
        asyncio.run(adapter.embed(["xin chào"]))
    assert attempted == ["key-1"]


def test_embed_splits_more_than_one_hundred_contents_and_preserves_order() -> None:
    client = _RecordingClient()
    adapter = GeminiEmbeddingAdapter(_settings(), client=client)  # type: ignore[arg-type]

    vectors = asyncio.run(adapter.embed([str(index) for index in range(101)]))

    assert [len(batch) for batch in client.batches] == [100, 1]
    assert vectors == tuple((float(index),) for index in range(101))


class _ProjectEmbeddingClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    @property
    def aio(self) -> _ProjectEmbeddingClient:
        return self

    @property
    def models(self) -> _ProjectEmbeddingClient:
        return self

    async def embed_content(self, **kwargs: object) -> Any:
        self.calls.append(kwargs)
        contents = kwargs["contents"]
        assert isinstance(contents, list)
        return type(
            "Resp",
            (),
            {
                "embeddings": [
                    type("E", (), {"values": [0.1] * 3072})() for _ in contents
                ]
            },
        )()


def test_project_embedding_uses_gemini_2_dimensions_and_document_task() -> None:
    settings = GeminiEmbeddingSettings(
        api_keys=("key-1",),
        model="gemini-embedding-2",
        dimensions=3072,
        timeout_seconds=30,
        batch_size=100,
        rotate_on_rate_limit=True,
        max_attempts=1,
    )
    client = _ProjectEmbeddingClient()
    vectors = asyncio.run(
        GeminiEmbeddingAdapter(settings, client=client).embed(
            ["first document chunk", "second document chunk"], task="retrieval.passage"
        )
    )

    assert len(vectors[0]) == 3072
    assert len(vectors) == 2
    assert [call["contents"] for call in client.calls] == [
        ["first document chunk"],
        ["second document chunk"],
    ]
    assert client.calls[0]["model"] == "gemini-embedding-2"
    config = client.calls[0]["config"]
    assert config.output_dimensionality == 3072
    assert str(config.task_type).endswith("RETRIEVAL_DOCUMENT")


def test_jina_embedding_settings_default_to_v5_omni_small() -> None:
    settings = JinaEmbeddingSettings.from_env(
        {"JINA_API_KEY": "test-key"}, load_env_file=False
    )

    assert settings.model == "jina-embeddings-v5-omni-small"
    assert settings.dimensions == 1024


def test_hashing_embedder_accepts_retrieval_task() -> None:
    vectors = asyncio.run(
        HashingEmbedder().embed(["text"], task="retrieval.passage")
    )

    assert len(vectors) == 1


class _RecordingJinaTransport:
    def __init__(self, response: Mapping[str, object]) -> None:
        self.response = response
        self.url = ""
        self.headers: Mapping[str, str] = {}
        self.payload: Mapping[str, object] = {}
        self.timeout_seconds = 0.0

    async def post_json(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout_seconds: float,
    ) -> Mapping[str, object]:
        self.url = url
        self.headers = headers
        self.payload = payload
        self.timeout_seconds = timeout_seconds
        return self.response


def _jina_settings() -> JinaEmbeddingSettings:
    return JinaEmbeddingSettings.from_env(
        {"JINA_API_KEY": "test-key"}, load_env_file=False
    )


def test_jina_adapter_posts_v5_model_and_passage_task() -> None:
    transport = _RecordingJinaTransport(
        {"data": [{"index": 0, "embedding": [0.1] * 1024}]}
    )
    adapter = JinaEmbeddingAdapter(_jina_settings(), transport=transport)

    vectors = asyncio.run(adapter.embed(["policy"], task="retrieval.passage"))

    assert vectors == ((0.1,) * 1024,)
    assert transport.url == "https://api.jina.ai/v1/embeddings"
    assert transport.headers["Authorization"] == "Bearer test-key"
    assert transport.payload == {
        "model": "jina-embeddings-v5-omni-small",
        "input": ["policy"],
        "task": "retrieval.passage",
        "dimensions": 1024,
        "embedding_type": "float",
    }


def test_jina_adapter_rejects_response_with_wrong_vector_dimension() -> None:
    transport = _RecordingJinaTransport(
        {"data": [{"index": 0, "embedding": [0.1]}]}
    )
    adapter = JinaEmbeddingAdapter(_jina_settings(), transport=transport)

    with pytest.raises(ValueError, match="dimension"):
        asyncio.run(adapter.embed(["policy"]))
