import importlib
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from tests.unit.scripts.cli_harness import load_script, run_cli

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts/evaluate_chat_rag.py"
SAMPLE_FIXTURE = REPO_ROOT / "tests/fixtures/chat_rag/sample_chat_ragas_dataset.json"
LOCAL_ONLY_FIELDS = (
    "question",
    "answer",
    "contexts",
    "reference_answer",
    "ground_truth",
    "user_input",
    "response",
    "retrieved_contexts",
    "reference",
)


def _assert_no_local_only_fields(node: object) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            assert key not in LOCAL_ONLY_FIELDS, f"report leaked local-only field {key}"
            _assert_no_local_only_fields(value)
    elif isinstance(node, list):
        for item in node:
            _assert_no_local_only_fields(item)


def _module() -> Any:
    return load_script("evaluate_chat_rag")


def test_default_output_directory_uses_the_evaluation_workspace() -> None:
    assert _module().DEFAULT_OUTPUT_DIR == REPO_ROOT / "evaluations" / "CHAT-RAGAS" / "baselines"


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

    assert report["schema_version"] == "chat-rag-eval.v1"
    assert report["metrics"]["retrieval"] == {
        "labeled_case_count": 2,
        "hit_at_1": 0.5,
        "hit_at_5": 1.0,
        "mrr": 0.75,
        "recall_at_5": 1.0,
    }
    assert report["metrics"]["citation_linkage"]["valid_rate"] == 0.5
    assert report["metrics"]["abstention"]["accuracy"] == 0.5
    assert report["metrics"]["abstention"]["false_abstention_count"] == 0
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
        module.run_ragas(
            module.parse_cases(payload),
            evaluator_llm="fake-llm",
            evaluator_embeddings="fake-emb",
        )


def test_ragas_requires_explicit_evaluator_llm_and_embeddings() -> None:
    module = _module()
    payload = {
        "cases": [
            {
                "id": "case-1",
                "expected_document_ids": ["doc-1"],
                "retrieved_document_ids": ["doc-1"],
                "citation_document_ids": ["doc-1"],
                "question": "q",
                "answer": "a",
                "contexts": ["c"],
                "reference_answer": "r",
            }
        ]
    }
    cases = module.parse_cases(payload)

    with pytest.raises(
        ValueError, match="evaluator_llm and evaluator_embeddings must be explicitly provided"
    ):
        module.run_ragas(cases, evaluator_llm=None, evaluator_embeddings="fake-emb")

    with pytest.raises(
        ValueError, match="evaluator_llm and evaluator_embeddings must be explicitly provided"
    ):
        module.run_ragas(cases, evaluator_llm="fake-llm", evaluator_embeddings=None)


def test_ragas_fails_clearly_when_the_optional_dependency_is_absent() -> None:
    module = _module()
    payload = {
        "cases": [
            {
                "id": "case-1",
                "expected_document_ids": ["doc-1"],
                "retrieved_document_ids": ["doc-1"],
                "citation_document_ids": ["doc-1"],
                "question": "q",
                "answer": "a",
                "contexts": ["c"],
                "reference_answer": "r",
            }
        ]
    }
    cases = module.parse_cases(payload)
    orig_import = importlib.import_module

    def fail_ragas_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name in ("ragas", "datasets", "ragas.llms", "ragas.embeddings", "ragas.metrics"):
            raise ImportError(f"No module named '{name}'")
        return orig_import(name, *args, **kwargs)

    with patch("importlib.import_module", side_effect=fail_ragas_import):
        with pytest.raises(RuntimeError, match="requires the optional ragas and datasets"):
            module.run_ragas(cases, evaluator_llm="fake", evaluator_embeddings="fake")

        with pytest.raises(RuntimeError, match="requires the optional ragas and datasets"):
            module.init_evaluator("google")


