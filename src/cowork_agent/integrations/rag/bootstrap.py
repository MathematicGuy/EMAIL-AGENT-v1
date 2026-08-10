"""Shared construction of the RAG store for both entry points.

The API process and the worker process each need the same corpus-backed
semantic memory, and each must degrade the same way: RETRIEVE_RAG candidates
fall back to structured empty retrieval (§12.3) when the corpus, the vector
store, or an upstream API is unavailable, so a missing index never blocks
digest runs.

Qdrant is the production store when ``QDRANT_URL``/``QDRANT_ENABLED`` are set
in the environment; otherwise the legacy in-repo hybrid store is used.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from qdrant_client import AsyncQdrantClient

from cowork_agent.config import GeminiSettings, QdrantSettings
from cowork_agent.features.email_action_plan.ports import SemanticMemoryPort
from cowork_agent.identity import LOCAL_TENANT_ID
from cowork_agent.integrations.rag.embeddings import EmbeddingPort, GeminiEmbeddingAdapter
from cowork_agent.integrations.rag.hybrid import HybridSemanticMemory
from cowork_agent.integrations.rag.jina_reranker import JinaRerankerAdapter
from cowork_agent.integrations.rag.knowledge_base import load_corpus
from cowork_agent.integrations.rag.null_memory import NullSemanticMemory
from cowork_agent.integrations.rag.qdrant import QdrantSemanticMemory, ingest_corpus

logger = logging.getLogger(__name__)

#: Committed in-repo knowledge corpus (V1-M3), resolved from the package root.
RAG_CORPUS_PATH = Path(__file__).resolve().parents[4] / "data" / "extracted"


async def build_semantic_memory(
    settings: GeminiSettings,
    qdrant_settings: QdrantSettings | None = None,
) -> SemanticMemoryPort:
    """Qdrant when configured, else the in-repo store; null on any failure.

    ``qdrant_settings`` defaults to the environment (``.env``), so neither
    entry point has to know where the URL and API key come from.
    """
    resolved = QdrantSettings.from_env() if qdrant_settings is None else qdrant_settings
    try:
        embedder = GeminiEmbeddingAdapter(settings)
        if resolved.enabled:
            return await _build_qdrant_memory(resolved, embedder)
        return await _build_in_repo_memory(embedder)
    except Exception as exc:
        # The message, not just the type: this degrade path is silent by
        # design, so the log line is the only evidence of *why* RAG is off.
        logger.warning(
            "Semantic memory unavailable (%s: %s); retrieval returns structured empty results",
            type(exc).__name__,
            exc,
        )
        return NullSemanticMemory()


async def _build_qdrant_memory(
    settings: QdrantSettings, embedder: EmbeddingPort
) -> SemanticMemoryPort:
    client = AsyncQdrantClient(url=settings.url, api_key=settings.api_key or None)
    await _ensure_corpus(client, settings, embedder)
    logger.info(
        "Semantic memory backed by Qdrant collection %s", settings.collection_name
    )
    return QdrantSemanticMemory(client, settings.collection_name, embedder)


async def _ensure_corpus(
    client: AsyncQdrantClient, settings: QdrantSettings, embedder: EmbeddingPort
) -> None:
    """Ingest only when the collection is absent, empty, or explicitly re-indexed.

    Both the API and the worker call this at startup; re-ingesting every time
    would re-embed the whole corpus on each boot and briefly empty a shared
    cloud collection out from under the other process.
    """
    if not settings.reindex and await client.collection_exists(settings.collection_name):
        if (await client.count(settings.collection_name)).count > 0:
            return
    documents = load_corpus(RAG_CORPUS_PATH, tenant_id=LOCAL_TENANT_ID)
    count = await ingest_corpus(
        client,
        settings.collection_name,
        documents,
        embedder,
        vector_size=settings.vector_size,
    )
    logger.info("Ingested %d knowledge chunks into %s", count, settings.collection_name)


async def _build_in_repo_memory(embedder: EmbeddingPort) -> SemanticMemoryPort:
    documents = load_corpus(RAG_CORPUS_PATH, tenant_id=LOCAL_TENANT_ID)
    memory = HybridSemanticMemory(
        documents,
        embedder,
        reranker=JinaRerankerAdapter(api_key=os.getenv("JINA_API_KEY")),
    )
    await memory.build_index()
    return memory
