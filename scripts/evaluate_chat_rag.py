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
import importlib
import json
import math
import os
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
                "reference": reference_answer,
                "user_input": question,
                "response": answer,
                "retrieved_contexts": contexts,
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
    ragas_scores: dict[str, float] | None = None

    def latency(self, stage: str) -> int | None:
        return {
            "retrieval": self.retrieval_latency_ms,
            "generation": self.generation_latency_ms,
            "evaluator": self.evaluator_latency_ms,
        }[stage]

    def to_report(self) -> dict[str, object]:
        data: dict[str, object] = {
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
        }
        if self.ragas_scores is not None:
            data["ragas_scores"] = self.ragas_scores
        data["latency_ms"] = {
            "retrieval": self.retrieval_latency_ms,
            "generation": self.generation_latency_ms,
            "evaluator": self.evaluator_latency_ms,
        }
        return data


def _case_metrics(
    case: ChatRagCase,
    ragas_scores: dict[str, float] | None = None,
) -> CaseMetrics:
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
        ragas_scores=ragas_scores,
    )


def compute_report(
    payload: Mapping[str, object],
    cases: Sequence[ChatRagCase],
    *,
    evaluator_provider: str | None = None,
    evaluator_model: str | None = None,
    evaluator_workers: Mapping[str, int | None] | None = None,
    ragas_result: Mapping[str, Any] | None = None,
    per_case_ragas_scores: Sequence[dict[str, float] | None] | None = None,
) -> dict[str, object]:
    if per_case_ragas_scores is not None and len(per_case_ragas_scores) == len(cases):
        case_metrics = tuple(
            _case_metrics(case, ragas_scores=r_scores)
            for case, r_scores in zip(cases, per_case_ragas_scores, strict=True)
        )
    else:
        case_metrics = tuple(_case_metrics(case) for case in cases)

    labeled = tuple(metric for metric in case_metrics if metric.expected_document_count)
    abstention_labeled = tuple(
        metric
        for metric in case_metrics
        if metric.should_abstain is not None and metric.abstained is not None
    )
    false_abstention_count = sum(
        1 for metric in abstention_labeled if not metric.should_abstain and metric.abstained
    )
    abstention_accuracy = (
        _mean(
            float(metric.should_abstain == metric.abstained)
            for metric in abstention_labeled
        )
        if abstention_labeled
        else None
    )

    metrics_section: dict[str, Any] = {
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
            "accuracy": abstention_accuracy,
            "false_abstention_count": false_abstention_count,
        },
    }

    if ragas_result is not None:
        ragas_metrics_dict: dict[str, Any] = {
            "evaluated_case_count": ragas_result.get("evaluated_case_count", len(cases)),
            "faithfulness": ragas_result.get("faithfulness"),
            "answer_relevancy": ragas_result.get("answer_relevancy"),
        }
        if ragas_result.get("failed_case_count", 0) > 0:
            ragas_metrics_dict["failed_case_count"] = ragas_result["failed_case_count"]
        metrics_section["ragas"] = ragas_metrics_dict

    metrics_section["latency_ms"] = {
        stage: {
            "p50": _percentile((metric.latency(stage) for metric in case_metrics), 50),
            "p95": _percentile((metric.latency(stage) for metric in case_metrics), 95),
        }
        for stage in ("retrieval", "generation", "evaluator")
    }

    report: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "dataset_version": str(payload.get("dataset_version", "unspecified")),
        "provider": _metadata_string(payload.get("provider")),
        "model": _metadata_string(payload.get("model")),
    }
    if evaluator_provider is not None:
        report["evaluator_provider"] = evaluator_provider
    if evaluator_model is not None:
        report["evaluator_model"] = evaluator_model
    if evaluator_workers is not None:
        report["evaluator_workers"] = dict(evaluator_workers)

    report["case_count"] = len(cases)
    report["metrics"] = metrics_section
    report["per_case"] = [metric.to_report() for metric in case_metrics]
    return report


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
            default_model = (
                openrouter_cfg.allowed_models[0]
                if openrouter_cfg.allowed_models
                else openrouter_cfg.model
            )
            llm_model = model_override or default_model
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


def _normalize_model_name(name: str) -> str:
    norm = name.strip().lower()
    prefixes = (
        "models/",
        "google/",
        "mistral/",
        "mistralai/",
        "openrouter/",
        "openai/",
        "anthropic/",
    )
    while True:
        stripped = False
        for prefix in prefixes:
            if norm.startswith(prefix):
                norm = norm[len(prefix):]
                stripped = True
                break
        if not stripped:
            break
    return norm