def test_init_evaluator_missing_provider_packages_fail_fast() -> None:
    module = _module()
    orig_import = importlib.import_module

    # Mistral provider missing langchain_mistralai
    def fail_mistral_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "langchain_mistralai":
            raise ImportError("No module named 'langchain_mistralai'")
        return orig_import(name, *args, **kwargs)

    with patch("importlib.import_module", side_effect=fail_mistral_import):
        with pytest.raises(RuntimeError, match="requires langchain-mistralai"):
            module.init_evaluator("mistral")

    # OpenRouter provider missing langchain_openai
    def fail_openrouter_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "langchain_openai":
            raise ImportError("No module named 'langchain_openai'")
        return orig_import(name, *args, **kwargs)

    with patch("importlib.import_module", side_effect=fail_openrouter_import):
        with pytest.raises(RuntimeError, match="requires langchain-openai"):
            module.init_evaluator("openrouter")

    # Google provider missing langchain_google_genai
    def fail_google_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "langchain_google_genai":
            raise ImportError("No module named 'langchain_google_genai'")
        return orig_import(name, *args, **kwargs)

    with patch("importlib.import_module", side_effect=fail_google_import):
        with pytest.raises(RuntimeError, match="requires langchain-google-genai"):
            module.init_evaluator("google")

    # Unsupported provider raises ValueError
    with pytest.raises(ValueError, match="Unsupported evaluator provider"):
        module.init_evaluator("unsupported_provider")


def test_resolve_evaluator_models_defaults_and_overrides() -> None:
    module = _module()
    google_llm, google_emb = module.resolve_evaluator_models("google")
    assert "gemini" in google_llm
    assert "gemini-embedding" in google_emb

    mistral_llm, mistral_emb = module.resolve_evaluator_models("mistral")
    assert "mistral" in mistral_llm
    assert "mistral-embed" in mistral_emb

    openrouter_llm, openrouter_emb = module.resolve_evaluator_models("openrouter")
    assert isinstance(openrouter_llm, str) and bool(openrouter_llm)
    assert "gemini-embedding" in openrouter_emb

    custom_llm, custom_emb = module.resolve_evaluator_models(
        "google", model_override="custom-judge", embedding_override="custom-emb"
    )
    assert custom_llm == "custom-judge"
    assert custom_emb == "custom-emb"


def test_validate_evaluator_pairing_guards_against_self_preference_bias() -> None:
    module = _module()
    # Matching generator and judge models raises ValueError
    with pytest.raises(ValueError, match="Self-preference bias violation"):
        module.validate_evaluator_pairing(
            generator_model="gemini-2.0-flash", evaluator_model="gemini-2.0-flash"
        )

    # Case-insensitive match raises ValueError
    with pytest.raises(ValueError, match="Self-preference bias violation"):
        module.validate_evaluator_pairing(
            generator_model="Gemini-2.0-Flash", evaluator_model="gemini-2.0-flash"
        )

    # Prefix normalized match raises ValueError
    with pytest.raises(ValueError, match="Self-preference bias violation"):
        module.validate_evaluator_pairing(
            generator_model="models/gemini-2.0-flash",
            evaluator_model="google/gemini-2.0-flash",
        )

    # Multi-prefix normalized match raises ValueError
    with pytest.raises(ValueError, match="Self-preference bias violation"):
        module.validate_evaluator_pairing(
            generator_model="openrouter/google/gemini-2.0-flash",
            evaluator_model="models/gemini-2.0-flash",
        )

    # Prohibited throughput model raises ValueError
    with pytest.raises(ValueError, match="Throughput model 'gemini-3.5-flash-lite' is prohibited"):
        module.validate_evaluator_pairing(
            generator_model="mistral-large-latest",
            evaluator_model="gemini-3.5-flash-lite",
        )

    # Distinct models pass
    module.validate_evaluator_pairing(
        generator_model="gemini-2.0-flash", evaluator_model="mistral-large-latest"
    )

    # Allow same model flag bypasses check
    module.validate_evaluator_pairing(
        generator_model="gemini-2.0-flash",
        evaluator_model="gemini-2.0-flash",
        allow_same_model=True,
    )


