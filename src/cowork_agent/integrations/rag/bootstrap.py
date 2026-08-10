"""Shared construction of the in-repo RAG store for both entry points.

The API process and the worker process each need the same corpus-backed
semantic memory, and each must degrade the same way: RETRIEVE_RAG candidates
fall back to structured empty retrieval (§12.3) when the corpus or an upstream
API is unavailable, so a missing index never blocks digest runs.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from cowork_agent.config import GeminiSettings
from cowork_agent.features.email_action_plan.ports import SemanticMemoryPort
from cowork_agent.identity import LOCAL_TENANT_ID
from cowork_agent.integrations.rag.embeddings import GeminiEmbeddingAdapter
from cowork_agent.integrations.rag.hybrid import HybridSemanticMemory
from cowork_agent.integrations.rag.jina_reranker import JinaRerankerAdapter
from cowork_agent.integrations.rag.knowledge_base import load_corpus
from cowork_agent.integrations.rag.null_memory import NullSemanticMemory
from cowork_agent.integrations.rag.query_transform import RuleBasedQueryTransformer

logger = logging.getLogger(__name__)

#: Committed in-repo knowledge corpus (V1-M3), resolved from the package root.
RAG_CORPUS_PATH = Path(__file__).resolve().parents[4] / "data" / "extracted"


async def build_semantic_memory(settings: GeminiSettings) -> SemanticMemoryPort:
    """Best-effort in-repo RAG store; null memory on any setup failure."""
    try:
        documents = load_corpus(RAG_CORPUS_PATH, tenant_id=LOCAL_TENANT_ID)
        memory = HybridSemanticMemory(
            documents,
            GeminiEmbeddingAdapter(settings),
            reranker=JinaRerankerAdapter(api_key=os.getenv("JINA_API_KEY")),
            query_transformer=RuleBasedQueryTransformer(enable_hyde=True),
            enable_mmr=True,
        )
        await memory.build_index()
        return memory
    except Exception as exc:
        # The message, not just the type: this degrade path is silent by
        # design, so the log line is the only evidence of *why* RAG is off.
        logger.warning(
            "Semantic memory unavailable (%s: %s); retrieval returns structured empty results",
            type(exc).__name__,
            exc,
        )
        return NullSemanticMemory()
