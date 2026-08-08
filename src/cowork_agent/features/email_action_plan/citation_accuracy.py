"""Pure inspection of plan-step citation overlap with retrieved chunks."""

from dataclasses import dataclass

from cowork_agent.domain.target_contracts import (
    SemanticRetrievalResponse,
    Task,
)


@dataclass(frozen=True, slots=True)
class CitationOverlap:
    """One plan-step citation checked against the retrieval response."""

    step: int
    citation_id: str
    chunk_found: bool
    overlap_score: float
    instruction_preview: str
    chunk_preview: str


@dataclass(frozen=True, slots=True)
class CitationAccuracyReport:
    """Aggregate citation-overlap metrics for one generated task."""

    task_id: str
    total_citations: int
    found_count: int
    missing_count: int
    mean_overlap: float
    overlaps: tuple[CitationOverlap, ...]


def _word_set(text: str) -> frozenset[str]:
    return frozenset(text.lower().split())


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    union = a | b
    if not union:
        return 0.0
    return round(len(a & b) / len(union), 4)


def _preview(text: str, length: int = 80) -> str:
    return text.replace("\n", " ")[:length]


def inspect_citation_accuracy(
    task: Task,
    retrieval: SemanticRetrievalResponse | None,
) -> CitationAccuracyReport:
    """Report token overlap for every plan-step citation without mutating ``task``."""

    chunks_by_id = (
        {} if retrieval is None else {chunk.chunk_id: chunk for chunk in retrieval.chunks}
    )
    overlaps: list[CitationOverlap] = []
    found_scores: list[float] = []

    for step in task.action_plan:
        instruction_words = _word_set(step.instruction)
        for citation_id in step.supporting_citation_ids:
            chunk = chunks_by_id.get(citation_id)
            if chunk is None:
                overlaps.append(
                    CitationOverlap(
                        step=step.step,
                        citation_id=citation_id,
                        chunk_found=False,
                        overlap_score=0.0,
                        instruction_preview=_preview(step.instruction),
                        chunk_preview="",
                    )
                )
                continue

            overlap_score = _jaccard(instruction_words, _word_set(chunk.text))
            found_scores.append(overlap_score)
            overlaps.append(
                CitationOverlap(
                    step=step.step,
                    citation_id=citation_id,
                    chunk_found=True,
                    overlap_score=overlap_score,
                    instruction_preview=_preview(step.instruction),
                    chunk_preview=_preview(chunk.text),
                )
            )

    found_count = len(found_scores)
    total_citations = len(overlaps)
    return CitationAccuracyReport(
        task_id=task.task_id,
        total_citations=total_citations,
        found_count=found_count,
        missing_count=total_citations - found_count,
        mean_overlap=round(sum(found_scores) / found_count, 4) if found_scores else 0.0,
        overlaps=tuple(overlaps),
    )
