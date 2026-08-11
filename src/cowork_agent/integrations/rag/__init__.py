"""Semantic Memory (RAG) adapters.

Retrieval-only company-knowledge access per PRD-v1 FR-08: corpus loading,
embeddings, and the Qdrant-backed vector store. The Memory Gateway of PRD-v2
is not scaffolded here.

``HybridSemanticMemory`` and ``InRepoSemanticMemory`` are deprecated and no
longer exported: they remain importable from their own modules for the
offline retrieval-evaluation harness only.
"""

from .chat_memory import SemanticChatMemoryAdapter
from .null_memory import NullSemanticMemory
from .qdrant import QdrantSemanticMemory, ingest_corpus

__all__ = [
    "NullSemanticMemory",
    "QdrantSemanticMemory",
    "SemanticChatMemoryAdapter",
    "ingest_corpus",
]
