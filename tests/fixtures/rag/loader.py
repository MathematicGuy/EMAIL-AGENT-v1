"""Typed loader for the labeled retrieval golden set (SPEC-rag §5).

Loaded by the retrieval evaluation harness (`scripts/evaluate_retrieval.py`)
and the end-to-end email->corpus fixtures. Schema is documented in README.md
next to this file.

Rules 1-3 are pure schema checks. Rules 4-6 compare the labels against the
real corpus and therefore need ``corpus_dir``; they are the anti-rot guard
that makes a re-chunk fail loudly instead of silently scoring 0.0.
"""

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum, StrEnum
from pathlib import Path
from typing import Any, TypeVar

TEnum = TypeVar("TEnum", bound=Enum)

_CASE_ID_PATTERN = re.compile(r"^q-\d{3}$")


class Probe(StrEnum):
    LEXICAL = "lexical"
    SEMANTIC = "semantic"
    MIXED = "mixed"
    UNANSWERABLE = "unanswerable"


#: Probe types every corpus document must carry at least once (SPEC §5.1).
ANSWERABLE_PROBES: tuple[Probe, ...] = (Probe.LEXICAL, Probe.SEMANTIC, Probe.MIXED)

EXPANDED_CASE_COUNT = 100
LEGACY_CASE_COUNT = 32
LEGACY_CASE_SNAPSHOT_SHA256 = (
    "656ab75870a3f3f9b3de0486debd3e0f835f2a4ef03fa97191e7950df00c1a7f"
)
NEW_DOCUMENT_IDS = frozenset(
    {
        "01-2021-nd-cp-283247",
        "31-2024-qh15-523642",
        "41-2024-qh15-557190",
        "49-2019-qh14-402073",
        "chi-tiet-thu-tuc-1-004194-1786097965866",
        "chi-tiet-thu-tuc-1-115132-1786096253281",
        "chi-tiet-thu-tuc-1-115970-1786097982328",
        "chi-tiet-thu-tuc-1-116194-1786096137126",
        "chi-tiet-thu-tuc-2-001194-1786096928665",
        "chi-tiet-thu-tuc-3-000228-1786096860852",
        "dang-ky-tam-tru",
    }
)
LARGE_LEGAL_DOCUMENT_IDS = frozenset(
    {
        "01-2021-nd-cp-283247",
        "31-2024-qh15-523642",
        "41-2024-qh15-557190",
        "49-2019-qh14-402073",
    }
)
DETAILED_PROCEDURE_DOCUMENT_IDS = NEW_DOCUMENT_IDS - LARGE_LEGAL_DOCUMENT_IDS - {
    "dang-ky-tam-tru"
}
THREE_PROBE_NEW_DOCUMENT_IDS = NEW_DOCUMENT_IDS - {"dang-ky-tam-tru"}


class RetrievalFixtureError(ValueError):
    """Raised when the fixture JSON violates the documented schema."""


@dataclass(frozen=True)
class RetrievalCase:
    id: str
    query: str
    probe: Probe
    expected_document_ids: tuple[str, ...]
    expected_sections: tuple[str, ...]
    email_body: str | None
    notes: str | None


DEFAULT_FIXTURE_PATH = Path(__file__).with_name("retrieval_golden.json")


