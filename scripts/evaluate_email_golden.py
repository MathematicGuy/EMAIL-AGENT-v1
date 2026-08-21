"""Write immutable, metadata-only Email Intent evaluation runs."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parseaddr
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from cowork_agent.config import EmailRagQualitySettings
from cowork_agent.domain.target_contracts import (
    BodyFormat,
    EphemeralEmailEnvelope,
    FetchStatus,
    RetrievalFilters,
    RetrievalLimits,
    RetrievalStatus,
    SemanticRetrievalRequest,
    SemanticRetrievalResponse,
)
from cowork_agent.features.email_action_plan.correlation import TaskCandidate
from cowork_agent.features.email_action_plan.evidence import (
    GATE_VERSION,
    EvidenceStatus,
    assess_retrieval_evidence,
)
from cowork_agent.features.email_action_plan.query_rewrite import (
    RetrievalQueryRewriterPort,
    build_query_rewrite_input,
    deterministic_query,
)
from cowork_agent.features.email_action_plan.routing import (
    candidate_requires_processing,
    resolve_candidate_after_retrieval,
)

try:
    from scripts.email_evaluation_artifacts import (
        PIPELINE_VERSION,
        PROMPT_VERSION,
        atomic_write_json,
        dataset_fingerprint,
        load_json_object,
        validate_candidate_dataset,
        validate_golden_dataset,
        validate_run_artifact,
    )
except ModuleNotFoundError:
    from email_evaluation_artifacts import (  # type: ignore[no-redef]
        PIPELINE_VERSION,
        PROMPT_VERSION,
        atomic_write_json,
        dataset_fingerprint,
        load_json_object,
        validate_candidate_dataset,
        validate_golden_dataset,
        validate_run_artifact,
    )

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CANDIDATES = REPO_ROOT / "evaluations" / "EMAIL" / "gmail_candidates.json"
DEFAULT_GOLDEN = REPO_ROOT / "evaluations" / "EMAIL" / "golden_dataset.json"
DEFAULT_RUNS_DIR = REPO_ROOT / "evaluations" / "EMAIL" / "runs"
MAX_CASES = 50


@dataclass(frozen=True, slots=True)
class EvaluationRuntime:
    classifier: Any
    semantic_memory: Any
    query_rewriter: RetrievalQueryRewriterPort | None
    quality_settings: EmailRagQualitySettings
    provider: str
    model: str


def load_envelopes_from_candidates(
    candidates: Sequence[Mapping[str, object]],
) -> list[EphemeralEmailEnvelope]:
    """Convert private candidate content into ephemeral classifier inputs."""

    envelopes = []
    for candidate in candidates:
        sender_name, sender_email = parseaddr(str(candidate["sender"]))
        envelopes.append(
            EphemeralEmailEnvelope(
                run_id="email-evaluation",
                user_id="",
                gmail_message_id=str(candidate["source_message_id"]),
                gmail_thread_id=str(candidate["gmail_thread_id"]),
                gmail_url="",
                sender_name=sender_name,
                sender_email=sender_email or str(candidate["sender"]),
                recipients=(),
                subject=str(candidate["subject"]),
                received_at=datetime.fromisoformat(str(candidate["received_at"])),
                labels=tuple(str(label) for label in cast(Sequence[object], candidate["labels"])),
                normalized_body=str(candidate["gmail_content"]),
                body_format=BodyFormat.TEXT,
                attachments_present=False,
                fetch_status=FetchStatus.COMPLETE,
            )
        )
    return envelopes


def select_shard(
    candidates: Sequence[Mapping[str, object]], *, shard_index: int, shard_count: int, limit: int
) -> list[dict[str, object]]:
    """Select one explicit, contiguous candidate shard without exceeding the run cap."""

    if not 1 <= limit <= MAX_CASES:
        raise ValueError(f"limit must be between 1 and {MAX_CASES}")
    if shard_count < 1:
        raise ValueError("shard_count must be at least 1")
    if not 1 <= shard_index <= shard_count:
        raise ValueError("shard_index must be between 1 and shard_count")
    start = (shard_index - 1) * limit
    end = start + limit
    if end > len(candidates):
        raise ValueError(f"requested shard requires {end} candidates; found {len(candidates)}")
    return [dict(candidate) for candidate in candidates[start:end]]


def _prediction(decision: Any, *, is_fallback: bool) -> dict[str, object]:
    return {
        "actionability": decision.actionability.value,
        "email_is_sufficient": decision.email_is_sufficient,
        "expected_document_types": [item.value for item in decision.expected_document_types],
        "confidence": decision.confidence,
        "source_status": "classifier_fallback" if is_fallback else "model_prediction",
    }


def _candidate(message: EphemeralEmailEnvelope, decision: Any) -> TaskCandidate:
    return TaskCandidate(
        candidate_key=message.gmail_message_id,
        gmail_thread_id=message.gmail_thread_id,
        incident_key=None,
        source_message_ids=(message.gmail_message_id,),
        decisions=((message.gmail_message_id, decision),),
    )


async def _retrieve_candidate(
    message: EphemeralEmailEnvelope,
    candidate: TaskCandidate,
    semantic_memory: Any,
    query_rewriter: RetrievalQueryRewriterPort | None,
) -> tuple[SemanticRetrievalResponse, str]:
    decision = candidate.decisions[0][1]
    gaps = tuple(decision.knowledge_gaps)
    rewrite_input = build_query_rewrite_input(
        candidate_action_items=(decision.candidate_action_item,),
        knowledge_gaps=gaps,
        messages=((message.subject, message.normalized_body),),
    )
    query = decision.retrieval_query
    rewrite_status = "classifier_query" if query else "fallback"
    if query is None and query_rewriter is not None:
        try:
            query = await query_rewriter.rewrite(rewrite_input)
            rewrite_status = "rewritten" if query else "fallback"
        except Exception:
            query = None
    if not query:
        query = deterministic_query(rewrite_input)
        rewrite_status = "fallback"
    request = SemanticRetrievalRequest(
        run_id="email-evaluation",
        user_id="",
        query=query,
        knowledge_gaps=gaps,
        filters=RetrievalFilters(document_status=("ready",)),
        limits=RetrievalLimits(top_k=5, min_score=-1.0, timeout_ms=8_000),
    )
    for _attempt in (1, 2):
        try:
            return await semantic_memory.retrieve(request), rewrite_status
        except Exception:
            continue
    return (
        SemanticRetrievalResponse(
            query_id=f"q_eval_{message.gmail_message_id}",
            chunks=(),
            retrieval_status=RetrievalStatus.UNAVAILABLE,
            latency_ms=0,
        ),
        rewrite_status,
    )


async def evaluate(
    messages: Sequence[EphemeralEmailEnvelope],
    classifier: Any,
    current_time: datetime,
    *,
    semantic_memory: Any,
    query_rewriter: RetrievalQueryRewriterPort | None,
    quality_settings: EmailRagQualitySettings,
) -> dict[str, object]:
    """Run classifier, retrieval, evidence gate, and final resolver metadata-only."""

    classify = classifier.classify
    unwrapped = getattr(classify, "__wrapped__", None)
    if unwrapped is not None:
        for method_name in ("_generate", "_complete"):
            method = getattr(classifier, method_name, None)
            inner = getattr(method, "__wrapped__", None)
            if inner is not None:
                setattr(classifier, method_name, inner.__get__(classifier, type(classifier)))
        result = await unwrapped(classifier, "UTC", current_time, messages)
    else:
        result = await classify("UTC", current_time, messages)

    decisions = {item.gmail_message_id: item for item in result.decisions}
    results: list[dict[str, object]] = []
    missing_ids: list[str] = []
    for message in messages:
        classified = decisions.get(message.gmail_message_id)
        if classified is None:
            missing_ids.append(message.gmail_message_id)
            continue
        decision = classified.decision
        candidate = _candidate(message, decision)
        prediction = _prediction(decision, is_fallback=classified.is_fallback)
        if candidate_requires_processing(candidate):
            response, rewrite_status = await _retrieve_candidate(
                message, candidate, semantic_memory, query_rewriter
            )
            evidence = assess_retrieval_evidence(response, quality_settings)
            resolution = resolve_candidate_after_retrieval(candidate, evidence.status)
            retrieval = {
                "attempted": True,
                "retrieval_status": response.retrieval_status.value,
                "evidence_status": evidence.status.value,
                "result_count": len(response.chunks),
                "accepted_chunk_count": len(evidence.response.chunks),
                "top_rerank_score": evidence.top_rerank_score,
                "query_rewrite_status": rewrite_status,
                "degraded": evidence.status is EvidenceStatus.UNAVAILABLE,
            }
        else:
            resolution = resolve_candidate_after_retrieval(candidate, EvidenceStatus.UNSUPPORTED)
            retrieval = {
                "attempted": False,
                "retrieval_status": None,
                "evidence_status": None,
                "result_count": 0,
                "accepted_chunk_count": 0,
                "top_rerank_score": None,
                "query_rewrite_status": None,
                "degraded": False,
            }
        routing = {
            "resolved_route": resolution.route.value,
            "mode": resolution.mode,
            "forced_by_guard": resolution.forced_by_guard,
            "reason_codes": [code.value for code in resolution.reason_codes],
        }
        results.append(
            {
                "source_message_id": message.gmail_message_id,
                "prediction": prediction,
                "retrieval": retrieval,
                "routing": routing,
            }
        )
    return {"results": results, "missing_ids": missing_ids}


def _validated_selected_case_ids(
    golden: Mapping[str, object], selected_candidates: Sequence[Mapping[str, object]]
) -> set[str]:
    golden_cases = cast(Sequence[Mapping[str, object]], golden["cases"])
    golden_by_id = {str(case["case_id"]): case for case in golden_cases}
    selected_ids = {str(candidate["case_id"]) for candidate in selected_candidates}
    missing_ids = selected_ids - golden_by_id.keys()
    if missing_ids:
        missing = ", ".join(sorted(missing_ids))
        raise ValueError(f"golden dataset is missing selected case_id(s): {missing}")
    for candidate in selected_candidates:
        golden_case = golden_by_id[str(candidate["case_id"])]
        if candidate["source_message_id"] != golden_case["source_message_id"]:
            raise ValueError(
                f"golden source_message_id does not match case_id {candidate['case_id']}"
            )
    return selected_ids


def build_run_artifact(
    summary: Mapping[str, object],
    *,
    golden: Mapping[str, object],
    selected_candidates: Sequence[Mapping[str, object]],
    provider: str,
    model: str,
    quality_settings: EmailRagQualitySettings,
    run_at: datetime,
    shard_index: int,
    shard_count: int,
) -> dict[str, object]:
    """Build and validate one immutable, truth-free evaluation run."""

    validated_golden = validate_golden_dataset(golden)
    selected_case_ids = _validated_selected_case_ids(validated_golden, selected_candidates)
    raw_results = summary.get("results")
    if not isinstance(raw_results, Sequence) or isinstance(raw_results, (str, bytes)):
        raise ValueError("evaluation summary.results must be a sequence")
    results_by_source_id: dict[str, Mapping[str, object]] = {}
    for raw_result in raw_results:
        if not isinstance(raw_result, Mapping):
            raise ValueError("evaluation summary.results must contain objects")
        source_message_id = raw_result.get("source_message_id")
        prediction = raw_result.get("prediction")
        retrieval = raw_result.get("retrieval")
        routing = raw_result.get("routing")
        if (
            not isinstance(source_message_id, str)
            or not isinstance(prediction, Mapping)
            or not isinstance(retrieval, Mapping)
            or not isinstance(routing, Mapping)
        ):
            raise ValueError(
                "evaluation summary result must include prediction, retrieval, and routing"
            )
        results_by_source_id[source_message_id] = raw_result

    cases: list[dict[str, object]] = []
    for candidate in selected_candidates:
        source_message_id = str(candidate["source_message_id"])
        result = results_by_source_id.get(source_message_id)
        if result is None:
            raise ValueError(f"evaluation summary is missing source_message_id {source_message_id}")
        cases.append(
            {
                "case_id": str(candidate["case_id"]),
                "prediction": dict(cast(Mapping[str, object], result["prediction"])),
                "retrieval": dict(cast(Mapping[str, object], result["retrieval"])),
                "routing": dict(cast(Mapping[str, object], result["routing"])),
            }
        )
    if len(cases) > MAX_CASES:
        raise ValueError(f"run contains {len(cases)} cases; maximum is {MAX_CASES}")
    if {case["case_id"] for case in cases} != selected_case_ids:
        raise ValueError("run case_id set differs from the selected candidates")

    created_at = run_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
    run = {
        "schema_version": 2,
        "run_id": f"email-pipeline-{run_at.astimezone(UTC):%Y%m%dT%H%M%SZ}-{uuid4().hex}",
        "created_at": created_at,
        "dataset_fingerprint": dataset_fingerprint(validated_golden),
        "rubric_version": validated_golden["rubric_version"],
        "provider": provider,
        "model": model,
        "prompt_version": PROMPT_VERSION,
        "pipeline_version": PIPELINE_VERSION,
        "quality_gate": {
            "version": GATE_VERSION,
            "min_rerank_score": quality_settings.min_rerank_score,
            "relative_cutoff_ratio": quality_settings.relative_cutoff_ratio,
        },
        "shard": {"index": shard_index, "count": shard_count, "case_count": len(cases)},
        "cases": cases,
    }
    return validate_run_artifact(run)


def build_live_classifier() -> tuple[Any, str, str]:
    from cowork_agent.config import load_runtime_environment

    load_runtime_environment()
    os.environ["LANGFUSE_TRACING_ENABLED"] = "false"
    provider = os.getenv("LLM_PROVIDER", "gemini").strip().lower()
    if provider == "gemini":
        from cowork_agent.config import GeminiSettings
        from cowork_agent.integrations.llm.providers.gemini import GeminiRouteClassifier

        settings = GeminiSettings.from_env()
        return (
            GeminiRouteClassifier(settings, include_filtered_summary=False),
            provider,
            settings.model,
        )
    if provider == "groq":
        from cowork_agent.config import GroqSettings
        from cowork_agent.integrations.llm.providers.groq import GroqRouteClassifier

        settings = GroqSettings.from_env()
        return GroqRouteClassifier(settings), provider, settings.model
    if provider == "mistral":
        from cowork_agent.config import MistralSettings
        from cowork_agent.integrations.llm.providers.mistral import MistralRouteClassifier

        settings = MistralSettings.from_env()
        return MistralRouteClassifier(settings), provider, settings.model
    if provider == "openrouter":
        from cowork_agent.config import OpenRouterSettings
        from cowork_agent.integrations.llm.providers.openrouter import OpenRouterRouteClassifier

        settings = OpenRouterSettings.from_env()
        return OpenRouterRouteClassifier(settings), provider, settings.model
    raise ValueError(f"unsupported LLM_PROVIDER for email evaluation: {provider}")


async def build_live_runtime() -> EvaluationRuntime:
    """Compose the same company retrieval and fixed-Gemini rewrite providers."""
    from cowork_agent.config import GeminiSettings, JinaEmbeddingSettings
    from cowork_agent.integrations.llm.providers.gemini import (
        GeminiRetrievalQueryRewriter,
    )
    from cowork_agent.integrations.rag.bootstrap import build_semantic_memory

    classifier, provider, model = build_live_classifier()
    quality_settings = EmailRagQualitySettings.from_env()
    semantic_memory = await build_semantic_memory(JinaEmbeddingSettings.from_env())
    query_rewriter = GeminiRetrievalQueryRewriter(GeminiSettings.from_env())
    return EvaluationRuntime(
        classifier=classifier,
        semantic_memory=semantic_memory,
        query_rewriter=query_rewriter,
        quality_settings=quality_settings,
        provider=provider,
        model=model,
    )


async def run_live_evaluation(
    messages: Sequence[EphemeralEmailEnvelope], run_at: datetime
) -> tuple[EvaluationRuntime, dict[str, object]]:
    """Build and exercise async providers on one event loop."""

    runtime = await build_live_runtime()
    summary = await evaluate(
        messages,
        runtime.classifier,
        run_at,
        semantic_memory=runtime.semantic_memory,
        query_rewriter=runtime.query_rewriter,
        quality_settings=runtime.quality_settings,
    )
    return runtime, summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write an immutable Email Intent evaluation run.")
    parser.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATES)
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS_DIR)
    parser.add_argument("--shard-index", type=int, required=True)
    parser.add_argument("--shard-count", type=int, required=True)
    parser.add_argument("--limit", type=int, default=MAX_CASES)
    args = parser.parse_args(argv)
    try:
        candidate_dataset = validate_candidate_dataset(load_json_object(args.candidates))
        golden = validate_golden_dataset(load_json_object(args.golden))
        candidate_cases = cast(Sequence[Mapping[str, object]], candidate_dataset["cases"])
        selected = select_shard(
            candidate_cases,
            shard_index=args.shard_index,
            shard_count=args.shard_count,
            limit=args.limit,
        )
        _validated_selected_case_ids(golden, selected)
        run_at = datetime.now(UTC)
        runtime, summary = asyncio.run(
            run_live_evaluation(load_envelopes_from_candidates(selected), run_at)
        )
        run = build_run_artifact(
            summary,
            golden=golden,
            selected_candidates=selected,
            provider=runtime.provider,
            model=runtime.model,
            quality_settings=runtime.quality_settings,
            run_at=run_at,
            shard_index=args.shard_index,
            shard_count=args.shard_count,
        )
        destination = args.runs_dir / f"{run['run_id']}.json"
        atomic_write_json(run, destination)
    except (OSError, ValueError) as exc:
        print(f"Email evaluation failed: {exc}", file=sys.stderr)
        return 2
    print(f"Wrote immutable Email Intent run {run['run_id']} with {len(selected)} cases.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
