"""Retrieve-first inspection bucket tests."""

from pathlib import Path

from tests.unit.scripts.cli_harness import load_script


def _truth(actionability: str, *, context_required: bool) -> dict[str, object]:
    return {
        "actionability": actionability,
        "email_is_sufficient": not context_required,
        "knowledge_gaps": ["Synthetic gap"] if context_required else [],
        "expected_document_types": ["company_policy"] if context_required else [],
        "rationale": "Synthetic rationale.",
        "retrieval_expected": actionability not in {"informational", "irrelevant"},
        "company_context_required": context_required,
    }


def test_sorter_uses_retrieve_first_annotation_buckets() -> None:
    module = load_script("sort_reviewed_annotations_by_route")
    reviewed = {
        "schema_version": 2,
        "rubric_version": "email-pipeline-annotation-v2",
        "reviewed_at": "2026-08-20T00:00:00Z",
        "systematic_errors_resolved": True,
        "case_count": 3,
        "cases": [
            {
                "case_id": "c1",
                "source_message_id": "m1",
                "final": _truth("informational", context_required=False),
            },
            {
                "case_id": "c2",
                "source_message_id": "m2",
                "final": _truth("action_required", context_required=False),
            },
            {
                "case_id": "c3",
                "source_message_id": "m3",
                "final": _truth("unclear", context_required=True),
            },
        ],
    }
    candidates = {
        "schema_version": 1,
        "fetched_at": "2026-08-20T00:00:00Z",
        "gmail_query": "in:inbox",
        "ordering": "received_at_desc",
        "case_count": 3,
        "cases": [
            {
                "case_id": f"c{index}",
                "source_message_id": f"m{index}",
                "gmail_thread_id": f"t{index}",
                "sender": "Synthetic <synthetic@example.com>",
                "subject": "Synthetic subject",
                "received_at": f"2026-08-20T00:0{4 - index}:00Z",
                "labels": ["INBOX"],
                "gmail_content": "Synthetic private content.",
            }
            for index in range(1, 4)
        ],
    }

    grouped = module.sort_reviewed_annotations(reviewed, candidates)

    assert {name: value["case_count"] for name, value in grouped.items()} == {
        "no_action": 1,
        "retrieve_first_context_optional": 1,
        "retrieve_first_context_required": 1,
    }


def test_writer_replaces_legacy_route_files(tmp_path: Path) -> None:
    module = load_script("sort_reviewed_annotations_by_route")
    for filename in module.LEGACY_ROUTE_FILES:
        (tmp_path / filename).write_text("stale", encoding="utf-8")
    grouped = {
        bucket: {
            "schema_version": 2,
            "rubric_version": "email-pipeline-annotation-v2",
            "evaluation_bucket": bucket,
            "case_count": 0,
            "cases": [],
        }
        for bucket in module.BUCKETS
    }

    paths = module.write_route_files(grouped, tmp_path)

    assert all(path.exists() for path in paths)
    assert all(not (tmp_path / filename).exists() for filename in module.LEGACY_ROUTE_FILES)
