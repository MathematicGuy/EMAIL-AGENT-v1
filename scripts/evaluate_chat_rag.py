"""Evaluate Chat-RAG evidence without persisting chat or document content.

The input dataset is local-only. Its cases may contain question, answer,
contexts, and reference_answer for an optional RAGAS pass, but those fields
are intentionally omitted from the committed report. Every run also computes
deterministic document-ID, citation-link, abstention, and stage-latency
metrics, so an agent can validate the integration without an evaluator model.

Example:
    python scripts/evaluate_chat_rag.py --input C:\\temp\\chat-rag.json
    python scripts/evaluate_chat_rag.py --input C:\\temp\\chat-rag.json --ragas
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = REPO_ROOT / "evaluations" / "CHAT-RAGAS" / "baselines"
SCHEMA_VERSION = "chat-rag-eval.v1"


@dataclass(frozen=True)
class ChatRagCase:
    """One local-only evaluation case; report serialization stays metadata-only."""

    case_id: str
    expected_document_ids: tuple[str, ...]
    retrieved_document_ids: tuple[str, ...]
    citation_document_ids: tuple[str, ...]
    should_abstain: bool | None
    abstained: bool | None
    retrieval_latency_ms: int | None
    generation_latency_ms: int | None
    evaluator_latency_ms: int | None
    raw_ragas_record: dict[str, Any] | None


def _string_tuple(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ValueError(f"{field} must be a list of non-empty strings")
    return tuple(value)


def _optional_bool(value: object, field: str) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be true, false, or null")
    return value


def _optional_latency(value: object, field: str) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer or null")
    return value


def parse_cases(payload: Mapping[str, object]) -> tuple[ChatRagCase, ...]:
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("cases must be a non-empty list")
    cases: list[ChatRagCase] = []
    seen_ids: set[str] = set()
    for raw_case in raw_cases:
        if not isinstance(raw_case, dict):
            raise ValueError("each case must be an object")
        case_id = raw_case.get("id")
        if not isinstance(case_id, str) or not case_id:
            raise ValueError("case id must be a non-empty string")
        if case_id in seen_ids:
            raise ValueError(f"duplicate case id: {case_id}")
        seen_ids.add(case_id)
        latency = raw_case.get("latency_ms", {})
        if not isinstance(latency, dict):
            raise ValueError("latency_ms must be an object")
        ragas_fields = ("question", "answer", "contexts", "reference_answer")
        has_ragas_fields = any(field in raw_case for field in ragas_fields)
        raw_ragas_record = None
        if has_ragas_fields:
            question = raw_case.get("question")
            answer = raw_case.get("answer")
            contexts = raw_case.get("contexts")
            reference_answer = raw_case.get("reference_answer")
            if (
                not isinstance(question, str)
                or not isinstance(answer, str)
                or not isinstance(reference_answer, str)
                or not isinstance(contexts, list)
                or not all(isinstance(context, str) for context in contexts)
            ):
                raise ValueError(
                    "RAGAS cases require string question, answer, reference_answer, and contexts"
                )
            raw_ragas_record = {
                "question": question,
                "answer": answer,
                "contexts": contexts,
                "ground_truth": reference_answer,
            }
        cases.append(
            ChatRagCase(
                case_id=case_id,
                expected_document_ids=_string_tuple(
                    raw_case.get("expected_document_ids", []), "expected_document_ids"
                ),
                retrieved_document_ids=_string_tuple(
                    raw_case.get("retrieved_document_ids", []), "retrieved_document_ids"
                ),
                citation_document_ids=_string_tuple(
                    raw_case.get("citation_document_ids", []), "citation_document_ids"
                ),
                should_abstain=_optional_bool(raw_case.get("should_abstain"), "should_abstain"),
                abstained=_optional_bool(raw_case.get("abstained"), "abstained"),
                retrieval_latency_ms=_optional_latency(
                    latency.get("retrieval"), "latency_ms.retrieval"
                ),
                generation_latency_ms=_optional_latency(
                    latency.get("generation"), "latency_ms.generation"
                ),
                evaluator_latency_ms=_optional_latency(
                    latency.get("evaluator"), "latency_ms.evaluator"
                ),
                raw_ragas_record=raw_ragas_record,
            )
        )
    return tuple(cases)


def _percentile(values: Iterable[int | None], percentile: int) -> int | None:
    ordered = sorted(value for value in values if value is not None)
    if not ordered:
        return None
    index = max(0, min(len(ordered) - 1, round((len(ordered) - 1) * percentile / 100)))
    return ordered[index]


def _mean(values: Iterable[float]) -> float | None:
    materialized = tuple(values)
    return None if not materialized else round(mean(materialized), 4)


@dataclass(frozen=True)
class CaseMetrics:
    """Deterministic per-case metrics; every field is metadata, never content."""

    case_id: str
    expected_document_count: int
    retrieved_document_count: int
    citation_document_count: int
    hit_at_1: bool | None
    hit_at_5: bool | None
    reciprocal_rank: float
    recall_at_5: float | None
    citation_id_valid: bool
    should_abstain: bool | None
    abstained: bool | None
    retrieval_latency_ms: int | None
    generation_latency_ms: int | None
    evaluator_latency_ms: int | None

    def latency(self, stage: str) -> int | None:
        return {
            "retrieval": self.retrieval_latency_ms,
            "generation": self.generation_latency_ms,
            "evaluator": self.evaluator_latency_ms,
        }[stage]

    def to_report(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "expected_document_count": self.expected_document_count,
            "retrieved_document_count": self.retrieved_document_count,
            "citation_document_count": self.citation_document_count,
            "hit_at_1": self.hit_at_1,
            "hit_at_5": self.hit_at_5,
            "reciprocal_rank": self.reciprocal_rank,
            "recall_at_5": self.recall_at_5,
            "citation_id_valid": self.citation_id_valid,
            "should_abstain": self.should_abstain,
            "abstained": self.abstained,
            "latency_ms": {
                "retrieval": self.retrieval_latency_ms,
                "generation": self.generation_latency_ms,
                "evaluator": self.evaluator_latency_ms,
            },
        }


def _case_metrics(case: ChatRagCase) -> CaseMetrics:
    expected = set(case.expected_document_ids)
    retrieved = case.retrieved_document_ids
    first_rank = next(
        (index for index, document_id in enumerate(retrieved, start=1) if document_id in expected),
        None,
    )
    cited = set(case.citation_document_ids)
    return CaseMetrics(
        case_id=case.case_id,
        expected_document_count=len(expected),
        retrieved_document_count=len(retrieved),
        citation_document_count=len(cited),
        hit_at_1=first_rank == 1 if expected else None,
        hit_at_5=first_rank is not None and first_rank <= 5 if expected else None,
        reciprocal_rank=round(1 / first_rank, 4) if first_rank else 0.0,
        recall_at_5=(
            round(len(expected.intersection(retrieved[:5])) / len(expected), 4)
            if expected
            else None
        ),
        citation_id_valid=cited.issubset(set(retrieved)),
        should_abstain=case.should_abstain,
        abstained=case.abstained,
        retrieval_latency_ms=case.retrieval_latency_ms,
        generation_latency_ms=case.generation_latency_ms,
        evaluator_latency_ms=case.evaluator_latency_ms,
    )


def compute_report(
    payload: Mapping[str, object], cases: Sequence[ChatRagCase]
) -> dict[str, object]:
    case_metrics = tuple(_case_metrics(case) for case in cases)
    labeled = tuple(metric for metric in case_metrics if metric.expected_document_count)
    abstention_labeled = tuple(
        metric
        for metric in case_metrics
        if metric.should_abstain is not None and metric.abstained is not None
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "dataset_version": str(payload.get("dataset_version", "unspecified")),
        "provider": _metadata_string(payload.get("provider")),
        "model": _metadata_string(payload.get("model")),
        "case_count": len(cases),
        "metrics": {
            "retrieval": {
                "labeled_case_count": len(labeled),
                "hit_at_1": _mean(float(bool(metric.hit_at_1)) for metric in labeled),
                "hit_at_5": _mean(float(bool(metric.hit_at_5)) for metric in labeled),
                "mrr": _mean(metric.reciprocal_rank for metric in labeled),
                "recall_at_5": _mean(metric.recall_at_5 or 0.0 for metric in labeled),
            },
            "citation_linkage": {
                "case_count": len(case_metrics),
                "valid_rate": _mean(float(metric.citation_id_valid) for metric in case_metrics),
            },
            "abstention": {
                "labeled_case_count": len(abstention_labeled),
                "accuracy": _mean(
                    float(metric.should_abstain == metric.abstained)
                    for metric in abstention_labeled
                ),
            },
            "latency_ms": {
                stage: {
                    "p50": _percentile((metric.latency(stage) for metric in case_metrics), 50),
                    "p95": _percentile((metric.latency(stage) for metric in case_metrics), 95),
                }
                for stage in ("retrieval", "generation", "evaluator")
            },
        },
        "per_case": [metric.to_report() for metric in case_metrics],
    }


def _metadata_string(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("provider and model metadata must be strings")
    return value


def resolve_evaluator_models(
    provider: str = "google",
    model_override: str | None = None,
    embedding_override: str | None = None,
) -> tuple[str, str]:
    """Resolve evaluator LLM and embedding model IDs according to project config."""
    if provider == "mistral":
        try:
            from cowork_agent.config import MistralSettings

            mistral_cfg = MistralSettings.from_env()
            llm_model = model_override or mistral_cfg.model
        except Exception:
            llm_model = model_override or "mistral-large-latest"
        emb_model = embedding_override or "mistral-embed"
        return llm_model, emb_model

    if provider == "openrouter":
        try:
            from cowork_agent.config import OpenRouterSettings

            openrouter_cfg = OpenRouterSettings.from_env()
            llm_model = model_override or openrouter_cfg.allowed_models[0]
        except Exception:
            llm_model = model_override or "deepseek/deepseek-r1-0528"
        emb_model = embedding_override or "gemini-embedding-2"
        return llm_model, emb_model

    # Default provider: Google
    try:
        from cowork_agent.config import GeminiEmbeddingSettings, GeminiSettings

        gemini_cfg = GeminiSettings.from_env()
        emb_cfg = GeminiEmbeddingSettings.from_env()
        # Prohibit throughput model gemini-3.5-flash-lite as evaluator LLM default
        default_judge = (
            "gemini-2.0-flash"
            if gemini_cfg.model == "gemini-3.5-flash-lite"
            else gemini_cfg.model
        )
        llm_model = model_override or default_judge
        emb_model = embedding_override or emb_cfg.model
    except Exception:
        llm_model = model_override or "gemini-2.0-flash"
        emb_model = embedding_override or "gemini-embedding-2"
    return llm_model, emb_model


def validate_evaluator_pairing(
    generator_model: str | None,
    evaluator_model: str | None,
    *,
    allow_same_model: bool = False,
) -> None:
    """Validate that the evaluator model is distinct from the generator model."""
    if allow_same_model or not generator_model or not evaluator_model:
        return
    if generator_model.strip().lower() == evaluator_model.strip().lower():
        msg = (
            f"Self-preference bias violation: evaluator model '{evaluator_model}' cannot be "
            f"the same as generator model '{generator_model}'. See docs/evaluations/RAGAS.md § 2.1."
        )
        raise ValueError(msg)


def init_evaluator(
    provider: str = "google",
    model: str | None = None,
    embedding_model: str | None = None,
) -> tuple[Any, Any]:
    """Initialize Ragas LLM and Embeddings wrappers, avoiding OpenAI defaults."""
    llm_id, emb_id = resolve_evaluator_models(provider, model, embedding_model)
    try:
        from ragas.embeddings import embedding_factory  # type: ignore[import-not-found]
        from ragas.llms import llm_factory  # type: ignore[import-not-found]

        evaluator_llm = llm_factory(llm_id, provider=provider)
        evaluator_embeddings = embedding_factory(provider=provider, model=emb_id)
        return evaluator_llm, evaluator_embeddings
    except (ImportError, AttributeError):
        pass

    try:
        from ragas.embeddings import LangchainEmbeddingsWrapper
        from ragas.llms import LangchainLLMWrapper
    except ImportError as exc:
        raise RuntimeError(
            "--ragas requires the optional ragas and datasets packages in the active environment"
        ) from exc

    if provider == "mistral":
        from langchain_mistralai import (  # type: ignore[import-not-found]
            ChatMistralAI,
            MistralAIEmbeddings,
        )

        chat_llm = ChatMistralAI(model=llm_id, temperature=0)
        embedder = MistralAIEmbeddings(model=emb_id)
        return LangchainLLMWrapper(chat_llm), LangchainEmbeddingsWrapper(embedder)

    from langchain_google_genai import (  # type: ignore[import-not-found]
        ChatGoogleGenerativeAI,
        GoogleGenerativeAIEmbeddings,
    )

    chat_llm = ChatGoogleGenerativeAI(model=llm_id, temperature=0)
    embedder = GoogleGenerativeAIEmbeddings(model=emb_id)
    return LangchainLLMWrapper(chat_llm), LangchainEmbeddingsWrapper(embedder)


def get_langfuse_callback() -> list[Any]:
    """Return Langfuse callback handler if configured, else empty list."""
    try:
        from langfuse.callback import CallbackHandler  # type: ignore[import-not-found]

        return [CallbackHandler()]
    except (ImportError, Exception):
        return []


def run_ragas(
    cases: Sequence[ChatRagCase],
    evaluator_llm: Any = None,
    evaluator_embeddings: Any = None,
    callbacks: list[Any] | None = None,
) -> dict[str, object]:
    """Run generation-focused RAGAS metrics (faithfulness, answer_relevancy)."""
    records = [case.raw_ragas_record for case in cases if case.raw_ragas_record is not None]
    if len(records) != len(cases):
        raise ValueError(
            "--ragas requires question, answer, contexts, and reference_answer in every case"
        )
    try:
        from datasets import Dataset  # type: ignore[import-not-found]
        from ragas import evaluate  # type: ignore[import-not-found]
        from ragas.metrics import (  # type: ignore[import-not-found]
            answer_relevancy,
            faithfulness,
        )
    except ImportError as error:
        raise RuntimeError(
            "--ragas requires the optional ragas and datasets packages in the active environment"
        ) from error

    result = evaluate(
        dataset=Dataset.from_list(records),
        metrics=[faithfulness, answer_relevancy],
        llm=evaluator_llm,
        embeddings=evaluator_embeddings,
        callbacks=callbacks or [],
        raise_exceptions=False,
    )
    scores = getattr(result, "scores", [])
    aggregates: dict[str, float] = {}
    for metric_name in ("faithfulness", "answer_relevancy"):
        values = [
            float(score[metric_name])
            for score in scores
            if isinstance(score, dict) and score.get(metric_name) is not None
        ]
        if values:
            aggregates[metric_name] = round(mean(values), 4)
    return {"evaluated_case_count": len(records), "metrics": aggregates}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="Local-only JSON dataset")
    parser.add_argument("--output", type=Path, help="Metadata-only report path")
    parser.add_argument("--ragas", action="store_true", help="Run optional RAGAS judge metrics")
    parser.add_argument(
        "--evaluator-provider",
        choices=["google", "mistral", "openrouter"],
        default="google",
        help="Evaluator LLM provider",
    )
    parser.add_argument("--evaluator-model", type=str, help="Override evaluator LLM model ID")
    parser.add_argument(
        "--evaluator-embedding-model", type=str, help="Override evaluator embedding model ID"
    )
    parser.add_argument(
        "--allow-same-model",
        action="store_true",
        help="Allow evaluator model to be identical to generator model",
    )
    args = parser.parse_args(argv)
    try:
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("dataset root must be an object")
        cases = parse_cases(payload)
        report = compute_report(payload, cases)
        if args.ragas:
            generator_model = _metadata_string(payload.get("model"))
            llm_model, emb_model = resolve_evaluator_models(
                provider=args.evaluator_provider,
                model_override=args.evaluator_model,
                embedding_override=args.evaluator_embedding_model,
            )
            validate_evaluator_pairing(
                generator_model=generator_model,
                evaluator_model=llm_model,
                allow_same_model=args.allow_same_model,
            )
            evaluator_llm, evaluator_embeddings = init_evaluator(
                provider=args.evaluator_provider,
                model=llm_model,
                embedding_model=emb_model,
            )
            callbacks = get_langfuse_callback()
            report["evaluator_provider"] = args.evaluator_provider
            report["evaluator_model"] = llm_model
            report["ragas"] = run_ragas(
                cases,
                evaluator_llm=evaluator_llm,
                evaluator_embeddings=evaluator_embeddings,
                callbacks=callbacks,
            )
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as error:
        parser.error(str(error))
    target = args.output or DEFAULT_OUTPUT_DIR / (
        f"chat-rag-eval-{datetime.now(UTC).strftime('%Y-%m-%dT%H%M%S')}.json"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2, ensure_ascii=True), encoding="utf-8")
    print(f"Evaluated {report['case_count']} Chat-RAG cases; report={target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
