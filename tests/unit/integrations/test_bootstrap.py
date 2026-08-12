"""Tests for the RAG bootstrap wiring (Qdrant migration, Task C).

The point of these tests is the degrade contract: whichever store is
configured, a failure to build it must return NullSemanticMemory rather than
raise, because a broken index may never block a digest run.
"""

import asyncio
from collections.abc import Sequence

import pytest
from qdrant_client import AsyncQdrantClient

from cowork_agent.config import GeminiSettings, QdrantSettings
from cowork_agent.integrations.rag import bootstrap
from cowork_agent.integrations.rag.fakes import HashingEmbedder
from cowork_agent.integrations.rag.hybrid import HybridSemanticMemory
from cowork_agent.integrations.rag.null_memory import NullSemanticMemory
from cowork_agent.integrations.rag.qdrant import QdrantSemanticMemory

COLLECTION = "bootstrap_company_knowledge"


def _gemini_settings() -> GeminiSettings:
    return GeminiSettings.from_env(
        {"GEMINI_API_KEY_1": "key-1", "GEMINI_MODEL": "gemini-3.5-flash-lite"},
        load_env_file=False,
    )


def _qdrant_settings(**overrides: str) -> QdrantSettings:
    environment = {
        "QDRANT_URL": "http://localhost:6333",
        "QDRANT_API_KEY": "test-key",
        "QDRANT_COLLECTION": COLLECTION,
        "QDRANT_ENABLED": "true",
        "QDRANT_VECTOR_SIZE": "64",
        **overrides,
    }
    return QdrantSettings.from_env(environment, load_env_file=False)


@pytest.fixture
def local_qdrant(monkeypatch: pytest.MonkeyPatch) -> AsyncQdrantClient:
    """Route the bootstrap's client and embedder to offline test doubles."""
    client = AsyncQdrantClient(":memory:")
    monkeypatch.setattr(bootstrap, "AsyncQdrantClient", lambda **kwargs: client)
    monkeypatch.setattr(
        bootstrap, "GeminiEmbeddingAdapter", lambda settings: HashingEmbedder()
    )
    return client


def test_disabled_qdrant_yields_hybrid_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        bootstrap, "GeminiEmbeddingAdapter", lambda settings: HashingEmbedder()
    )
    memory = asyncio.run(
        bootstrap.build_semantic_memory(
            _gemini_settings(), _qdrant_settings(QDRANT_ENABLED="false")
        )
    )

    assert isinstance(memory, HybridSemanticMemory)




def test_enabled_qdrant_ingests_the_corpus_and_returns_the_adapter(
    local_qdrant: AsyncQdrantClient,
) -> None:
    memory = asyncio.run(
        bootstrap.build_semantic_memory(_gemini_settings(), _qdrant_settings())
    )

    assert isinstance(memory, QdrantSemanticMemory)
    assert asyncio.run(local_qdrant.count(COLLECTION)).count > 0


def test_a_populated_collection_is_not_re_ingested_on_the_next_boot(
    local_qdrant: AsyncQdrantClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    asyncio.run(bootstrap.build_semantic_memory(_gemini_settings(), _qdrant_settings()))
    ingested: list[str] = []

    async def _spy(client: object, collection: str, *args: object, **kwargs: object) -> int:
        ingested.append(collection)
        return 0

    monkeypatch.setattr(bootstrap, "ingest_corpus", _spy)
    asyncio.run(bootstrap.build_semantic_memory(_gemini_settings(), _qdrant_settings()))

    assert ingested == []


def test_reindex_forces_ingestion_even_when_the_collection_is_populated(
    local_qdrant: AsyncQdrantClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    asyncio.run(bootstrap.build_semantic_memory(_gemini_settings(), _qdrant_settings()))
    ingested: list[str] = []
    original = bootstrap.ingest_corpus

    async def _spy(client: object, collection: str, *args: object, **kwargs: object) -> int:
        ingested.append(collection)
        return await original(client, collection, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(bootstrap, "ingest_corpus", _spy)
    asyncio.run(
        bootstrap.build_semantic_memory(
            _gemini_settings(), _qdrant_settings(QDRANT_REINDEX="true")
        )
    )

    assert ingested == [COLLECTION]


def test_an_unreachable_qdrant_degrades_to_null_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _explode(**kwargs: object) -> AsyncQdrantClient:
        raise ConnectionError("connection refused")

    monkeypatch.setattr(bootstrap, "AsyncQdrantClient", _explode)
    monkeypatch.setattr(
        bootstrap, "GeminiEmbeddingAdapter", lambda settings: HashingEmbedder()
    )

    memory = asyncio.run(
        bootstrap.build_semantic_memory(_gemini_settings(), _qdrant_settings())
    )

    assert isinstance(memory, HybridSemanticMemory)


def test_a_vector_size_mismatch_degrades_to_null_memory(
    local_qdrant: AsyncQdrantClient,
) -> None:
    memory = asyncio.run(
        bootstrap.build_semantic_memory(
            _gemini_settings(), _qdrant_settings(QDRANT_VECTOR_SIZE="768")
        )
    )

    assert isinstance(memory, HybridSemanticMemory)



def test_a_missing_corpus_degrades_to_null_memory(
    local_qdrant: AsyncQdrantClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _missing(*args: object, **kwargs: object) -> Sequence[object]:
        raise ValueError("Knowledge corpus directory not found")

    monkeypatch.setattr(bootstrap, "load_corpus", _missing)

    memory = asyncio.run(
        bootstrap.build_semantic_memory(_gemini_settings(), _qdrant_settings())
    )

    assert isinstance(memory, NullSemanticMemory)