def test_count_active_keys_and_compute_effective_workers() -> None:
    module = _module()

    # Active keys counting with varied suffixes and placeholder filtering
    fake_env = {
        "MISTRAL_API_KEY": "key1",
        "MISTRAL_API_KEY2": "key2",
        "MISTRAL_API_KEY3": "key3",
        "GEMINI_API_KEY_1": "gkey1",
        "GEMINI_API_KEY_2": "gkey2",
        "GEMINI_API_KEY_3": "replace-with-key",
    }
    assert module.count_active_keys("mistral", environ=fake_env) == 3
    assert module.count_active_keys("google", environ=fake_env) == 2
    assert module.count_active_keys("openrouter", environ={}) == 1

    # Effective worker bounds
    assert module.compute_effective_workers(
        requested_max_workers=1, active_key_count=3, ready_case_count=10
    ) == 1
    assert module.compute_effective_workers(
        requested_max_workers=3, active_key_count=3, ready_case_count=10
    ) == 3
    assert module.compute_effective_workers(
        requested_max_workers=5, active_key_count=3, ready_case_count=10
    ) == 3
    assert module.compute_effective_workers(
        requested_max_workers=5, active_key_count=5, ready_case_count=2
    ) == 2
    assert module.compute_effective_workers(
        requested_max_workers=10, active_key_count=10, ready_case_count=10
    ) == 5
    assert module.compute_effective_workers(
        requested_max_workers=None, active_key_count=3, ready_case_count=10
    ) == 3


def test_mock_ragas_evaluation_execution_and_schema() -> None:
    module = _module()
    payload = {
        "dataset_version": "synthetic-ragas-v1",
        "provider": "google",
        "model": "gemini-2.0-flash",
        "cases": [
            {
                "id": "case-1",
                "expected_document_ids": ["doc-1"],
                "retrieved_document_ids": ["doc-1"],
                "citation_document_ids": ["doc-1"],
                "should_abstain": False,
                "abstained": False,
                "latency_ms": {"retrieval": 10, "generation": 100, "evaluator": 200},
                "question": "What is the policy?",
                "answer": "The policy is 30 days.",
                "contexts": ["Policy duration is 30 days."],
                "reference_answer": "30 days.",
            },
            {
                "id": "case-2",
                "expected_document_ids": ["doc-2"],
                "retrieved_document_ids": ["doc-2"],
                "citation_document_ids": ["doc-2"],
                "should_abstain": False,
                "abstained": False,
                "latency_ms": {"retrieval": 15, "generation": 150, "evaluator": 250},
                "question": "What is the fee?",
                "answer": "The fee is $10.",
                "contexts": ["The fee is $10."],
                "reference_answer": "$10 fee.",
            },
        ],
    }

    cases = module.parse_cases(payload)

    mock_result = MagicMock()
    mock_result.scores = [
        {"faithfulness": 1.0, "answer_relevancy": 0.90},
        {"faithfulness": 0.92, "answer_relevancy": 0.86},
    ]

    mock_datasets = MagicMock()
    mock_ragas = MagicMock()
    mock_ragas.evaluate.return_value = mock_result
    mock_ragas_metrics = MagicMock()
    mock_ragas_metrics.faithfulness = "mock_faithfulness"
    mock_ragas_metrics.answer_relevancy = "mock_answer_relevancy"

    def fake_import_module(name: str) -> Any:
        if name == "datasets":
            return mock_datasets
        if name == "ragas":
            return mock_ragas
        if name == "ragas.metrics":
            return mock_ragas_metrics
        raise ImportError(f"No module named '{name}'")

    with patch("importlib.import_module", side_effect=fake_import_module):
        ragas_output = module.run_ragas(
            cases,
            evaluator_llm="mock_llm",
            evaluator_embeddings="mock_embeddings",
        )

    assert ragas_output["evaluated_case_count"] == 2
    assert ragas_output["faithfulness"] == 0.96
    assert ragas_output["answer_relevancy"] == 0.88
    assert ragas_output["failed_case_count"] == 0
    assert len(ragas_output["per_case_scores"]) == 2
    assert ragas_output["per_case_scores"][0] == {"faithfulness": 1.0, "answer_relevancy": 0.90}

    # Verify report assembly with RAGAS metrics
    report = module.compute_report(
        payload,
        cases,
        evaluator_provider="mistral",
        evaluator_model="mistral-large-latest",
        ragas_result=ragas_output,
        per_case_ragas_scores=ragas_output["per_case_scores"],
    )

    assert report["schema_version"] == "chat-rag-eval.v1"
    assert report["evaluator_provider"] == "mistral"
    assert report["evaluator_model"] == "mistral-large-latest"
    assert report["metrics"]["ragas"]["faithfulness"] == 0.96
    assert report["per_case"][0]["ragas_scores"] == {
        "faithfulness": 1.0,
        "answer_relevancy": 0.90,
    }
    assert report["per_case"][1]["ragas_scores"] == {
        "faithfulness": 0.92,
        "answer_relevancy": 0.86,
    }
    _assert_no_local_only_fields(report)


