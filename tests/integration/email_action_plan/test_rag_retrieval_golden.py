"""End-to-end email->corpus retrieval over the labeled golden set (SPEC-rag C5).

Every golden case carrying an ``email_body`` is replayed through the real
DigestWorker graph: FakeMailbox -> FakeRouteClassifier -> **real**
``InRepoSemanticMemory`` over the committed corpus -> generator. The assertion
is on the chunk the generator actually received, so nothing between routing and
generation can quietly drop or reorder retrieval and still pass.

The offline embedder is ``HashingEmbedder``, which buckets tokens by hash and
carries no semantics; listed hashing cases cannot rank correctly under it and
are marked xfail with the measured reason. They are confirmed passing under
``--embedder gemini`` (see docs/evaluations/baselines/retrieval-eval-*-gemini-dense.json).
The assertion is deliberately *not* weakened to "somewhere in the top 5": that
would rebuild the blind spot this spec exists to remove.
"""

import asyncio
from pathlib import Path

import pytest
from _pytest.mark.structures import ParameterSet

from cowork_agent.domain import RunStatus
from cowork_agent.domain.target_contracts import (
    Actionability,
    EmailRouteDecision,
    ReasonCode,
    Route,
)
from cowork_agent.features.email_action_plan.short_term import ShortTermStore
from cowork_agent.features.email_action_plan.workflow import CreateDigestRun, DigestWorker
from cowork_agent.integrations.gmail.fakes import FakeMailbox, SafeTextAttachmentExtractor
from cowork_agent.integrations.llm.fakes import FakePlanGenerator, FakeRouteClassifier
from cowork_agent.integrations.rag.fakes import HashingEmbedder
from cowork_agent.integrations.rag.knowledge_base import load_corpus
from cowork_agent.integrations.rag.memory import InRepoSemanticMemory
from cowork_agent.persistence.repositories.local import (
    InMemoryResultRepository,
    InMemoryRunRepository,
    InMemoryTaskRepository,
)
from tests.fixtures.rag.loader import Probe, RetrievalCase, load_retrieval_golden
from tests.integration.email_action_plan.test_workflow import NOW, email, task_for

REPO_ROOT = Path(__file__).resolve().parents[3]
CORPUS_DIR = REPO_ROOT / "data" / "extracted"

LEGACY_EMAIL_DOCUMENT_IDS = frozenset(
    {
        "cap-lai-cccd",
        "dang-ky-ket-hon",
        "dang-ky-xe",
        "huong-dan-nop-ho-so-dai-hoc-vinuni",
        "thue-dien-tu",
        "thu-tuc-dang-ky-bhxh-luatvietnam",
    }
)

#: Cases that cannot rank correctly under a non-semantic embedder. Measured, not
#: guessed: each ranks a topically unrelated document first under HashingEmbedder
#: and the expected document first under Gemini.
_HASHING_XFAIL = {
    "q-001": "HashingEmbedder ranks dang-ky-xe first; passes under Gemini",
    "q-006": "HashingEmbedder ranks thu-tuc-dang-ky-bhxh-luatvietnam first; passes under Gemini",
    "q-014": "HashingEmbedder ranks thu-tuc-dang-ky-bhxh-luatvietnam first; no semantics",
    "q-016": "HashingEmbedder ranks dang-ky-xe first; expected vinuni admissions guide",
}

#: Not an embedder artifact: no retriever in the repo abstains on an
#: unanswerable query. Reproduced under Gemini dense, BM25, hybrid and
#: hybrid+rerank, all at abstention_rate 0.0. Tracked as the open C6 gap.
_ABSTENTION_XFAIL = (
    "no retriever abstains on unanswerable queries; min_score=0.2 filters nothing. "
    "Fails under Gemini too - this is a product gap, not a fake-embedder artifact."
)


def _email_cases() -> list[RetrievalCase]:
    return [
        case
        for case in load_retrieval_golden(corpus_dir=CORPUS_DIR)
        if case.email_body is not None
    ]


