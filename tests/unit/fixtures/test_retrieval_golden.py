"""Every SPEC-rag §5.2 validation rule fires (T-4).

Rules 1-3 run on inline JSON alone. Rules 4-6 run against the real corpus
under ``data/extracted``; rule 5 is the anti-rot guard that stops the golden
set from silently scoring 0.0 after a re-chunk.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
CORPUS_DIR = REPO_ROOT / "data" / "extracted"
FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "rag" / "retrieval_golden.json"

_LOADER_PATH = Path(__file__).resolve().parents[2] / "fixtures" / "rag" / "loader.py"
_spec = importlib.util.spec_from_file_location("retrieval_fixture_loader", _LOADER_PATH)
assert _spec is not None and _spec.loader is not None
loader = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = loader  # dataclasses resolve annotations via sys.modules
_spec.loader.exec_module(loader)

RetrievalFixtureError = loader.RetrievalFixtureError


def _write(tmp_path: Path, payload: object) -> Path:
    target = tmp_path / "retrieval_golden.json"
    target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return target


def _valid_case(**overrides: object) -> dict[str, object]:
    case: dict[str, object] = {
        "id": "q-001",
        "query": "Thủ tục cấp lại CCCD gồm những bước nào?",
        "probe": "mixed",
        "expected_document_ids": ["cap_lai_cccd"],
        "expected_sections": [],
        "email_body": None,
        "notes": "schema fixture",
    }
    case.update(overrides)
    return case


def _corpus_sections() -> dict[str, list[str]]:
    from cowork_agent.integrations.rag.knowledge_base import load_corpus

    return {
        document.document_id: list(
            dict.fromkeys(
                chunk.section for chunk in document.chunks if chunk.section is not None
            )
        )
        for document in load_corpus(CORPUS_DIR, tenant_id="local")
    }


def _covering_cases() -> list[dict[str, object]]:
    """A set that satisfies all six rules against the real corpus."""
    cases: list[dict[str, object]] = []
    for document_id, sections in sorted(_corpus_sections().items()):
        for probe in ("lexical", "semantic", "mixed"):
            cases.append(
                _valid_case(
                    id=f"q-{len(cases) + 1:03d}",
                    probe=probe,
                    expected_document_ids=[document_id],
                    expected_sections=sections[:1],
                )
            )
    cases.append(
        _valid_case(
            id=f"q-{len(cases) + 1:03d}",
            probe="unanswerable",
            expected_document_ids=[],
            expected_sections=[],
        )
    )
    return cases


# --- schema-only rules (no corpus) ----------------------------------------


def test_schema_rules_pass_without_a_corpus(tmp_path: Path) -> None:
    cases = loader.load_retrieval_golden(_write(tmp_path, [_valid_case()]))
    assert len(cases) == 1
    assert cases[0].probe is loader.Probe.MIXED
    assert cases[0].expected_document_ids == ("cap_lai_cccd",)
    assert cases[0].email_body is None


def test_rule_1_rejects_duplicate_ids(tmp_path: Path) -> None:
    with pytest.raises(RetrievalFixtureError, match="duplicate"):
        loader.load_retrieval_golden(_write(tmp_path, [_valid_case(), _valid_case()]))


def test_rule_1_rejects_malformed_id(tmp_path: Path) -> None:
    with pytest.raises(RetrievalFixtureError, match="q-NNN"):
        loader.load_retrieval_golden(_write(tmp_path, [_valid_case(id="query-1")]))


def test_rule_2_rejects_unknown_probe(tmp_path: Path) -> None:
    with pytest.raises(RetrievalFixtureError, match="probe"):
        loader.load_retrieval_golden(_write(tmp_path, [_valid_case(probe="keyword")]))


def test_rule_3_rejects_unanswerable_with_expected_documents(tmp_path: Path) -> None:
    case = _valid_case(probe="unanswerable", expected_document_ids=["cap_lai_cccd"])
    with pytest.raises(RetrievalFixtureError, match="empty if and only if"):
        loader.load_retrieval_golden(_write(tmp_path, [case]))


def test_rule_3_rejects_answerable_without_expected_documents(tmp_path: Path) -> None:
    case = _valid_case(probe="mixed", expected_document_ids=[])
    with pytest.raises(RetrievalFixtureError, match="empty if and only if"):
        loader.load_retrieval_golden(_write(tmp_path, [case]))


def test_missing_required_field_is_rejected(tmp_path: Path) -> None:
    case = _valid_case()
    del case["query"]
    with pytest.raises(RetrievalFixtureError, match="query"):
        loader.load_retrieval_golden(_write(tmp_path, [case]))


def test_missing_email_body_key_is_rejected(tmp_path: Path) -> None:
    case = _valid_case()
    del case["email_body"]
    with pytest.raises(RetrievalFixtureError, match="email_body"):
        loader.load_retrieval_golden(_write(tmp_path, [case]))


def test_notes_are_optional(tmp_path: Path) -> None:
    case = _valid_case()
    del case["notes"]
    assert loader.load_retrieval_golden(_write(tmp_path, [case]))[0].notes is None


def test_non_array_root_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(RetrievalFixtureError, match="JSON array"):
        loader.load_retrieval_golden(_write(tmp_path, {"id": "q-001"}))


def test_invalid_json_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "retrieval_golden.json"
    target.write_text("[{", encoding="utf-8")
    with pytest.raises(RetrievalFixtureError, match="invalid JSON"):
        loader.load_retrieval_golden(target)


# --- corpus-backed rules --------------------------------------------------


def test_repository_fixture_requires_one_hundred_cases() -> None:
    with pytest.raises(RetrievalFixtureError, match="exactly 100"):
        loader._validate_repository_fixture_contract((), FIXTURE_PATH)


def test_real_fixture_has_expanded_distribution() -> None:
    cases = loader.load_retrieval_golden(corpus_dir=CORPUS_DIR)
    assert len(cases) == 100
    assert sum(case.probe is loader.Probe.UNANSWERABLE for case in cases) == 12
    assert all(case.email_body is None for case in cases[32:])


def test_rule_4_rejects_unknown_document_id(tmp_path: Path) -> None:
    case = _valid_case(expected_document_ids=["dang_ky_tam_tru"])
    with pytest.raises(RetrievalFixtureError, match="unknown document id"):
        loader.load_retrieval_golden(_write(tmp_path, [case]), corpus_dir=CORPUS_DIR)


def test_rule_5_rejects_section_absent_from_the_corpus(tmp_path: Path) -> None:
    """The anti-rot guard: a stale section label fails loudly, not silently."""
    case = _valid_case(expected_sections=["Quy trình đã bị đổi tên sau khi re-chunk"])
    with pytest.raises(RetrievalFixtureError, match="the golden set is stale"):
        loader.load_retrieval_golden(_write(tmp_path, [case]), corpus_dir=CORPUS_DIR)


def test_rule_5_rejects_a_section_belonging_to_another_document(tmp_path: Path) -> None:
    sections = _corpus_sections()
    borrowed = sections["dang_ky_xe"][0]
    assert borrowed not in sections["cap_lai_cccd"]
    case = _valid_case(expected_document_ids=["cap_lai_cccd"], expected_sections=[borrowed])
    with pytest.raises(RetrievalFixtureError, match="is not emitted by load_corpus"):
        loader.load_retrieval_golden(_write(tmp_path, [case]), corpus_dir=CORPUS_DIR)


def test_rule_5_accepts_a_real_section(tmp_path: Path) -> None:
    cases = _covering_cases()
    case = next(case for case in cases if case["expected_document_ids"] == ["cap_lai_cccd"])
    section = _corpus_sections()["cap_lai_cccd"][0]
    case["expected_sections"] = [section]
    loaded = loader.load_retrieval_golden(_write(tmp_path, cases), corpus_dir=CORPUS_DIR)
    assert loaded[cases.index(case)].expected_sections == (section,)


def test_rule_6_rejects_missing_probe_coverage(tmp_path: Path) -> None:
    with pytest.raises(RetrievalFixtureError, match="missing probe coverage"):
        loader.load_retrieval_golden(_write(tmp_path, [_valid_case()]), corpus_dir=CORPUS_DIR)


def test_rule_6_names_the_uncovered_document_and_probe(tmp_path: Path) -> None:
    cases = [case for case in _covering_cases() if case["probe"] != "semantic"]
    with pytest.raises(RetrievalFixtureError, match="cap_lai_cccd:semantic"):
        loader.load_retrieval_golden(_write(tmp_path, cases), corpus_dir=CORPUS_DIR)


def test_rule_6_requires_an_unanswerable_case(tmp_path: Path) -> None:
    cases = [case for case in _covering_cases() if case["probe"] != "unanswerable"]
    with pytest.raises(RetrievalFixtureError, match="no unanswerable case"):
        loader.load_retrieval_golden(_write(tmp_path, cases), corpus_dir=CORPUS_DIR)


def test_full_coverage_passes_every_rule(tmp_path: Path) -> None:
    cases = _covering_cases()
    loaded = loader.load_retrieval_golden(_write(tmp_path, cases), corpus_dir=CORPUS_DIR)
    assert len(loaded) == len(cases)
    probes = {case.probe for case in loaded}
    assert probes == set(loader.Probe)
