import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "build_evaluation_dashboard.py"


def load_module():
    spec = importlib.util.spec_from_file_location("evaluation_dashboard", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_default_paths_use_the_evaluation_workspace() -> None:
    module = load_module()

    assert module.DEFAULT_INPUT_DIR == REPO_ROOT / "evaluations" / "baselines"
    assert module.DEFAULT_OUTPUT == REPO_ROOT / "evaluations" / "dashboard.md"


def _report(*, cases: int, documents: int, chunks: int, embedder: str, retriever: str):
    return {
        "generated_at": "2026-08-13T00:00:00Z",
        "embedder": embedder,
        "retriever": retriever,
        "reranker": None,
        "case_count": cases,
        "corpus": {"document_count": documents, "chunk_count": chunks},
        "section_level": {"mrr": 0.5, "recall_at_5": 0.7},
        "by_probe": {"semantic": {"section_level": {"mrr": 0.25}}},
        "abstention": {"abstention_rate": 0.0},
        "latency_ms": {"p50": 12, "p95": 25},
    }


def test_load_reports_separates_current_and_historical_evidence(tmp_path: Path) -> None:
    module = load_module()
    (tmp_path / "retrieval-eval-2026-08-08-gemini-dense.json").write_text(
        json.dumps(_report(cases=32, documents=6, chunks=36, embedder="gemini", retriever="dense")),
        encoding="utf-8",
    )
    (tmp_path / "retrieval-eval-2026-08-13-hashing-dense.json").write_text(
        json.dumps(
            _report(cases=100, documents=17, chunks=1066, embedder="hashing", retriever="dense")
        ),
        encoding="utf-8",
    )
    (tmp_path / "not-an-evaluation.json").write_text("{}", encoding="utf-8")

    reports = module.load_reports(tmp_path)

    assert [report.scope for report in reports] == ["historical", "current"]
    assert reports[0].semantic_evidence is True
    assert reports[1].semantic_evidence is False


def test_render_dashboard_keeps_component_latency_gaps_visible(tmp_path: Path) -> None:
    module = load_module()
    target = tmp_path / "retrieval-eval-2026-08-13-hashing-hybrid.json"
    target.write_text(
        json.dumps(
            _report(cases=100, documents=17, chunks=1066, embedder="hashing", retriever="hybrid")
        ),
        encoding="utf-8",
    )

    dashboard = module.render_dashboard(module.load_reports(tmp_path))

    assert "Retrieval Evaluation Dashboard" in dashboard
    assert "Current Corpus Evidence" in dashboard
    assert "mechanical-only" in dashboard
    assert "Per-component timing is not emitted" in dashboard
    assert "Hybrid retrieval" in dashboard
    assert "RAG request" in dashboard


def test_render_dashboard_describes_only_currently_reported_retrievers(tmp_path: Path) -> None:
    module = load_module()
    target = tmp_path / "retrieval-eval-2026-08-17-hashing-dense.json"
    target.write_text(
        json.dumps(
            _report(cases=100, documents=17, chunks=1069, embedder="hashing", retriever="dense")
        ),
        encoding="utf-8",
    )

    dashboard = module.render_dashboard(module.load_reports(tmp_path))

    assert "**Current retrieval coverage:** dense." in dashboard
    assert "current report set exercises dense, Turbovec, and hybrid" not in dashboard


def test_render_dashboard_warns_about_chunking_cohorts(tmp_path: Path) -> None:
    module = load_module()
    for chunks in (1043, 1066):
        (tmp_path / f"retrieval-eval-2026-08-13-hashing-dense-{chunks}.json").write_text(
            json.dumps(
                _report(
                    cases=100,
                    documents=17,
                    chunks=chunks,
                    embedder="hashing",
                    retriever="dense",
                )
            ),
            encoding="utf-8",
        )

    dashboard = module.render_dashboard(module.load_reports(tmp_path))

    assert "1043, 1066 chunks" in dashboard
    assert "compare only within one cohort" in dashboard

def test_main_writes_dashboard_from_reports(tmp_path: Path) -> None:
    module = load_module()
    input_dir = tmp_path / "baselines"
    input_dir.mkdir()
    (input_dir / "retrieval-eval-2026-08-13-hashing-dense.json").write_text(
        json.dumps(
            _report(cases=100, documents=17, chunks=1066, embedder="hashing", retriever="dense")
        ),
        encoding="utf-8",
    )
    output = tmp_path / "dashboard.md"

    assert module.main(["--input-dir", str(input_dir), "--output", str(output)]) == 0
    assert output.exists()
    assert "retrieval-eval-2026-08-13-hashing-dense.json" in output.read_text(encoding="utf-8")
