"""Maximum Marginal Relevance (MMR) diversification for semantic chunks.

Implements MMR algorithm to reduce redundancy and increase diversity in retrieved chunks:
MMR = argmax_{d in R \\ S} [ lambda * Sim(d, q) - (1 - lambda) * max_{d_j in S} Sim(d, d_j) ]
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from cowork_agent.domain.target_contracts import SemanticChunk


def mmr_diversify(
    chunks: Sequence[SemanticChunk],
    chunk_vectors: Sequence[Sequence[float]] | Sequence[np.ndarray],
    query_vector: Sequence[float] | np.ndarray,
    top_k: int = 5,
    lambda_mult: float = 0.7,
) -> tuple[SemanticChunk, ...]:
    """Select top_k chunks using Maximum Marginal Relevance (MMR)."""
    if not chunks or top_k <= 0:
        return ()

    if len(chunks) <= top_k:
        return tuple(chunks)

    q_vec = np.asarray(query_vector, dtype=np.float32)
    q_norm = np.linalg.norm(q_vec)
    if q_norm > 0:
        q_vec = q_vec / q_norm

    doc_vecs = np.asarray(chunk_vectors, dtype=np.float32)
    norms = np.linalg.norm(doc_vecs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    doc_vecs = doc_vecs / norms

    # Cosine similarity to query
    query_sims = doc_vecs @ q_vec

    selected_indices: list[int] = []
    unselected_indices = list(range(len(chunks)))

    for _ in range(min(top_k, len(chunks))):
        best_score = -float("inf")
        best_idx = -1

        for idx in unselected_indices:
            sim_q = float(query_sims[idx])

            if not selected_indices:
                max_sim_doc = 0.0
            else:
                selected_vecs = doc_vecs[selected_indices]
                doc_sims = selected_vecs @ doc_vecs[idx]
                max_sim_doc = float(np.max(doc_sims))

            score = lambda_mult * sim_q - (1.0 - lambda_mult) * max_sim_doc
            if score > best_score:
                best_score = score
                best_idx = idx

        if best_idx != -1:
            selected_indices.append(best_idx)
            unselected_indices.remove(best_idx)

    return tuple(chunks[i] for i in selected_indices)