def _parameters() -> list[ParameterSet]:
    parameters = []
    for case in _email_cases():
        marks = []
        if case.probe is Probe.UNANSWERABLE:
            marks.append(pytest.mark.xfail(reason=_ABSTENTION_XFAIL, strict=False))
        elif case.id in _HASHING_XFAIL:
            marks.append(pytest.mark.xfail(reason=_HASHING_XFAIL[case.id], strict=False))
        parameters.append(pytest.param(case, id=case.id, marks=marks))
    return parameters


@pytest.fixture(scope="module")
def semantic_memory() -> InRepoSemanticMemory:
    """One built index shared by every case; the corpus is static."""
    documents = tuple(
        document
        for document in load_corpus(CORPUS_DIR)
        if document.document_id in LEGACY_EMAIL_DOCUMENT_IDS
    )
    memory = InRepoSemanticMemory(documents, HashingEmbedder())
    asyncio.run(memory.build_index())
    return memory


def _decision(case: RetrievalCase) -> EmailRouteDecision:
    return EmailRouteDecision(
        actionability=Actionability.ACTION_REQUIRED,
        route=Route.RETRIEVE_RAG,
        candidate_action_item=None,
        email_is_sufficient=False,
        knowledge_gaps=(case.query,),
        retrieval_query=case.query,
        expected_document_types=(),
        reason_codes=(ReasonCode.DOMAIN_KNOWLEDGE_REQUIRED,),
        confidence=0.9,
    )


@pytest.mark.parametrize("case", _parameters())
def test_email_retrieves_the_expected_corpus_document(
    case: RetrievalCase, semantic_memory: InRepoSemanticMemory
) -> None:
    async def scenario() -> None:
        assert case.email_body is not None
        messages = [email("m1", "t1", "Yêu cầu hỗ trợ", body=case.email_body)]
        generator = FakePlanGenerator((task_for("m1", "Yêu cầu hỗ trợ"),))
        runs = InMemoryRunRepository()
        run = await CreateDigestRun(runs).execute(
            user_id="u1",
            mailbox_connection_id="mbx1",
            idempotency_key=f"golden-{case.id}",
            now=NOW,
        )
        worker = DigestWorker(
            runs,
            InMemoryResultRepository(),
            FakeMailbox(messages),
            SafeTextAttachmentExtractor(),
            FakeRouteClassifier({"m1": _decision(case)}),
            generator,
            ShortTermStore(),
            task_repository=InMemoryTaskRepository(),
            semantic_memory=semantic_memory,
        )

        completed = await worker.execute(run.id, now=NOW)

        assert completed is not None and completed.status is RunStatus.SUCCEEDED
        assert len(generator.received_retrievals) == 1
        retrieval = generator.received_retrievals[0]
        assert retrieval is not None, f"{case.id}: generator received no retrieval"
        chunks = retrieval.chunks
        if case.probe is Probe.UNANSWERABLE:
            assert chunks == (), (
                f"{case.id}: no corpus document answers this query, so retrieval "
                f"must return nothing; got {chunks[0].document_id if chunks else None}"
            )
            return
        assert chunks, f"{case.id}: retrieval returned no chunks"
        assert chunks[0].document_id in case.expected_document_ids, (
            f"{case.id}: top chunk is {chunks[0].document_id!r}, "
            f"expected one of {list(case.expected_document_ids)}"
        )

    asyncio.run(scenario())


def test_email_cases_cover_exactly_the_legacy_email_documents() -> None:
    """Guard the E2E fixture boundary against retrieval-only documents."""
    covered = {
        document_id
        for case in _email_cases()
        for document_id in case.expected_document_ids
    }
    assert covered == LEGACY_EMAIL_DOCUMENT_IDS


def test_email_e2e_memory_contains_only_the_legacy_six_document_corpus(
    semantic_memory: InRepoSemanticMemory,
) -> None:
    indexed_document_ids = {chunk.document_id for chunk in semantic_memory._chunks}
    assert indexed_document_ids == LEGACY_EMAIL_DOCUMENT_IDS


def test_selected_email_e2e_cases_are_from_the_legacy_query_range() -> None:
    """Keep email E2E replay scoped to the original q-001 through q-032 cases."""
    assert all(
        case.id in {f"q-{number:03d}" for number in range(1, 33)}
        for case in _email_cases()
    )
