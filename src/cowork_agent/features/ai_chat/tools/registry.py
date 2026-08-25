"""Name + schema + handler registry that dispatches one tool call and never raises."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

DEFAULT_TOOL_TIMEOUT_SECONDS = 15.0


@dataclass(frozen=True, slots=True)
class ToolResult:
    """One tool outcome as the model will read it back."""

    ok: bool
    text: str


@dataclass(frozen=True, slots=True)
class Tool:
    """A callable action: what it is called, what it does, what it accepts."""

    name: str
    # One line. Rendered into the classifier prompt, so it is what the router
    # decides on -- it describes when to pick the tool, not how it works.
    description: str
    # JSON Schema, object type. Only the subset in `validate_arguments` is
    # enforced, but the full document is kept so a provider's native
    # tool-calling API can be handed it verbatim later.
    parameters: Mapping[str, object]
    handler: Callable[[Mapping[str, object]], Awaitable[ToolResult]]


class ToolRegistry:
    """Expose tool schemas to the model and dispatch calls by name."""

    def __init__(
        self,
        tools: Sequence[Tool] = (),
        *,
        timeout_seconds: float = DEFAULT_TOOL_TIMEOUT_SECONDS,
    ) -> None:
        by_name: dict[str, Tool] = {}
        for tool in tools:
            if tool.name in by_name:
                # A shadowed tool would make dispatch depend on registration
                # order, so this is a wiring bug, not a runtime condition.
                raise ValueError(f"duplicate tool name: {tool.name}")
            by_name[tool.name] = tool
        self._tools = by_name
        self._timeout_seconds = timeout_seconds

    def specs(self) -> tuple[Tool, ...]:
        """Registered tools in stable name order."""

        return tuple(self._tools[name] for name in sorted(self._tools))

    def get(self, name: str) -> Tool | None:
        """The named tool, or None. Lets callers narrow before spending a model call."""

        return self._tools.get(name)

    async def run(self, name: str, arguments: Mapping[str, object]) -> ToolResult:
        """Validate against the tool's schema, dispatch, and report failure as data.

        Never raises for an unknown name, invalid arguments, a handler
        exception, or a timeout -- every one of those comes back as
        `ToolResult(ok=False)`. The controller is mid-stream when it calls this
        and must not die, and a later ReAct loop has to be able to read the
        failure and decide whether to retry.

        The single exception is `asyncio.CancelledError`, which propagates: the
        caller going away is not a tool failure to report back to a model.
        """

        tool = self._tools.get(name)
        if tool is None:
            known = ", ".join(sorted(self._tools)) or "none"
            return ToolResult(ok=False, text=f"Unknown tool {name!r}. Available: {known}.")

        problem = validate_arguments(tool.parameters, arguments)
        if problem is not None:
            return ToolResult(ok=False, text=f"Invalid arguments for {name}: {problem}")

        try:
            return await asyncio.wait_for(tool.handler(arguments), self._timeout_seconds)
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            return ToolResult(
                ok=False,
                text=f"Tool {name} timed out after {self._timeout_seconds:g}s.",
            )
        except Exception as exc:  # noqa: BLE001 - failure is the return value here
            return ToolResult(ok=False, text=f"Tool {name} failed: {type(exc).__name__}: {exc}")


_JSON_TYPES: Mapping[str, type | tuple[type, ...]] = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "object": dict,
    "array": (list, tuple),
}


def validate_arguments(schema: Mapping[str, object], arguments: Mapping[str, object]) -> str | None:
    """Check `arguments` against the JSON Schema subset tool parameters use.

    Returns a one-line problem description, or None when valid.

    Deliberately hand-rolled rather than pulling in `jsonschema`: the supported
    subset is `type`, `properties`, `required`, `additionalProperties: false`,
    `enum` and `maxLength`, which is everything the tools in this codebase
    declare. Anything outside that subset is ignored, so a schema that grows
    past it silently loses enforcement -- swap in a real validator at that
    point rather than extending this.
    """

    properties = _mapping(schema.get("properties"))
    required = schema.get("required")
    if isinstance(required, Sequence) and not isinstance(required, str):
        missing = [str(key) for key in required if str(key) not in arguments]
        if missing:
            return f"missing required {', '.join(sorted(missing))}"

    if schema.get("additionalProperties") is False:
        unexpected = [key for key in arguments if key not in properties]
        if unexpected:
            return f"unexpected {', '.join(sorted(unexpected))}"

    for key, value in arguments.items():
        spec = _mapping(properties.get(key))
        if not spec:
            continue
        problem = _validate_value(key, value, spec)
        if problem is not None:
            return problem
    return None


def _validate_value(key: str, value: object, spec: Mapping[str, object]) -> str | None:
    declared = spec.get("type")
    if isinstance(declared, str):
        expected = _JSON_TYPES.get(declared)
        # `bool` is a subclass of `int`, so an unguarded isinstance would let
        # `True` pass as an integer.
        mistyped = expected is not None and (
            not isinstance(value, expected)
            or (declared in {"integer", "number"} and isinstance(value, bool))
        )
        if mistyped:
            return f"{key} must be {declared}, got {type(value).__name__}"

    allowed = spec.get("enum")
    if isinstance(allowed, Sequence) and not isinstance(allowed, str) and value not in allowed:
        return f"{key} must be one of {', '.join(str(item) for item in allowed)}"

    max_length = spec.get("maxLength")
    if isinstance(max_length, int) and isinstance(value, str) and len(value) > max_length:
        return f"{key} exceeds maxLength {max_length}"
    return None


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}