def test_ragas_handles_nan_and_failed_cases() -> None:
    module = _module()
    payload = {
        "dataset_version": "synthetic-ragas-nan-v1",
        "provider": "google",
        "model": "gemini-2.0-flash",
        "cases": [
            {
                "id": "case-1",
                "expected_document_ids": ["doc-1"],
                "retrieved_document_ids": ["doc-1"],
                "citation_document_ids": ["doc-1"],
                "question": "q1",
                "answer": "a1",
                "contexts": ["c1"],
                "reference_answer": "r1",
            },
            {
                "id": "case-2",
                "expected_document_ids": ["doc-2"],
                "retrieved_document_ids": ["doc-2"],
                "citation_document_ids": ["doc-2"],
                "question": "q2",
                "answer": "a2",
                "contexts": ["c2"],
                "reference_answer": "r2",
            },
        ],
    }
    cases = module.parse_cases(payload)

    mock_result = MagicMock()
    mock_result.scores = [
        {"faithfulness": 0.95, "answer_relevancy": 0.90},
        {"faithfulness": float("nan"), "answer_relevancy": float("nan")},
    ]

    mock_datasets = MagicMock()
    mock_ragas = MagicMock()
    mock_ragas.evaluate.return_value = mock_result
    mock_ragas_metrics = MagicMock()

    def fake_import_module(name: str) -> Any:
        if name == "datasets":
            return mock_datasets
        if name == "ragas":
            return mock_ragas
        if name == "ragas.metrics":
            return mock_ragas_metrics
        raise ImportError(f"No module named '{name}'")

    with patch("importlib.import_module", side_effect=fake_import_module):
        ragas_output = module.run_ragas(
            cases, evaluator_llm="mock_llm", evaluator_embeddings="mock_emb"
        )

    assert ragas_output["evaluated_case_count"] == 2
    assert ragas_output["failed_case_count"] == 1
    assert ragas_output["faithfulness"] == 0.95
    assert ragas_output["answer_relevancy"] == 0.90
    assert len(ragas_output["per_case_scores"]) == 2
    assert ragas_output["per_case_scores"][0] == {"faithfulness": 0.95, "answer_relevancy": 0.90}
    assert ragas_output["per_case_scores"][1] == {}


def test_get_langfuse_callback_behavior() -> None:
    module = _module()

    # Without keys in environment, returns empty list
    with patch.dict("os.environ", {"LANGFUSE_PUBLIC_KEY": "", "LANGFUSE_SECRET_KEY": ""}):
        assert module.get_langfuse_callback() == []

    # With placeholder keys in environment, returns empty list
    with patch.dict(
        "os.environ",
        {"LANGFUSE_PUBLIC_KEY": "replace-with-pk", "LANGFUSE_SECRET_KEY": "replace-with-sk"},
    ):
        assert module.get_langfuse_callback() == []

    # With valid keys in environment and Langfuse v3 available
    mock_cb_instance = MagicMock()
    mock_langchain_mod = MagicMock()
    mock_langchain_mod.CallbackHandler.return_value = mock_cb_instance

    with patch.dict("os.environ", {"LANGFUSE_PUBLIC_KEY": "pk-1", "LANGFUSE_SECRET_KEY": "sk-1"}):
        with patch("importlib.import_module", return_value=mock_langchain_mod):
            callbacks = module.get_langfuse_callback()
            assert len(callbacks) == 1
            assert callbacks[0] == mock_cb_instance


