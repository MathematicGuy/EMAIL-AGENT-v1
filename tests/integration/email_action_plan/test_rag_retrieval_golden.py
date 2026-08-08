"""End-to-end email->corpus retrieval over the labeled golden set (SPEC-rag C5).

Every golden case carrying an ``email_body`` is replayed through the real
DigestWorker graph: FakeMailbox -> FakeRouteClassifier -> **real**
``InRepoSemanticMemory`` over the committed corpus -> generator. The assertion
is on the chunk the generator actually received, so nothing between routing and
generation can quietly drop or reorder retrieval and still pass.

The offline embedder is ``HashingEmbedder``, which buckets tokens by hash and
carries no semantics; three cases genuinely cannot rank correctly under it and
are marked xfail with the measured reason. They are confirmed passing under
``--embedder gemini`` (see docs/baselines/retrieval-eval-*-gemini-dense.json).
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
from cowork_agent.identity import LOCAL_TENANT_ID
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

#: Cases that cannot rank correctly under a non-semantic embedder. Measured, not
#: guessed: each ranks a topically unrelated document first under HashingEmbedder
#: and the expected document first under Gemini.
_HASHING_XFAIL = {
    "q-001": "HashingEmbedder ranks dang_ky_xe first; passes under Gemini",
    "q-006": "HashingEmbedder ranks thu_tuc_dang_ky_bhxh_luatvietnam first; passes under Gemini",
    "q-026": "HashingEmbedder ranks dang_ky_xe first; passes under Gemini",
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
    memory = InRepoSemanticMemory(
        load_corpus(CORPUS_DIR, tenant_id=LOCAL_TENANT_ID), HashingEmbedder()
    )
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


def test_every_corpus_document_is_covered_by_an_email_case() -> None:
    """Guard the fixture spread: an email case per corpus document (PLAN T-2.4)."""
    covered = {
        document_id
        for case in _email_cases()
        for document_id in case.expected_document_ids
    }
    corpus = {
        document.document_id
        for document in load_corpus(CORPUS_DIR, tenant_id=LOCAL_TENANT_ID)
    }
    assert covered == corpus, f"documents with no email case: {sorted(corpus - covered)}"