def validate_evaluator_pairing(
    generator_model: str | None,
    evaluator_model: str | None,
    *,
    allow_same_model: bool = False,
) -> None:
    """Validate that the evaluator model is distinct from generator and not throughput tier."""
    if not evaluator_model:
        return

    eval_norm = _normalize_model_name(evaluator_model)
    if eval_norm == "gemini-3.5-flash-lite":
        msg = (
            "Throughput model 'gemini-3.5-flash-lite' is prohibited as evaluator judge. "
            "See docs/evaluations/RAGAS.md § 2."
        )
        raise ValueError(msg)

    if allow_same_model or not generator_model:
        return

    gen_norm = _normalize_model_name(generator_model)
    if gen_norm == eval_norm:
        msg = (
            f"Self-preference bias violation: evaluator model '{evaluator_model}' cannot be "
            f"the same as generator model '{generator_model}'. See docs/evaluations/RAGAS.md § 2.1."
        )
        raise ValueError(msg)


def count_active_keys(provider: str, environ: Mapping[str, str] | None = None) -> int:
    """Count active API keys for the specified provider from environment."""
    env = os.environ if environ is None else environ
    prefix_map = {
        "mistral": ["MISTRAL_API_KEY"],
        "google": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
        "openrouter": ["OPENROUTER_API_KEY"],
    }
    base_vars = prefix_map.get(provider, [f"{provider.upper()}_API_KEY"])
    keys: set[str] = set()
    for base_var in base_vars:
        for key, val in env.items():
            if (
                key == base_var
                or (key.startswith(base_var) and key[len(base_var):].isdigit())
                or (key.startswith(f"{base_var}_") and key[len(base_var) + 1:].isdigit())
            ):
                cleaned = val.strip()
                if cleaned and not cleaned.startswith("replace-with-"):
                    keys.add(cleaned)
    return max(1, len(keys))


def _resolve_api_key(provider: str, environ: Mapping[str, str] | None = None) -> str:
    env = os.environ if environ is None else environ
    if provider == "google":
        for direct_name in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
            direct = env.get(direct_name, "").strip()
            if direct and not direct.startswith("replace-with-"):
                return direct
        for idx in range(1, 100):
            for var_name in (
                f"GEMINI_API_KEY_{idx}",
                f"GEMINI_API_KEY{idx}",
                f"GOOGLE_API_KEY_{idx}",
                f"GOOGLE_API_KEY{idx}",
            ):
                val = env.get(var_name, "").strip()
                if val and not val.startswith("replace-with-"):
                    return val
        return ""
    if provider == "mistral":
        direct = env.get("MISTRAL_API_KEY", "").strip()
        if direct and not direct.startswith("replace-with-"):
            return direct
        for idx in range(1, 100):
            for var_name in (f"MISTRAL_API_KEY_{idx}", f"MISTRAL_API_KEY{idx}"):
                val = env.get(var_name, "").strip()
                if val and not val.startswith("replace-with-"):
                    return val
        return ""
    if provider == "openrouter":
        direct = env.get("OPENROUTER_API_KEY", "").strip()
        if direct and not direct.startswith("replace-with-"):
            return direct
        for idx in range(1, 100):
            for var_name in (f"OPENROUTER_API_KEY_{idx}", f"OPENROUTER_API_KEY{idx}"):
                val = env.get(var_name, "").strip()
                if val and not val.startswith("replace-with-"):
                    return val
        return ""
    return ""


def compute_effective_workers(
    requested_max_workers: int | None,
    active_key_count: int,
    ready_case_count: int,
    plugin_limit: int = 5,
) -> int:
    """Compute effective worker count dynamically bounded by keys, cases, and plugin limit."""
    req = (
        requested_max_workers
        if (requested_max_workers is not None and requested_max_workers > 0)
        else active_key_count
    )
    return max(1, min(req, max(1, active_key_count), max(1, ready_case_count), plugin_limit))


