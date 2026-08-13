from tests.fixtures.chat_routing.loader import ChatRoutingGroup, load_chat_routing_cases


def test_chat_routing_fixture_is_balanced_and_has_required_trap_groups() -> None:
    cases = load_chat_routing_cases()

    assert len(cases) >= 60
    counts = {
        group: sum(case.group is group for case in cases) for group in ChatRoutingGroup
    }
    assert set(counts.values()) == {len(cases) // 4}
    assert all(
        case.labels.expected_needs_rag
        for case in cases
        if case.group is ChatRoutingGroup.AMBIGUOUS
    )
    assert all(
        not case.labels.expected_needs_rag
        for case in cases
        if case.group is ChatRoutingGroup.DISTRACTOR
    )
