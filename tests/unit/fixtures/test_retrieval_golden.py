"""Every SPEC-rag §5.2 validation rule fires (T-4).

Rules 1-3 run on inline JSON alone. Rules 4-6 run against the real corpus
under ``data/extracted``; rule 5 is the anti-rot guard that stops the golden
set from silently scoring 0.0 after a re-chunk.
"""

import hashlib
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

# Kept as a second, independent copy of the loader's constant: both must be
# edited for a legacy-case change to land, which is what makes the rotation
# deliberate. See the loader for why this value moved.
LEGACY_CASE_SNAPSHOT_SHA256 = "075281fd462ea8c2e3faf267daf2ef0f0bef6e2c7ebcd9cae7db3c9ee77515dd"


def _write(tmp_path: Path, payload: object) -> Path:
    target = tmp_path / "retrieval_golden.json"
    target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return target


def _repository_payload() -> list[dict[str, object]]:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert isinstance(payload, list)
    return payload


def _load_as_repository_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, payload: object
) -> None:
    target = _write(tmp_path, payload)
    monkeypatch.setattr(loader, "DEFAULT_FIXTURE_PATH", target)
    loader.load_retrieval_golden()


def _valid_case(**overrides: object) -> dict[str, object]:
    case: dict[str, object] = {
        "id": "q-001",
        "query": "Thủ tục cấp lại CCCD gồm những bước nào?",
        "probe": "mixed",
        "expected_document_ids": ["cap-lai-cccd"],
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
            dict.fromkeys(chunk.section for chunk in document.chunks if chunk.section is not None)
        )
        for document in load_corpus(CORPUS_DIR)
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
    assert cases[0].expected_document_ids == ("cap-lai-cccd",)
    assert cases[0].email_body is None

    case_no_notes = _valid_case()
    del case_no_notes["notes"]
    assert loader.load_retrieval_golden(_write(tmp_path, [case_no_notes]))[0].notes is None


def test_schema_rules_rejection_suite(tmp_path: Path) -> None:
    # Rule 1: duplicate ids
    with pytest.raises(RetrievalFixtureError, match="duplicate"):
        loader.load_retrieval_golden(_write(tmp_path, [_valid_case(), _valid_case()]))

    # Rule 1: malformed id
    with pytest.raises(RetrievalFixtureError, match="q-NNN"):
        loader.load_retrieval_golden(_write(tmp_path, [_valid_case(id="query-1")]))

    # Rule 2: unknown probe
    with pytest.raises(RetrievalFixtureError, match="probe"):
        loader.load_retrieval_golden(_write(tmp_path, [_valid_case(probe="keyword")]))

    # Rule 3: unanswerable with expected docs
    with pytest.raises(RetrievalFixtureError, match="empty if and only if"):
        loader.load_retrieval_golden(
            _write(
                tmp_path,
                [_valid_case(probe="unanswerable", expected_document_ids=["cap-lai-cccd"])],
            )
        )

    # Rule 3: answerable without expected docs
    with pytest.raises(RetrievalFixtureError, match="empty if and only if"):
        loader.load_retrieval_golden(
            _write(tmp_path, [_valid_case(probe="mixed", expected_document_ids=[])])
        )

    # Missing required query
    bad_query = _valid_case()
    del bad_query["query"]
    with pytest.raises(RetrievalFixtureError, match="query"):
        loader.load_retrieval_golden(_write(tmp_path, [bad_query]))

    # Missing email body
    bad_body = _valid_case()
    del bad_body["email_body"]
    with pytest.raises(RetrievalFixtureError, match="email_body"):
        loader.load_retrieval_golden(_write(tmp_path, [bad_body]))

    # Non array root
    with pytest.raises(RetrievalFixtureError, match="JSON array"):
        loader.load_retrieval_golden(_write(tmp_path, {"id": "q-001"}))

    # Invalid json
    target = tmp_path / "retrieval_golden_bad.json"
    target.write_text("[{", encoding="utf-8")
    with pytest.raises(RetrievalFixtureError, match="invalid JSON"):
        loader.load_retrieval_golden(target)


# --- corpus-backed rules --------------------------------------------------