def test_cli_execution_with_mocked_ragas_and_no_save_per_case_scores(tmp_path: Path) -> None:
    module = _module()
    source = tmp_path / "chat_ragas_input.json"
    output = tmp_path / "chat_ragas_report.json"
    payload = {
        "dataset_version": "synthetic-v1",
        "provider": "google",
        "model": "gemini-2.0-flash",
        "cases": [
            {
                "id": "case-1",
                "expected_document_ids": ["doc-1"],
                "retrieved_document_ids": ["doc-1"],
                "citation_document_ids": ["doc-1"],
                "should_abstain": False,
                "abstained": False,
                "latency_ms": {"retrieval": 20, "generation": 40, "evaluator": 100},
                "question": "local question 1",
                "answer": "local answer 1",
                "contexts": ["local context 1"],
                "reference_answer": "local reference 1",
            }
        ],
    }
    source.write_text(json.dumps(payload), encoding="utf-8")

    mock_ragas_res = {
        "evaluated_case_count": 1,
        "failed_case_count": 0,
        "faithfulness": 0.98,
        "answer_relevancy": 0.95,
        "per_case_scores": [{"faithfulness": 0.98, "answer_relevancy": 0.95}],
    }

    with (
        patch.object(module, "init_evaluator", return_value=("fake_llm", "fake_emb")),
        patch.object(module, "run_ragas", return_value=mock_ragas_res),
    ):
        exit_code = module.main(
            [
                "--input",
                str(source),
                "--output",
                str(output),
                "--ragas",
                "--evaluator-provider",
                "mistral",
                "--evaluator-model",
                "mistral-large-latest",
                "--max-workers",
                "3",
            ]
        )

    assert exit_code == 0
    assert output.exists()
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["schema_version"] == "chat-rag-eval.v1"
    assert report["evaluator_provider"] == "mistral"
    assert report["evaluator_model"] == "mistral-large-latest"
    assert report["evaluator_workers"]["requested_max_workers"] == 3
    assert report["evaluator_workers"]["effective_workers"] == 1
    assert report["metrics"]["ragas"]["faithfulness"] == 0.98
    assert report["per_case"][0]["ragas_scores"] == {
        "faithfulness": 0.98,
        "answer_relevancy": 0.95,
    }
    _assert_no_local_only_fields(report)

    # Test --no-save-per-case-scores omits ragas_scores from per_case
    with (
        patch.object(module, "init_evaluator", return_value=("fake_llm", "fake_emb")),
        patch.object(module, "run_ragas", return_value=mock_ragas_res),
    ):
        exit_code = module.main(
            [
                "--input",
                str(source),
                "--output",
                str(output),
                "--ragas",
                "--evaluator-provider",
                "mistral",
                "--evaluator-model",
                "mistral-large-latest",
                "--no-save-per-case-scores",
            ]
        )

    assert exit_code == 0
    report_no_per_case = json.loads(output.read_text(encoding="utf-8"))
    assert "ragas_scores" not in report_no_per_case["per_case"][0]
    _assert_no_local_only_fields(report_no_per_case)


