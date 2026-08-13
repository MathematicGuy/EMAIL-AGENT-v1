"""Shared construction of the RAG store for both entry points.

The API process and the worker process each need the same corpus-backed
semantic memory, and each must degrade the same way: RETRIEVE_RAG candidates
fall back to structured empty retrieval (§12.3) when the corpus, the vector
store, or an upstream API is unavailable, so a missing index never blocks
digest runs.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from qdrant_client import AsyncQdrantClient

from cowork_agent.config import JinaEmbeddingSettings, QdrantSettings
from cowork_agent.features.email_action_plan.ports import SemanticMemoryPort
from cowork_agent.identity import LOCAL_TENANT_ID
from cowork_agent.integrations.rag.embeddings import EmbeddingPort, JinaEmbeddingAdapter
from cowork_agent.integrations.rag.hybrid import HybridSemanticMemory
from cowork_agent.integrations.rag.knowledge_base import KnowledgeDocument, load_corpus
from cowork_agent.integrations.rag.null_memory import NullSemanticMemory
from cowork_agent.integrations.rag.qdrant import QdrantSemanticMemory, ingest_corpus

logger = logging.getLogger(__name__)

#: Committed in-repo knowledge corpus (V1-M3), resolved from the package root.
RAG_CORPUS_PATH = Path(__file__).resolve().parents[4] / "data" / "extracted"


TURBOVEC_SNAPSHOT_PATH = Path(__file__).resolve().parents[4] / ".data" / "turbovec_index.tvim"

_DEFAULT_STORE_PROVIDER = "turbovec"
_DISABLED_STORE_PROVIDERS = frozenset({"none", "off", "null", "disabled"})


async def build_semantic_memory(
    settings: JinaEmbeddingSettings,
    qdrant_settings: QdrantSettings | None = None,
) -> SemanticMemoryPort:
    """Best-effort RAG store selected by RAG_STORE_PROVIDER (default: turbovec)."""
    provider = os.getenv("RAG_STORE_PROVIDER", _DEFAULT_STORE_PROVIDER).strip().lower()
    if not provider:
        provider = _DEFAULT_STORE_PROVIDER
    resolved = QdrantSettings.from_env() if qdrant_settings is None else qdrant_settings

    if provider in _DISABLED_STORE_PROVIDERS:
        logger.warning("RAG_STORE_PROVIDER=%s; returning NullSemanticMemory", provider)
        return NullSemanticMemory()
    if provider == "turbovec":
        return await _build_turbovec_memory(settings)
    if provider != "qdrant":
        logger.warning(
            "Unknown RAG_STORE_PROVIDER=%s; degrading to NullSemanticMemory",
            provider,
        )
        return NullSemanticMemory()
    if not resolved.enabled:
        logger.warning(
            "RAG_STORE_PROVIDER=qdrant but Qdrant is disabled; degrading to NullSemanticMemory"
        )
        return NullSemanticMemory()
    try:
        return await _build_qdrant_memory(resolved, JinaEmbeddingAdapter(settings))
    except Exception as exc:
        logger.error(
            "Qdrant memory error (%s: %s); degrading to NullSemanticMemory",
            type(exc).__name__,
            exc,
        )
        return NullSemanticMemory()


async def _build_turbovec_memory(settings: JinaEmbeddingSettings) -> SemanticMemoryPort:
    try:
        from cowork_agent.integrations.rag.turbovec_memory import TurbovecSemanticMemory

        documents = load_corpus(RAG_CORPUS_PATH, tenant_id=LOCAL_TENANT_ID)
        embedder = JinaEmbeddingAdapter(settings)
        turbovec_memory = TurbovecSemanticMemory(
            documents,
            embedder,
            bit_width=4,
            index_path=TURBOVEC_SNAPSHOT_PATH,
        )
        await turbovec_memory.build_index()
        logger.info(
            "Semantic memory backed by Turbovec 4-bit Index (%s)",
            TURBOVEC_SNAPSHOT_PATH,
        )
        return await _wrap_hybrid(documents, embedder, turbovec_memory)
    except Exception as exc:
        logger.error(
            "Turbovec memory setup failed (%s: %s); degrading to NullSemanticMemory",
            type(exc).__name__,
            exc,
        )
        return NullSemanticMemory()


async def _build_qdrant_memory(
    settings: QdrantSettings, embedder: EmbeddingPort
) -> SemanticMemoryPort:
    client = AsyncQdrantClient(url=settings.url, api_key=settings.api_key or None)
    documents = await _ensure_corpus(client, settings, embedder)
    logger.info("Semantic memory backed by Qdrant collection %s", settings.collection_name)
    dense = QdrantSemanticMemory(client, settings.collection_name, embedder)
    return await _wrap_hybrid(documents, embedder, dense)


async def _wrap_hybrid(
    documents: tuple[KnowledgeDocument, ...],
    embedder: EmbeddingPort,
    dense: SemanticMemoryPort,
) -> SemanticMemoryPort:
    hybrid = HybridSemanticMemory(documents, embedder, dense=dense)
    await hybrid.build_index()
    logger.info("Dense store wrapped with BM25 + RRF hybrid retrieval")
    return hybrid


async def _ensure_corpus(
    client: AsyncQdrantClient, settings: QdrantSettings, embedder: EmbeddingPort
) -> tuple[KnowledgeDocument, ...]:
    """Ensure corpus is ingested into Qdrant (supports incremental sync)."""
    documents = load_corpus(RAG_CORPUS_PATH, tenant_id=LOCAL_TENANT_ID)
    count = await ingest_corpus(
        client,
        settings.collection_name,
        documents,
        embedder,
        vector_size=settings.vector_size,
        reindex=settings.reindex,
    )
    logger.info(
        "Corpus check complete for Qdrant collection %s (total points: %d)",
        settings.collection_name,
        count,
    )
    return documents