def init_evaluator(
    provider: str = "google",
    model: str | None = None,
    embedding_model: str | None = None,
) -> tuple[Any, Any]:
    """Initialize Ragas LLM and Embeddings wrappers, avoiding OpenAI defaults."""
    llm_id, emb_id = resolve_evaluator_models(provider, model, embedding_model)
    try:
        ragas_llms = importlib.import_module("ragas.llms")
        ragas_embeddings = importlib.import_module("ragas.embeddings")
        LangchainLLMWrapper = ragas_llms.LangchainLLMWrapper
        LangchainEmbeddingsWrapper = ragas_embeddings.LangchainEmbeddingsWrapper
    except (ImportError, AttributeError) as exc:
        missing_name = getattr(exc, "name", "") or ""
        if missing_name and not missing_name.startswith("ragas") and missing_name != "datasets":
            raise RuntimeError(
                f"Evaluator provider '{provider}' requires {missing_name.replace('_', '-')}. "
                "Install with: uv sync --extra eval"
            ) from exc
        raise RuntimeError(
            "--ragas requires the optional ragas and datasets packages in the active environment. "
            "Install them with: uv sync --extra eval (or pip install -e .[eval])"
        ) from exc

    if provider == "mistral":
        try:
            lc_mistral = importlib.import_module("langchain_mistralai")
            ChatMistralAI = lc_mistral.ChatMistralAI
            MistralAIEmbeddings = lc_mistral.MistralAIEmbeddings
            api_key = _resolve_api_key("mistral")
            chat_llm = ChatMistralAI(model=llm_id, api_key=api_key or None, temperature=0)
            embedder = MistralAIEmbeddings(model=emb_id, api_key=api_key or None)
            return LangchainLLMWrapper(chat_llm), LangchainEmbeddingsWrapper(embedder)
        except (ImportError, AttributeError) as exc:
            raise RuntimeError(
                f"Evaluator provider '{provider}' requires langchain-mistralai. "
                "Install with: uv sync --extra eval"
            ) from exc

    if provider == "openrouter":
        try:
            lc_openai = importlib.import_module("langchain_openai")
            ChatOpenAI = lc_openai.ChatOpenAI
            api_key = _resolve_api_key("openrouter")
            chat_llm = ChatOpenAI(
                model=llm_id,
                openai_api_key=api_key or None,
                openai_api_base="https://openrouter.ai/api/v1",
                temperature=0,
            )
            if "mistral" in emb_id.lower():
                lc_mistral = importlib.import_module("langchain_mistralai")
                m_key = _resolve_api_key("mistral")
                embedder = lc_mistral.MistralAIEmbeddings(
                    model=emb_id, api_key=m_key or None
                )
            else:
                lc_genai = importlib.import_module("langchain_google_genai")
                gemini_key = _resolve_api_key("google")
                embedder = lc_genai.GoogleGenerativeAIEmbeddings(
                    model=emb_id,
                    google_api_key=gemini_key or None,
                )
            return LangchainLLMWrapper(chat_llm), LangchainEmbeddingsWrapper(embedder)
        except (ImportError, AttributeError) as exc:
            raise RuntimeError(
                f"Evaluator provider '{provider}' requires langchain-openai and "
                "langchain-google-genai. Install with: uv sync --extra eval"
            ) from exc

    if provider == "google":
        try:
            lc_genai = importlib.import_module("langchain_google_genai")
            ChatGoogleGenerativeAI = lc_genai.ChatGoogleGenerativeAI
            GoogleGenerativeAIEmbeddings = lc_genai.GoogleGenerativeAIEmbeddings
            api_key = _resolve_api_key("google")
            chat_llm = ChatGoogleGenerativeAI(
                model=llm_id,
                google_api_key=api_key or None,
                temperature=0,
            )
            embedder = GoogleGenerativeAIEmbeddings(
                model=emb_id,
                google_api_key=api_key or None,
            )
            return LangchainLLMWrapper(chat_llm), LangchainEmbeddingsWrapper(embedder)
        except (ImportError, AttributeError) as exc:
            raise RuntimeError(
                f"Evaluator provider '{provider}' requires langchain-google-genai. "
                "Install with: uv sync --extra eval"
            ) from exc

    raise ValueError(f"Unsupported evaluator provider: '{provider}'")


def get_langfuse_callback() -> list[Any]:
    """Return Langfuse callback handler if configured, else empty list."""
    pk = os.environ.get("LANGFUSE_PUBLIC_KEY", "").strip()
    sk = os.environ.get("LANGFUSE_SECRET_KEY", "").strip()
    if not pk or not sk or pk.startswith("replace-with-") or sk.startswith("replace-with-"):
        return []
    for mod_name in ("langfuse.langchain", "langfuse.callback"):
        try:
            langfuse_cb = importlib.import_module(mod_name)
            CallbackHandler = getattr(langfuse_cb, "CallbackHandler", None)
            if CallbackHandler is not None:
                return [CallbackHandler()]
        except Exception:
            continue
    return []