def test_parse_cases_validation_errors() -> None:
    module = _module()

    # Empty cases
    with pytest.raises(ValueError, match="cases must be a non-empty list"):
        module.parse_cases({"cases": []})

    # Duplicate case IDs
    with pytest.raises(ValueError, match="duplicate case id"):
        module.parse_cases(
            {
                "cases": [
                    {"id": "dup-1", "expected_document_ids": []},
                    {"id": "dup-1", "expected_document_ids": []},
                ]
            }
        )

    # Non-object case
    with pytest.raises(ValueError, match="each case must be an object"):
        module.parse_cases({"cases": ["not-a-dict"]})

    # Invalid latency format
    with pytest.raises(ValueError, match="latency_ms must be an object"):
        module.parse_cases({"cases": [{"id": "c1", "latency_ms": "invalid"}]})

    # Invalid RAGAS fields (non-string question)
    with pytest.raises(ValueError, match="RAGAS cases require string"):
        module.parse_cases(
            {
                "cases": [
                    {
                        "id": "c1",
                        "question": 123,
                        "answer": "a",
                        "contexts": ["c"],
                        "reference_answer": "r",
                    }
                ]
            }
        )


def test_sample_chat_ragas_fixture_is_valid() -> None:
    assert SAMPLE_FIXTURE.exists()
    payload = json.loads(SAMPLE_FIXTURE.read_text(encoding="utf-8"))
    module = _module()
    cases = module.parse_cases(payload)
    assert len(cases) == 3

    report = module.compute_report(payload, cases)
    assert report["schema_version"] == "chat-rag-eval.v1"
    assert report["case_count"] == 3
    assert report["metrics"]["citation_linkage"]["valid_rate"] == 1.0
    assert report["metrics"]["abstention"]["accuracy"] == 1.0
    _assert_no_local_only_fields(report)


def test_ragas_scores_extracted_from_to_pandas_or_custom_sequence() -> None:
    module = _module()
    payload = {
        "dataset_version": "synthetic-v1",
        "cases": [
            {
                "id": "c1",
                "question": "q1",
                "answer": "a1",
                "contexts": ["c1"],
                "reference_answer": "r1",
            }
        ],
    }
    cases = module.parse_cases(payload)

    # Result without .scores, but with .to_pandas()
    class FakeDataFrame:
        def to_dict(self, orient: str = "records") -> list[dict[str, Any]]:
            return [{"faithfulness": 0.95, "answer_relevancy": 0.88}]

    mock_result_df = MagicMock()
    mock_result_df.scores = None
    mock_result_df.to_pandas.return_value = FakeDataFrame()

    mock_ragas = MagicMock()
    mock_ragas.evaluate.return_value = mock_result_df
    mock_ragas_metrics = MagicMock()

    with patch(
        "importlib.import_module",
        side_effect=lambda name: mock_ragas if name == "ragas" else mock_ragas_metrics,
    ):
        output = module.run_ragas(cases, evaluator_llm="fake", evaluator_embeddings="fake")
        assert output["evaluated_case_count"] == 1
        assert output["faithfulness"] == 0.95
        assert output["answer_relevancy"] == 0.88
        assert output["failed_case_count"] == 0
        assert output["per_case_scores"] == [{"faithfulness": 0.95, "answer_relevancy": 0.88}]


def test_ragas_handles_total_failure_all_nan() -> None:
    module = _module()
    payload = {
        "dataset_version": "all-fail-v1",
        "cases": [
            {
                "id": "c1",
                "question": "q1",
                "answer": "a1",
                "contexts": ["c1"],
                "reference_answer": "r1",
            }
        ],
    }
    cases = module.parse_cases(payload)

    mock_result = MagicMock()
    mock_result.scores = [{"faithfulness": float("nan"), "answer_relevancy": float("nan")}]
    mock_ragas = MagicMock()
    mock_ragas.evaluate.return_value = mock_result
    mock_ragas_metrics = MagicMock()

    with patch(
        "importlib.import_module",
        side_effect=lambda name: mock_ragas if name == "ragas" else mock_ragas_metrics,
    ):
        output = module.run_ragas(cases, evaluator_llm="fake", evaluator_embeddings="fake")
        assert output["evaluated_case_count"] == 1
        assert output["failed_case_count"] == 1
        assert output["faithfulness"] is None
        assert output["answer_relevancy"] is None
        assert output["per_case_scores"] == [{}]

    report = module.compute_report(payload, cases, ragas_result=output)
    assert report["metrics"]["ragas"]["faithfulness"] is None
    assert report["metrics"]["ragas"]["failed_case_count"] == 1
    _assert_no_local_only_fields(report)


