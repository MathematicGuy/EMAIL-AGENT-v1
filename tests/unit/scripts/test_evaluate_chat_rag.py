import json
from pathlib import Path

import pytest

from tests.unit.scripts.cli_harness import load_script, run_cli

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts/evaluate_chat_rag.py"
LOCAL_ONLY_FIELDS = ("question", "answer", "contexts", "reference_answer")


def _assert_no_local_only_fields(node: object) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            assert key not in LOCAL_ONLY_FIELDS, f"report leaked local-only field {key}"
            _assert_no_local_only_fields(value)
    elif isinstance(node, list):
        for item in node:
            _assert_no_local_only_fields(item)


def _module():
    return load_script("evaluate_chat_rag")


def test_default_output_directory_uses_the_evaluation_workspace() -> None:
    assert _module().DEFAULT_OUTPUT_DIR == REPO_ROOT / "evaluations" / "CHAT-RAG" / "baselines"




def test_metadata_only_report_calculates_retrieval_linkage_abstention_and_latency() -> None:
    module = _module()
    payload = {
        "dataset_version": "synthetic-v1",
        "provider": "fixture",
        "model": "fake",
        "cases": [
            {
                "id": "case-1",
                "expected_document_ids": ["doc-1"],
                "retrieved_document_ids": ["doc-1", "doc-2"],
                "citation_document_ids": ["doc-1"],
                "should_abstain": False,
                "abstained": False,
                "latency_ms": {"retrieval": 20, "generation": 40},
                "question": "local only question",
                "answer": "local only answer",
                "contexts": ["local only context"],
                "reference_answer": "local only reference",
            },
            {
                "id": "case-2",
                "expected_document_ids": ["doc-2"],
                "retrieved_document_ids": ["doc-3", "doc-2"],
                "citation_document_ids": ["doc-9"],
                "should_abstain": True,
                "abstained": False,
                "latency_ms": {"retrieval": 30, "generation": 60, "evaluator": 50},
            },
        ],
    }

    report = module.compute_report(payload, module.parse_cases(payload))

    assert report["metrics"]["retrieval"] == {
        "labeled_case_count": 2,
        "hit_at_1": 0.5,
        "hit_at_5": 1.0,
        "mrr": 0.75,
        "recall_at_5": 1.0,
    }
    assert report["metrics"]["citation_linkage"]["valid_rate"] == 0.5
    assert report["metrics"]["abstention"]["accuracy"] == 0.5
    assert report["metrics"]["latency_ms"]["retrieval"]["p95"] == 30
    _assert_no_local_only_fields(report)
    assert "local only" not in json.dumps(report)


def _write_dataset(path: Path, case: dict[str, object]) -> None:
    path.write_text(
        json.dumps({"dataset_version": "synthetic-v1", "cases": [case]}),
        encoding="utf-8",
    )


def test_cli_writes_a_metadata_only_report(tmp_path: Path) -> None:
    source = tmp_path / "local-input.json"
    output = tmp_path / "report.json"
    _write_dataset(
        source,
        {
            "id": "case-1",
            "expected_document_ids": ["doc-1"],
            "retrieved_document_ids": ["doc-1"],
            "citation_document_ids": ["doc-1"],
            "latency_ms": {},
            "question": "local only question",
            "answer": "local only answer",
            "contexts": ["local only context"],
            "reference_answer": "local only reference",
        },
    )

    result = run_cli("evaluate_chat_rag", "--input", str(source), "--output", str(output))

    assert result.returncode == 0, result.stderr
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["schema_version"] == "chat-rag-eval.v1"
    _assert_no_local_only_fields(report)
    assert "local only" not in output.read_text(encoding="utf-8")


def test_ragas_requires_text_fields_in_every_case() -> None:
    module = _module()
    payload = {
        "cases": [
            {
                "id": "case-1",
                "expected_document_ids": ["doc-1"],
                "retrieved_document_ids": ["doc-1"],
                "citation_document_ids": ["doc-1"],
            }
        ]
    }

    with pytest.raises(ValueError, match="--ragas requires"):
        module.run_ragas(module.parse_cases(payload))


def test_ragas_fails_clearly_when_the_optional_dependency_is_absent(tmp_path: Path) -> None:
    source = tmp_path / "local-input.json"
    _write_dataset(
        source,
        {
            "id": "case-1",
            "expected_document_ids": ["doc-1"],
            "retrieved_document_ids": ["doc-1"],
            "citation_document_ids": ["doc-1"],
            "question": "q",
            "answer": "a",
            "contexts": ["c"],
            "reference_answer": "r",
        },
    )

    result = run_cli(
        "evaluate_chat_rag",
        "--input",
        str(source),
        "--output",
        str(tmp_path / "report.json"),
        "--ragas",
    )

    if result.returncode == 0:
        pytest.skip("ragas is installed in this environment; the missing-dependency path is moot")
    assert "requires the optional ragas and datasets packages" in result.stderr
