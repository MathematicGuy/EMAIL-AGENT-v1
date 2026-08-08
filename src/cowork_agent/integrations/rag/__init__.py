"""In-repo Semantic Memory (RAG) adapters for the local MVP.

Retrieval-only company-knowledge access per PRD-v1 FR-08: corpus loading,
embeddings, and the in-process vector store. The Memory Gateway of PRD-v2
is not scaffolded here.
"""

from .hybrid import HybridSemanticMemory
from .memory import InRepoSemanticMemory
from .null_memory import NullSemanticMemory

__all__ = ["HybridSemanticMemory", "InRepoSemanticMemory", "NullSemanticMemory"]