def test_cli_validates_max_workers_positive_integer(tmp_path: Path) -> None:
    module = _module()
    source = tmp_path / "input.json"
    _write_dataset(
        source,
        {
            "id": "c1",
            "expected_document_ids": ["doc-1"],
            "retrieved_document_ids": ["doc-1"],
            "citation_document_ids": ["doc-1"],
        },
    )

    with pytest.raises(SystemExit):
        module.main(["--input", str(source), "--max-workers", "0"])

    with pytest.raises(SystemExit):
        module.main(["--input", str(source), "--max-workers", "-2"])


def test_count_active_keys_and_resolve_api_key_google_and_openrouter() -> None:
    module = _module()

    # Google fallback to GOOGLE_API_KEY
    env_google = {"GOOGLE_API_KEY": "google-key-val"}
    assert module.count_active_keys("google", environ=env_google) == 1
    assert module._resolve_api_key("google", environ=env_google) == "google-key-val"

    # OpenRouter placeholder filtering
    env_openrouter_placeholder = {"OPENROUTER_API_KEY": "replace-with-openrouter-key"}
    assert module._resolve_api_key("openrouter", environ=env_openrouter_placeholder) == ""
    assert module.count_active_keys("openrouter", environ=env_openrouter_placeholder) == 1


def test_cli_execution_with_allow_same_model(tmp_path: Path) -> None:
    module = _module()
    source = tmp_path / "same_model_input.json"
    output = tmp_path / "same_model_report.json"
    payload = {
        "dataset_version": "synthetic-v1",
        "provider": "google",
        "model": "gemini-2.0-flash",
        "cases": [
            {
                "id": "c1",
                "expected_document_ids": ["doc-1"],
                "retrieved_document_ids": ["doc-1"],
                "citation_document_ids": ["doc-1"],
                "question": "q",
                "answer": "a",
                "contexts": ["c"],
                "reference_answer": "r",
            }
        ],
    }
    source.write_text(json.dumps(payload), encoding="utf-8")

    mock_ragas_res = {
        "evaluated_case_count": 1,
        "failed_case_count": 0,
        "faithfulness": 1.0,
        "answer_relevancy": 1.0,
        "per_case_scores": [{"faithfulness": 1.0, "answer_relevancy": 1.0}],
    }

    with (
        patch.object(module, "init_evaluator", return_value=("fake_llm", "fake_emb")),
        patch.object(module, "run_ragas", return_value=mock_ragas_res),
    ):
        exit_code = module.main(
            [
                "--input",
                str(source),
                "--output",
                str(output),
                "--ragas",
                "--evaluator-provider",
                "google",
                "--evaluator-model",
                "gemini-2.0-flash",
                "--allow-same-model",
            ]
        )
    assert exit_code == 0
    assert output.exists()
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["evaluator_model"] == "gemini-2.0-flash"
    assert report["model"] == "gemini-2.0-flash"
    _assert_no_local_only_fields(report)


