from datetime import UTC, datetime

from cowork_agent.domain.chat_contracts import (
    ChatTurn,
    IntentClassifierInput,
    ReadyDocumentRef,
)
from cowork_agent.features.ai_chat.intent.prompt import (
    INTENT_PROMPT_VERSION,
    build_intent_prompt,
)


def test_prompt_has_five_tiers_and_only_title_metadata() -> None:
    classifier_input = IntentClassifierInput(
        current_message="What did it say?",
        recent_turns=(ChatTurn("turn-1", "session-1", "Earlier", "Context", datetime.now(UTC)),),
        ready_documents=(ReadyDocumentRef("doc-1", "Employee Handbook"),),
    )

    prompt = build_intent_prompt(classifier_input)

    for tier in range(1, 6):
        assert f"TIER {tier}" in prompt
    assert "Employee Handbook" in prompt
    assert "doc-1" not in prompt
    assert "chunk" not in prompt.casefold()
    assert INTENT_PROMPT_VERSION == "chat-intent-v1"
