import asyncio
from collections.abc import Mapping

import pytest

from cowork_agent.features.ai_chat.tools import Tool, ToolRegistry, ToolResult

ECHO_SCHEMA: Mapping[str, object] = {
    "type": "object",
    "properties": {
        "text": {"type": "string", "maxLength": 10},
        "times": {"type": "integer"},
        "mode": {"type": "string", "enum": ["loud", "quiet"]},
    },
    "required": ["text"],
    "additionalProperties": False,
}


def _echo_tool(
    handler: object = None, *, name: str = "echo", schema: Mapping[str, object] = ECHO_SCHEMA
) -> Tool:
    async def default(arguments: Mapping[str, object]) -> ToolResult:
        return ToolResult(ok=True, text=str(arguments.get("text", "")))

    return Tool(
        name=name,
        description="echo the given text back",
        parameters=schema,
        handler=handler or default,  # type: ignore[arg-type]
    )


def _run(registry: ToolRegistry, name: str, arguments: Mapping[str, object]) -> ToolResult:
    return asyncio.run(registry.run(name, arguments))


def test_specs_are_returned_in_stable_name_order() -> None:
    registry = ToolRegistry([_echo_tool(name="zulu"), _echo_tool(name="alpha")])

    assert [tool.name for tool in registry.specs()] == ["alpha", "zulu"]


def test_duplicate_tool_names_are_a_wiring_error() -> None:
    with pytest.raises(ValueError, match="duplicate tool name: echo"):
        ToolRegistry([_echo_tool(), _echo_tool()])


def test_get_returns_none_for_an_unregistered_name() -> None:
    registry = ToolRegistry([_echo_tool()])

    assert registry.get("echo") is not None
    assert registry.get("create_calendar_event") is None


def test_a_valid_call_reaches_the_handler() -> None:
    registry = ToolRegistry([_echo_tool()])

    assert _run(registry, "echo", {"text": "hi"}) == ToolResult(ok=True, text="hi")


def test_unknown_tool_name_returns_a_failure_naming_what_exists() -> None:
    registry = ToolRegistry([_echo_tool()])

    result = _run(registry, "delete_everything", {})

    assert result.ok is False
    assert "delete_everything" in result.text
    assert "echo" in result.text


def test_empty_registry_reports_no_available_tools() -> None:
    result = _run(ToolRegistry(), "echo", {})

    assert result.ok is False
    assert "none" in result.text


@pytest.mark.parametrize(
    ("arguments", "expected_problem"),
    [
        ({}, "missing required text"),
        ({"text": "hi", "surprise": 1}, "unexpected surprise"),
        ({"text": 42}, "text must be string"),
        ({"text": "hi", "times": "3"}, "times must be integer"),
        ({"text": "hi", "times": True}, "times must be integer"),
        ({"text": "hi", "mode": "shouting"}, "mode must be one of"),
        ({"text": "0123456789x"}, "text exceeds maxLength 10"),
    ],
)
def test_schema_violations_fail_without_reaching_the_handler(
    arguments: Mapping[str, object], expected_problem: str
) -> None:
    calls: list[Mapping[str, object]] = []

    async def recording(args: Mapping[str, object]) -> ToolResult:
        calls.append(args)
        return ToolResult(ok=True, text="reached")

    registry = ToolRegistry([_echo_tool(recording)])

    result = _run(registry, "echo", arguments)

    assert result.ok is False
    assert expected_problem in result.text
    assert calls == []


def test_a_raising_handler_becomes_a_failed_result() -> None:
    async def explode(_: Mapping[str, object]) -> ToolResult:
        raise RuntimeError("the calendar API is down")

    registry = ToolRegistry([_echo_tool(explode)])

    result = _run(registry, "echo", {"text": "hi"})

    assert result.ok is False
    assert "RuntimeError" in result.text
    assert "the calendar API is down" in result.text


def test_a_hanging_handler_is_cut_off_at_the_timeout() -> None:
    async def hang(_: Mapping[str, object]) -> ToolResult:
        await asyncio.sleep(10)
        return ToolResult(ok=True, text="never")

    registry = ToolRegistry([_echo_tool(hang)], timeout_seconds=0.01)

    result = _run(registry, "echo", {"text": "hi"})

    assert result.ok is False
    assert "timed out" in result.text


def test_handler_cancellation_propagates_instead_of_being_reported() -> None:
    """A caller going away is not a tool failure to hand back to a model."""

    async def hang(_: Mapping[str, object]) -> ToolResult:
        await asyncio.sleep(10)
        return ToolResult(ok=True, text="never")

    async def scenario() -> None:
        registry = ToolRegistry([_echo_tool(hang)])
        task = asyncio.ensure_future(registry.run("echo", {"text": "hi"}))
        await asyncio.sleep(0)
        task.cancel()
        await task

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(scenario())


def test_a_handler_returning_ok_false_is_passed_through_unchanged() -> None:
    async def refuse(_: Mapping[str, object]) -> ToolResult:
        return ToolResult(ok=False, text="end must be after start")

    registry = ToolRegistry([_echo_tool(refuse)])

    assert _run(registry, "echo", {"text": "hi"}) == ToolResult(
        ok=False, text="end must be after start"
    )


def test_a_schema_without_constraints_accepts_anything() -> None:
    registry = ToolRegistry([_echo_tool(schema={"type": "object"})])

    assert _run(registry, "echo", {"anything": [1, 2]}).ok is True