def run_ragas(
    cases: Sequence[ChatRagCase],
    evaluator_llm: Any = None,
    evaluator_embeddings: Any = None,
    callbacks: list[Any] | None = None,
) -> dict[str, Any]:
    """Run generation-focused RAGAS metrics (faithfulness, answer_relevancy)."""
    if evaluator_llm is None or evaluator_embeddings is None:
        raise ValueError(
            "evaluator_llm and evaluator_embeddings must be explicitly provided "
            "to avoid unintended OpenAI fallback"
        )

    records = [case.raw_ragas_record for case in cases if case.raw_ragas_record is not None]
    if len(records) != len(cases):
        raise ValueError(
            "--ragas requires question, answer, contexts, and reference_answer in every case"
        )

    try:
        datasets_mod = importlib.import_module("datasets")
        ragas_mod = importlib.import_module("ragas")
        ragas_metrics = importlib.import_module("ragas.metrics")
        Dataset = datasets_mod.Dataset
        evaluate = ragas_mod.evaluate
        faithfulness = ragas_metrics.faithfulness
        answer_relevancy = ragas_metrics.answer_relevancy
    except (ImportError, AttributeError) as error:
        raise RuntimeError(
            "--ragas requires the optional ragas and datasets packages in the active environment. "
            "Install them with: uv sync --extra eval (or pip install -e .[eval])"
        ) from error

    # Exclude context_precision and context_recall explicitly
    metrics = [faithfulness, answer_relevancy]

    # Convert samples to modern EvaluationDataset if available, else Dataset
    eval_input: Any
    try:
        EvaluationDataset = ragas_mod.EvaluationDataset
        SingleTurnSample = ragas_mod.SingleTurnSample
        samples = [
            SingleTurnSample(
                user_input=rec["user_input"],
                response=rec["response"],
                retrieved_contexts=rec["retrieved_contexts"],
                reference=rec["reference"],
            )
            for rec in records
        ]
        eval_input = EvaluationDataset(samples=samples)
    except Exception:
        eval_input = Dataset.from_list(records)

    run_config = None
    try:
        RunConfig = getattr(ragas_mod, "RunConfig", None)
        if RunConfig is not None:
            run_config = RunConfig(max_workers=1)
    except Exception:
        run_config = None

    eval_kwargs: dict[str, Any] = {
        "dataset": eval_input,
        "metrics": metrics,
        "llm": evaluator_llm,
        "embeddings": evaluator_embeddings,
        "callbacks": callbacks or [],
        "raise_exceptions": False,
    }
    if run_config is not None:
        eval_kwargs["run_config"] = run_config

    try:
        result = evaluate(**eval_kwargs)
    except TypeError as exc:
        if "run_config" in str(exc) and "run_config" in eval_kwargs:
            eval_kwargs.pop("run_config")
            result = evaluate(**eval_kwargs)
        else:
            raise

    raw_scores = getattr(result, "scores", None)
    if raw_scores is None and isinstance(result, Mapping) and "scores" in result:
        raw_scores = result["scores"]

    scores: list[dict[str, Any]] = []
    if raw_scores is not None:
        if isinstance(raw_scores, list):
            scores = [dict(s) if isinstance(s, Mapping) else s for s in raw_scores]
        elif hasattr(raw_scores, "to_list") and callable(raw_scores.to_list):
            try:
                converted = raw_scores.to_list()
                if isinstance(converted, list):
                    scores = [dict(s) if isinstance(s, Mapping) else s for s in converted]
            except Exception:
                pass
        elif hasattr(raw_scores, "to_dict") and callable(raw_scores.to_dict):
            try:
                converted = raw_scores.to_dict(orient="records")
                if isinstance(converted, list):
                    scores = [dict(s) if isinstance(s, Mapping) else s for s in converted]
            except Exception:
                try:
                    converted_dict = raw_scores.to_dict()
                    if isinstance(converted_dict, Mapping):
                        col_lens = [
                            len(v) for v in converted_dict.values() if isinstance(v, Sequence)
                        ]
                        if col_lens and col_lens[0] == len(cases):
                            scores = [
                                {
                                    k: converted_dict[k][i]
                                    for k in converted_dict
                                    if isinstance(converted_dict[k], Sequence)
                                    and len(converted_dict[k]) > i
                                }
                                for i in range(len(cases))
                            ]
                except Exception:
                    pass
        elif isinstance(raw_scores, Mapping):
            col_lens = [len(v) for v in raw_scores.values() if isinstance(v, Sequence)]
            if col_lens and col_lens[0] == len(cases):
                scores = [
                    {
                        k: raw_scores[k][i]
                        for k in raw_scores
                        if isinstance(raw_scores[k], Sequence) and len(raw_scores[k]) > i
                    }
                    for i in range(len(cases))
                ]
        elif isinstance(raw_scores, Sequence):
            scores = [dict(s) if isinstance(s, Mapping) else s for s in raw_scores]

    if not scores and hasattr(result, "to_pandas") and callable(result.to_pandas):
        try:
            df = result.to_pandas()
            scores = df.to_dict(orient="records")
        except Exception:
            scores = []

    aggregates: dict[str, float | None] = {}
    for metric_name in ("faithfulness", "answer_relevancy"):
        values: list[float] = []
        if isinstance(scores, list):
            for score in scores:
                if isinstance(score, dict):
                    val = score.get(metric_name)
                    if (
                        val is not None
                        and isinstance(val, (int, float))
                        and not math.isnan(float(val))
                    ):
                        values.append(float(val))
        elif isinstance(scores, dict):
            val = scores.get(metric_name)
            if (
                val is not None
                and isinstance(val, (int, float))
                and not math.isnan(float(val))
            ):
                values.append(float(val))

        if values:
            aggregates[metric_name] = round(mean(values), 4)
        else:
            top_val = None
            if isinstance(result, dict) and metric_name in result:
                top_val = result[metric_name]
            elif hasattr(result, "__getitem__"):
                try:
                    top_val = result[metric_name]
                except Exception:
                    top_val = None
            if top_val is None and hasattr(result, metric_name):
                top_val = getattr(result, metric_name)

            if (
                top_val is not None
                and isinstance(top_val, (int, float))
                and not math.isnan(float(top_val))
            ):
                aggregates[metric_name] = round(float(top_val), 4)
            else:
                aggregates[metric_name] = None

    per_case_scores: list[dict[str, float]] = []
    failed_case_count = 0
    if isinstance(scores, list) and len(scores) == len(cases):
        for score in scores:
            case_score: dict[str, float] = {}
            case_failed = False
            if isinstance(score, dict):
                for metric_name in ("faithfulness", "answer_relevancy"):
                    val = score.get(metric_name)
                    if (
                        val is not None
                        and isinstance(val, (int, float))
                        and not math.isnan(float(val))
                    ):
                        case_score[metric_name] = round(float(val), 4)
                    else:
                        case_failed = True
            else:
                case_failed = True
            if case_failed:
                failed_case_count += 1
            per_case_scores.append(case_score)
    else:
        any_valid_aggregate = any(v is not None for v in aggregates.values())
        failed_case_count = 0 if any_valid_aggregate else len(cases)
        per_case_scores = [{} for _ in cases]

    return {
        "evaluated_case_count": len(records),
        "failed_case_count": failed_case_count,
        "faithfulness": aggregates.get("faithfulness"),
        "answer_relevancy": aggregates.get("answer_relevancy"),
        "per_case_scores": per_case_scores,
    }


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
    parser.add_argument(
        "--max-workers",
        type=int,
        default=None,
        help="Maximum worker count for batch evaluator scheduler",
    )
    parser.add_argument(
        "--save-per-case-scores",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Save detailed per-case ragas scores in report",
    )
    args = parser.parse_args(argv)
    try:
        if args.max_workers is not None and args.max_workers <= 0:
            raise ValueError("--max-workers must be a positive integer")
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("dataset root must be an object")
        cases = parse_cases(payload)

        if args.ragas:
            try:
                from cowork_agent.config import load_runtime_environment

                load_runtime_environment()
            except Exception:
                pass

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
            active_keys = count_active_keys(args.evaluator_provider)
            effective_workers = compute_effective_workers(
                requested_max_workers=args.max_workers,
                active_key_count=active_keys,
                ready_case_count=len(cases),
            )
            evaluator_llm, evaluator_embeddings = init_evaluator(
                provider=args.evaluator_provider,
                model=llm_model,
                embedding_model=emb_model,
            )
            callbacks = get_langfuse_callback()
            ragas_result = run_ragas(
                cases,
                evaluator_llm=evaluator_llm,
                evaluator_embeddings=evaluator_embeddings,
                callbacks=callbacks,
            )
            per_case_scores = (
                ragas_result.get("per_case_scores") if args.save_per_case_scores else None
            )
            workers_info = None
            if args.max_workers is not None or active_keys > 1:
                workers_info = {
                    "requested_max_workers": args.max_workers,
                    "effective_workers": effective_workers,
                    "active_key_count": active_keys,
                }
            report = compute_report(
                payload,
                cases,
                evaluator_provider=args.evaluator_provider,
                evaluator_model=llm_model,
                evaluator_workers=workers_info,
                ragas_result=ragas_result,
                per_case_ragas_scores=per_case_scores,
            )
        else:
            report = compute_report(payload, cases)

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