def test_repository_fixture_structure_and_legacy_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _repository_payload()
    # 100 cases requirement
    with pytest.raises(RetrievalFixtureError, match="exactly 100"):
        _load_as_repository_fixture(tmp_path, monkeypatch, payload[:-1])

    # Legacy snapshot hash
    serialized = json.dumps(
        payload[:32],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert hashlib.sha256(serialized).hexdigest() == LEGACY_CASE_SNAPSHOT_SHA256

    # Changed legacy object rejected
    mutated = _repository_payload()
    mutated[0]["query"] = "changed legacy query"
    with pytest.raises(RetrievalFixtureError, match="legacy case snapshot"):
        _load_as_repository_fixture(tmp_path, monkeypatch, mutated)

    # Contiguous IDs
    mutated_order = _repository_payload()
    mutated_order[32]["id"], mutated_order[33]["id"] = (
        mutated_order[33]["id"],
        mutated_order[32]["id"],
    )
    with pytest.raises(RetrievalFixtureError, match="contiguous IDs q-001 through q-100"):
        _load_as_repository_fixture(tmp_path, monkeypatch, mutated_order)

    # Exact appended allocation
    mutated_alloc = _repository_payload()
    mutated_alloc[32]["expected_document_ids"] = ["dang-ky-tam-tru"]
    with pytest.raises(RetrievalFixtureError, match="40 large-law cases"):
        _load_as_repository_fixture(tmp_path, monkeypatch, mutated_alloc)

    # Null email bodies on appended cases
    mutated_email = _repository_payload()
    mutated_email[32]["email_body"] = "synthetic but not an email fixture"
    with pytest.raises(RetrievalFixtureError, match="email_body: null"):
        _load_as_repository_fixture(tmp_path, monkeypatch, mutated_email)


def test_real_fixture_expanded_distribution() -> None:
    cases = loader.load_retrieval_golden(corpus_dir=CORPUS_DIR)
    assert len(cases) == 100
    assert sum(case.probe is loader.Probe.UNANSWERABLE for case in cases) == 12
    assert all(case.email_body is None for case in cases[32:])


def test_corpus_rules_4_5_6_validation(tmp_path: Path) -> None:
    # Rule 4: unknown document id
    with pytest.raises(RetrievalFixtureError, match="unknown document id"):
        loader.load_retrieval_golden(
            _write(tmp_path, [_valid_case(expected_document_ids=["dang_ky_tam_tru"])]),
            corpus_dir=CORPUS_DIR,
        )

    # Rule 5: section absent
    with pytest.raises(RetrievalFixtureError, match="the golden set is stale"):
        loader.load_retrieval_golden(
            _write(
                tmp_path,
                [_valid_case(expected_sections=["Quy trình đã bị đổi tên sau khi re-chunk"])],
            ),
            corpus_dir=CORPUS_DIR,
        )

    # Rule 5: borrowed section
    sections = _corpus_sections()
    borrowed = sections["dang-ky-xe"][0]
    assert borrowed not in sections["cap-lai-cccd"]
    with pytest.raises(RetrievalFixtureError, match="is not emitted by load_corpus"):
        loader.load_retrieval_golden(
            _write(
                tmp_path,
                [_valid_case(expected_document_ids=["cap-lai-cccd"], expected_sections=[borrowed])],
            ),
            corpus_dir=CORPUS_DIR,
        )

    # Rule 5: accepts real section
    cases = _covering_cases()
    case = next(c for c in cases if c["expected_document_ids"] == ["cap-lai-cccd"])
    section = sections["cap-lai-cccd"][0]
    case["expected_sections"] = [section]
    loaded = loader.load_retrieval_golden(_write(tmp_path, cases), corpus_dir=CORPUS_DIR)
    assert loaded[cases.index(case)].expected_sections == (section,)

    # Rule 6: missing probe coverage
    with pytest.raises(RetrievalFixtureError, match="missing probe coverage"):
        loader.load_retrieval_golden(_write(tmp_path, [_valid_case()]), corpus_dir=CORPUS_DIR)

    # Rule 6: names uncovered doc/probe
    cases_no_sem = [c for c in _covering_cases() if c["probe"] != "semantic"]
    with pytest.raises(RetrievalFixtureError, match="cap-lai-cccd:semantic"):
        loader.load_retrieval_golden(_write(tmp_path, cases_no_sem), corpus_dir=CORPUS_DIR)

    # Rule 6: requires unanswerable case
    cases_no_unans = [c for c in _covering_cases() if c["probe"] != "unanswerable"]
    with pytest.raises(RetrievalFixtureError, match="no unanswerable case"):
        loader.load_retrieval_golden(_write(tmp_path, cases_no_unans), corpus_dir=CORPUS_DIR)

    # Full coverage passes
    full_cases = _covering_cases()
    loaded_full = loader.load_retrieval_golden(_write(tmp_path, full_cases), corpus_dir=CORPUS_DIR)
    assert len(loaded_full) == len(full_cases)
    assert {c.probe for c in loaded_full} == set(loader.Probe)