def load_retrieval_golden(
    path: Path | None = None, *, corpus_dir: Path | None = None
) -> tuple[RetrievalCase, ...]:
    """Parse and validate the golden set.

    Args:
        path: Fixture JSON to read; defaults to ``retrieval_golden.json``.
        corpus_dir: Knowledge corpus directory. When given, rules 4-6 also
            run: every expected document id must be a real corpus document,
            every expected section must be one ``load_corpus`` actually
            emits for that document, and every document must carry each
            answerable probe type. Omit it for pure-schema checks that must
            not touch the corpus.

    Raises:
        RetrievalFixtureError: on any schema or corpus-consistency violation.
    """
    fixture_path = path or DEFAULT_FIXTURE_PATH
    try:
        raw = json.loads(fixture_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RetrievalFixtureError(f"{fixture_path}: invalid JSON: {exc}") from exc
    if not isinstance(raw, list):
        raise RetrievalFixtureError(f"{fixture_path}: top level must be a JSON array")
    cases = [_parse_case(entry, index, fixture_path) for index, entry in enumerate(raw)]
    ids = [case.id for case in cases]
    duplicates = {case_id for case_id in ids if ids.count(case_id) > 1}
    if duplicates:
        raise RetrievalFixtureError(f"{fixture_path}: duplicate case ids: {sorted(duplicates)}")
    if fixture_path.resolve() == DEFAULT_FIXTURE_PATH.resolve():
        _validate_repository_fixture_contract(cases, raw, fixture_path)
    if corpus_dir is not None:
        _validate_against_corpus(cases, corpus_dir, fixture_path)
    return tuple(cases)


def _parse_case(entry: Any, index: int, source: Path) -> RetrievalCase:
    where = f"{source}[{index}]"
    if not isinstance(entry, dict):
        raise RetrievalFixtureError(f"{where}: case must be an object")
    case_id = _require_str(entry, "id", where)
    if _CASE_ID_PATTERN.match(case_id) is None:
        raise RetrievalFixtureError(f"{where}: 'id' must match q-NNN; got {case_id!r}")
    probe = _enum_value(entry.get("probe"), Probe, f"{where}.probe")
    document_ids = _require_str_tuple(entry, "expected_document_ids", where)
    if bool(document_ids) is (probe is Probe.UNANSWERABLE):
        raise RetrievalFixtureError(
            f"{where}: 'expected_document_ids' must be empty if and only if "
            f"probe is {Probe.UNANSWERABLE.value}"
        )
    return RetrievalCase(
        id=case_id,
        query=_require_str(entry, "query", where),
        probe=probe,
        expected_document_ids=document_ids,
        expected_sections=_require_str_tuple(entry, "expected_sections", where),
        email_body=_optional_str(entry, "email_body", where),
        notes=_optional_str(entry, "notes", where, required=False),
    )


def _validate_against_corpus(
    cases: Sequence[RetrievalCase], corpus_dir: Path, source: Path
) -> None:
    """Rules 4-5: expected documents and sections must exist in the corpus."""
    from cowork_agent.integrations.rag.knowledge_base import load_corpus

    sections_by_document = {
        document.document_id: {
            chunk.section for chunk in document.chunks if chunk.section is not None
        }
        for document in load_corpus(corpus_dir)
    }
    for index, case in enumerate(cases):
        where = f"{source}[{index}]"
        for document_id in case.expected_document_ids:
            if document_id not in sections_by_document:
                raise RetrievalFixtureError(
                    f"{where}: unknown document id {document_id!r}; "
                    f"corpus has {sorted(sections_by_document)}"
                )
        known = {
            section
            for document_id in case.expected_document_ids
            for section in sections_by_document[document_id]
        }
        for section in case.expected_sections:
            if section not in known:
                raise RetrievalFixtureError(
                    f"{where}: section {section!r} is not emitted by load_corpus for "
                    f"{list(case.expected_document_ids)}; the golden set is stale"
                )
    if source.resolve() == DEFAULT_FIXTURE_PATH.resolve():
        _validate_repository_fixture_coverage(cases, tuple(sections_by_document), source)
    else:
        _validate_probe_coverage(cases, tuple(sections_by_document), source)


def _validate_repository_fixture_contract(
    cases: Sequence[RetrievalCase], raw_cases: Sequence[Any], source: Path
) -> None:
    """Enforce the immutable 32-case baseline and exact V2 allocation."""
    if len(cases) != EXPANDED_CASE_COUNT:
        raise RetrievalFixtureError(f"{source}: expected exactly 100 cases")
    expected_ids = tuple(f"q-{number:03d}" for number in range(1, 101))
    if tuple(case.id for case in cases) != expected_ids:
        raise RetrievalFixtureError(f"{source}: expected contiguous IDs q-001 through q-100")

    legacy_snapshot = json.dumps(
        list(raw_cases[:LEGACY_CASE_COUNT]),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if hashlib.sha256(legacy_snapshot).hexdigest() != LEGACY_CASE_SNAPSHOT_SHA256:
        raise RetrievalFixtureError(f"{source}: legacy case snapshot q-001 through q-032 changed")

    _validate_document_allocation(
        cases[32:72], LARGE_LEGAL_DOCUMENT_IDS, expected_per_document=10, source=source
    )
    _validate_document_allocation(
        cases[72:90],
        DETAILED_PROCEDURE_DOCUMENT_IDS,
        expected_per_document=3,
        source=source,
    )
    if any(case.expected_document_ids != ("dang-ky-tam-tru",) for case in cases[90:92]):
        raise RetrievalFixtureError(f"{source}: expected exactly 2 dang-ky-tam-tru cases")
    if any(
        case.probe is not Probe.UNANSWERABLE
        or case.expected_document_ids
        or case.expected_sections
        for case in cases[92:]
    ):
        raise RetrievalFixtureError(f"{source}: expected exactly 8 appended unanswerable cases")
    if any(case.email_body is not None for case in cases[LEGACY_CASE_COUNT:]):
        raise RetrievalFixtureError(f"{source}: appended cases must have email_body: null")


def _validate_document_allocation(
    cases: Sequence[RetrievalCase],
    expected_document_ids: frozenset[str],
    *,
    expected_per_document: int,
    source: Path,
) -> None:
    counts = {document_id: 0 for document_id in expected_document_ids}
    for case in cases:
        if len(case.expected_document_ids) != 1:
            break
        document_id = case.expected_document_ids[0]
        if document_id not in counts:
            break
        counts[document_id] += 1
    expected_counts = {
        document_id: expected_per_document for document_id in expected_document_ids
    }
    if counts != expected_counts:
        total = len(expected_document_ids) * expected_per_document
        category = "large-law" if expected_per_document == 10 else "detailed-procedure"
        raise RetrievalFixtureError(f"{source}: expected exactly {total} {category} cases")


def _validate_repository_fixture_coverage(
    cases: Sequence[RetrievalCase], document_ids: Sequence[str], source: Path
) -> None:
    """Enforce the V2 repository coverage allocation without constraining temp fixtures."""
    covered: dict[str, set[Probe]] = {document_id: set() for document_id in document_ids}
    for case in cases:
        for document_id in case.expected_document_ids:
            covered[document_id].add(case.probe)
    missing_documents = [document_id for document_id, probes in covered.items() if not probes]
    if missing_documents:
        raise RetrievalFixtureError(f"{source}: missing document coverage: {missing_documents}")
    missing_probes = [
        f"{document_id}:{probe.value}"
        for document_id in sorted(THREE_PROBE_NEW_DOCUMENT_IDS)
        for probe in ANSWERABLE_PROBES
        if probe not in covered[document_id]
    ]
    if missing_probes:
        raise RetrievalFixtureError(
            f"{source}: missing new-document probe coverage: {missing_probes}"
        )
    unanswerable_count = sum(case.probe is Probe.UNANSWERABLE for case in cases)
    if unanswerable_count != 12:
        raise RetrievalFixtureError(f"{source}: expected exactly 12 unanswerable cases")


def _validate_probe_coverage(
    cases: Sequence[RetrievalCase], document_ids: Sequence[str], source: Path
) -> None:
    """Rule 6: every document carries each answerable probe, plus abstention."""
    covered: dict[str, set[Probe]] = {document_id: set() for document_id in document_ids}
    for case in cases:
        for document_id in case.expected_document_ids:
            covered[document_id].add(case.probe)
    missing = [
        f"{document_id}:{probe.value}"
        for document_id in document_ids
        for probe in ANSWERABLE_PROBES
        if probe not in covered[document_id]
    ]
    if missing:
        raise RetrievalFixtureError(f"{source}: missing probe coverage: {missing}")
    if not any(case.probe is Probe.UNANSWERABLE for case in cases):
        raise RetrievalFixtureError(
            f"{source}: no {Probe.UNANSWERABLE.value} case; the abstention metric would be empty"
        )


def _require_str(entry: dict[str, Any], field: str, where: str) -> str:
    value = entry.get(field)
    if not isinstance(value, str) or not value:
        raise RetrievalFixtureError(f"{where}: missing non-empty string field '{field}'")
    return value


def _require_str_tuple(entry: dict[str, Any], field: str, where: str) -> tuple[str, ...]:
    value = entry.get(field)
    if not isinstance(value, list):
        raise RetrievalFixtureError(f"{where}: missing array field '{field}'")
    for item in value:
        if not isinstance(item, str) or not item:
            raise RetrievalFixtureError(f"{where}: '{field}' entries must be non-empty strings")
    return tuple(str(item) for item in value)


def _optional_str(
    entry: dict[str, Any], field: str, where: str, *, required: bool = True
) -> str | None:
    if field not in entry:
        if required:
            raise RetrievalFixtureError(f"{where}: missing field '{field}'")
        return None
    value = entry[field]
    if value is None:
        return None
    if not isinstance(value, str):
        raise RetrievalFixtureError(f"{where}: field '{field}' must be a string or null")
    return value


def _enum_value(value: Any, enum_type: type[TEnum], where: str) -> TEnum:
    try:
        return enum_type(value)
    except (ValueError, TypeError):
        allowed = ", ".join(item.value for item in enum_type)
        raise RetrievalFixtureError(
            f"{where}: unknown value {value!r}; allowed: {allowed}"
        ) from None
