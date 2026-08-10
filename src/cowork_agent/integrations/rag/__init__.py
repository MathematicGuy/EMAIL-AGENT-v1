"""In-repo Semantic Memory (RAG) adapters for the local MVP.

Retrieval-only company-knowledge access per PRD-v1 FR-08: corpus loading,
embeddings, and the in-process vector store. The Memory Gateway of PRD-v2
is not scaffolded here.
"""

from .hybrid import HybridSemanticMemory
from .memory import InRepoSemanticMemory
from .mmr import mmr_diversify
from .null_memory import NullSemanticMemory
from .query_transform import QueryTransformerPort, RuleBasedQueryTransformer

__all__ = [
    "HybridSemanticMemory",
    "InRepoSemanticMemory",
    "NullSemanticMemory",
    "QueryTransformerPort",
    "RuleBasedQueryTransformer",
    "mmr_diversify",
]
