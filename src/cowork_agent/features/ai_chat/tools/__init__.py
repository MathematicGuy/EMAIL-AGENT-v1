"""Executable in-chat tools: one registry, one dispatch, failures as data."""

from .calendar import (
    CALENDAR_TOOL_DESCRIPTION,
    CALENDAR_TOOL_NAME,
    CALENDAR_TOOL_SCHEMA,
    CalendarError,
    CalendarEventDraft,
    CalendarPort,
    InMemoryCalendar,
    build_calendar_tool,
    google_event_id,
)
from .registry import (
    DEFAULT_TOOL_TIMEOUT_SECONDS,
    Tool,
    ToolRegistry,
    ToolResult,
    validate_arguments,
)

__all__ = [
    "CALENDAR_TOOL_DESCRIPTION",
    "CALENDAR_TOOL_NAME",
    "CALENDAR_TOOL_SCHEMA",
    "DEFAULT_TOOL_TIMEOUT_SECONDS",
    "CalendarError",
    "CalendarEventDraft",
    "CalendarPort",
    "InMemoryCalendar",
    "Tool",
    "ToolRegistry",
    "ToolResult",
    "build_calendar_tool",
    "google_event_id",
    "validate_arguments",
]