def test_ragas_scores_extracted_from_column_oriented_mapping() -> None:
    module = _module()
    payload = {
        "dataset_version": "synthetic-v1",
        "cases": [
            {
                "id": "c1",
                "question": "q1",
                "answer": "a1",
                "contexts": ["c1"],
                "reference_answer": "r1",
            },
            {
                "id": "c2",
                "question": "q2",
                "answer": "a2",
                "contexts": ["c2"],
                "reference_answer": "r2",
            },
        ],
    }
    cases = module.parse_cases(payload)

    mock_result = MagicMock()
    mock_result.scores = {
        "faithfulness": [1.0, 0.90],
        "answer_relevancy": [0.85, 0.95],
    }

    mock_ragas = MagicMock()
    mock_ragas.evaluate.return_value = mock_result
    mock_ragas_metrics = MagicMock()

    with patch(
        "importlib.import_module",
        side_effect=lambda name: mock_ragas if name == "ragas" else mock_ragas_metrics,
    ):
        output = module.run_ragas(cases, evaluator_llm="fake", evaluator_embeddings="fake")
        assert output["evaluated_case_count"] == 2
        assert output["faithfulness"] == 0.95
        assert output["answer_relevancy"] == 0.90
        assert output["failed_case_count"] == 0
        assert len(output["per_case_scores"]) == 2
        assert output["per_case_scores"][0] == {"faithfulness": 1.0, "answer_relevancy": 0.85}
        assert output["per_case_scores"][1] == {"faithfulness": 0.90, "answer_relevancy": 0.95}


def test_ragas_handles_aggregate_only_result_without_per_case_scores() -> None:
    module = _module()
    payload = {
        "dataset_version": "synthetic-v1",
        "cases": [
            {
                "id": "c1",
                "question": "q1",
                "answer": "a1",
                "contexts": ["c1"],
                "reference_answer": "r1",
            }
        ],
    }
    cases = module.parse_cases(payload)

    # Result that is just a dictionary of aggregates (no .scores attribute)
    mock_result = {"faithfulness": 0.96, "answer_relevancy": 0.88}

    mock_ragas = MagicMock()
    mock_ragas.evaluate.return_value = mock_result
    mock_ragas_metrics = MagicMock()

    with patch(
        "importlib.import_module",
        side_effect=lambda name: mock_ragas if name == "ragas" else mock_ragas_metrics,
    ):
        output = module.run_ragas(cases, evaluator_llm="fake", evaluator_embeddings="fake")
        assert output["evaluated_case_count"] == 1
        assert output["faithfulness"] == 0.96
        assert output["answer_relevancy"] == 0.88
        assert output["failed_case_count"] == 0
        assert output["per_case_scores"] == [{}]


def test_resolve_api_key_handles_high_index_keys() -> None:
    module = _module()
    env = {
        "GEMINI_API_KEY_25": "gemini-key-25",
        "MISTRAL_API_KEY_30": "mistral-key-30",
        "OPENROUTER_API_KEY_15": "openrouter-key-15",
    }
    assert module._resolve_api_key("google", environ=env) == "gemini-key-25"
    assert module._resolve_api_key("mistral", environ=env) == "mistral-key-30"
    assert module._resolve_api_key("openrouter", environ=env) == "openrouter-key-15"


def test_run_ragas_falls_back_when_run_config_raises_typeerror() -> None:
    module = _module()
    payload = {
        "dataset_version": "synthetic-v1",
        "cases": [
            {
                "id": "c1",
                "question": "q1",
                "answer": "a1",
                "contexts": ["c1"],
                "reference_answer": "r1",
            }
        ],
    }
    cases = module.parse_cases(payload)

    mock_result = MagicMock()
    mock_result.scores = [{"faithfulness": 0.99, "answer_relevancy": 0.97}]

    # Mock evaluate that raises TypeError when run_config is passed, but succeeds without it
    def fake_evaluate(**kwargs: Any) -> Any:
        if "run_config" in kwargs:
            raise TypeError("evaluate() got an unexpected keyword argument 'run_config'")
        return mock_result

    mock_ragas = MagicMock()
    mock_ragas.evaluate.side_effect = fake_evaluate
    mock_ragas.RunConfig = MagicMock()
    mock_ragas_metrics = MagicMock()

    with patch(
        "importlib.import_module",
        side_effect=lambda name: mock_ragas if name == "ragas" else mock_ragas_metrics,
    ):
        output = module.run_ragas(cases, evaluator_llm="fake", evaluator_embeddings="fake")
        assert output["evaluated_case_count"] == 1
        assert output["faithfulness"] == 0.99
        assert output["answer_relevancy"] == 0.97
        assert output["failed_case_count"] == 0



