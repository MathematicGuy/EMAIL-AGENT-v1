from cowork_agent.config import ChatMemorySettings


def test_postgres_runtime_keeps_short_term_chat_memory_in_process() -> None:
    from cowork_agent.composition import create_chat_session_buffer
    from cowork_agent.features.ai_chat.session_buffer import InMemoryChatSessionBuffer

    buffer = create_chat_session_buffer(
        ChatMemorySettings(max_turns=3, ttl_seconds=60), durable=True
    )

    assert isinstance(buffer, InMemoryChatSessionBuffer)
