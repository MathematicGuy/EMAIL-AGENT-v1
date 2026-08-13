"""End-to-end Qdrant retrieval over the committed corpus (Qdrant migration, Task D).

Runs against ``AsyncQdrantClient(":memory:")`` — the real Qdrant query engine,
no server and no credentials. The offline embedder is ``HashingEmbedder``,
which buckets tokens by hash and carries no semantics, so nothing here asserts
*which* document ranks first; the claims are about the ACL boundary, the score
threshold, the limit, and the degrade path.
"""

import asyncio
from pathlib import Path

import pytest
from qdrant_client import AsyncQdrantClient

from cowork_agent.config import JinaEmbeddingSettings, QdrantSettings
from cowork_agent.domain.target_contracts import (
    RetrievalFilters,
    RetrievalLimits,
    RetrievalStatus,
    SemanticRetrievalRequest,
)
from cowork_agent.integrations.rag import bootstrap
from cowork_agent.integrations.rag.fakes import HashingEmbedder
from cowork_agent.integrations.rag.hybrid import HybridSemanticMemory
from cowork_agent.integrations.rag.knowledge_base import load_corpus
from cowork_agent.integrations.rag.null_memory import NullSemanticMemory
from cowork_agent.integrations.rag.qdrant import QdrantSemanticMemory, ingest_corpus

REPO_ROOT = Path(__file__).resolve().parents[2]
CORPUS_DIR = REPO_ROOT / "data" / "extracted"
COLLECTION = "integration_company_knowledge"
OWNER_TENANT = "tenant-owner"
FOREIGN_TENANT = "tenant-intruder"


def _request(
    *,
    tenant_id: str = OWNER_TENANT,
    tenant_scope: str = OWNER_TENANT,
    query: str = "hồ sơ đăng ký kết hôn cần giấy tờ gì",
    top_k: int = 5,
    min_score: float = 0.0,
) -> SemanticRetrievalRequest:
    return SemanticRetrievalRequest(
        run_id="run-integration",
        tenant_id=tenant_id,
        user_id="user@example.com",
        query=query,
        knowledge_gaps=(),
        filters=RetrievalFilters(tenant_scope=tenant_scope, document_status=("ready",)),
        limits=RetrievalLimits(top_k=top_k, min_score=min_score, timeout_ms=1500),
    )


@pytest.fixture(scope="module")
def memory() -> QdrantSemanticMemory:
    """One ingested in-memory collection shared by the read-only assertions."""

    async def build() -> QdrantSemanticMemory:
        client = AsyncQdrantClient(":memory:")
        documents = load_corpus(CORPUS_DIR, tenant_id=OWNER_TENANT)
        await ingest_corpus(client, COLLECTION, documents, HashingEmbedder())
        return QdrantSemanticMemory(client, COLLECTION, HashingEmbedder())

    return asyncio.run(build())


def test_retrieval_returns_citable_chunks_from_the_committed_corpus(
    memory: QdrantSemanticMemory,
) -> None:
    response = asyncio.run(memory.retrieve(_request()))

    assert response.retrieval_status is RetrievalStatus.SUCCESS
    assert response.chunks
    known_documents = {
        document.document_id
        for document in load_corpus(CORPUS_DIR, tenant_id=OWNER_TENANT)
    }
    for chunk in response.chunks:
        assert chunk.document_id in known_documents
        assert chunk.chunk_id.startswith(chunk.document_id)
        assert chunk.source_url.startswith("data/extracted/")
        assert chunk.text.strip()


def test_a_foreign_tenant_retrieves_nothing_from_the_same_collection(
    memory: QdrantSemanticMemory,
) -> None:
    response = asyncio.run(
        memory.retrieve(
            _request(tenant_id=FOREIGN_TENANT, tenant_scope=FOREIGN_TENANT)
        )
    )

    assert response.retrieval_status is RetrievalStatus.NO_RESULTS
    assert response.chunks == ()


def test_min_score_excludes_everything_below_the_threshold(
    memory: QdrantSemanticMemory,
) -> None:
    baseline = asyncio.run(memory.retrieve(_request(top_k=25, min_score=0.0)))
    assert len(baseline.chunks) > 1
    threshold = sorted(chunk.relevance_score for chunk in baseline.chunks)[-1]

    filtered = asyncio.run(memory.retrieve(_request(top_k=25, min_score=threshold)))

    assert filtered.chunks
    assert len(filtered.chunks) < len(baseline.chunks)
    assert all(chunk.relevance_score >= threshold for chunk in filtered.chunks)


def test_an_unreachable_score_threshold_yields_no_results(
    memory: QdrantSemanticMemory,
) -> None:
    response = asyncio.run(memory.retrieve(_request(min_score=1.01)))

    assert response.retrieval_status is RetrievalStatus.NO_RESULTS
    assert response.chunks == ()


@pytest.mark.parametrize("top_k", [1, 3, 7])
def test_top_k_bounds_the_returned_chunk_count(
    memory: QdrantSemanticMemory, top_k: int
) -> None:
    response = asyncio.run(memory.retrieve(_request(top_k=top_k)))

    assert 0 < len(response.chunks) <= top_k


def test_results_are_ordered_by_descending_relevance(
    memory: QdrantSemanticMemory,
) -> None:
    scores = [chunk.relevance_score for chunk in asyncio.run(memory.retrieve(_request())).chunks]

    assert scores == sorted(scores, reverse=True)


def test_an_unreachable_qdrant_falls_back_to_in_repo_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        bootstrap, "JinaEmbeddingAdapter", lambda settings: HashingEmbedder()
    )
    jina = JinaEmbeddingSettings.from_env(
        {"JINA_API_KEY": "test-key"}, load_env_file=False
    )
    # Port 1 is reserved and never listening: a real connection attempt, not a
    # patched-out one, is what proves the degrade path.
    qdrant = QdrantSettings.from_env(
        {
            "QDRANT_URL": "http://127.0.0.1:1",
            "QDRANT_ENABLED": "true",
            "QDRANT_COLLECTION": COLLECTION,
            "QDRANT_VECTOR_SIZE": "64",
        },
        load_env_file=False,
    )

    result = asyncio.run(bootstrap.build_semantic_memory(jina, qdrant))

    assert isinstance(result, HybridSemanticMemory)
    assert asyncio.run(result.retrieve(_request())).retrieval_status is (
        RetrievalStatus.NO_RESULTS
    )


def test_bootstrap_uses_jina_settings_without_gemini_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bootstrap, "JinaEmbeddingAdapter", lambda settings: HashingEmbedder())
    jina = JinaEmbeddingSettings.from_env(
        {"JINA_API_KEY": "test-key"}, load_env_file=False
    )
    qdrant = QdrantSettings.from_env(
        {"QDRANT_ENABLED": "false"}, load_env_file=False
    )

    result = asyncio.run(bootstrap.build_semantic_memory(jina, qdrant))

    assert not isinstance(result, NullSemanticMemory)
