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


def test_tool_registry_spec_ordering_duplicate_and_lookup() -> None:
    # Stable name order
    registry = ToolRegistry([_echo_tool(name="zulu"), _echo_tool(name="alpha")])
    assert [tool.name for tool in registry.specs()] == ["alpha", "zulu"]

    # Duplicate names error
    with pytest.raises(ValueError, match="duplicate tool name: echo"):
        ToolRegistry([_echo_tool(), _echo_tool()])

    # Lookup
    assert registry.get("alpha") is not None
    assert registry.get("missing") is None

    # Unknown tool execution
    result = _run(registry, "unknown", {})
    assert result.ok is False and "unknown" in result.text


def test_tool_registry_argument_validation_and_rejections() -> None:
    calls: list[Mapping[str, object]] = []

    async def recording(args: Mapping[str, object]) -> ToolResult:
        calls.append(args)
        return ToolResult(ok=True, text="reached")

    registry = ToolRegistry([_echo_tool(recording)])

    # Valid call
    valid_res = _run(registry, "echo", {"text": "hi"})
    assert valid_res.ok is True and valid_res.text == "reached"
    assert len(calls) == 1

    # Schema violations fail without reaching handler
    violations = [
        ({}, "missing required text"),
        ({"text": "hi", "surprise": 1}, "unexpected surprise"),
        ({"text": 42}, "text must be string"),
        ({"text": "hi", "times": "3"}, "times must be integer"),
        ({"text": "hi", "times": True}, "times must be integer"),
        ({"text": "hi", "mode": "shouting"}, "mode must be one of"),
        ({"text": "0123456789x"}, "text exceeds maxLength 10"),
    ]
    for args, problem in violations:
        res = _run(registry, "echo", args)
        assert res.ok is False and problem in res.text
    assert len(calls) == 1  # No additional calls reached handler


def test_tool_registry_execution_exceptions_timeouts_and_cancellation() -> None:
    # Raising handler becomes failed result
    async def explode(_: Mapping[str, object]) -> ToolResult:
        raise RuntimeError("API is down")

    assert _run(ToolRegistry([_echo_tool(explode)]), "echo", {"text": "hi"}).ok is False

    # Timeout
    async def hang(_: Mapping[str, object]) -> ToolResult:
        await asyncio.sleep(10)
        return ToolResult(ok=True, text="never")

    assert (
        _run(ToolRegistry([_echo_tool(hang)], timeout_seconds=0.01), "echo", {"text": "hi"}).ok
        is False
    )

    # Cancellation propagates
    async def scenario() -> None:
        reg = ToolRegistry([_echo_tool(hang)])
        task = asyncio.ensure_future(reg.run("echo", {"text": "hi"}))
        await asyncio.sleep(0)
        task.cancel()
        await task

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(scenario())
