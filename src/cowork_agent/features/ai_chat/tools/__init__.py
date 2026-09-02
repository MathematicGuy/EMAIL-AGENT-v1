"""Executable in-chat tools: one registry, one dispatch, failures as data."""

from .agenda import (
    AGENDA_TOOL_DESCRIPTION,
    AGENDA_TOOL_NAME,
    AGENDA_TOOL_SCHEMA,
    CalendarEventSummary,
    CalendarQueryPort,
    CalendarWindow,
    build_agenda_tool,
)
from .ambiguous_hour import ambiguous_hour_question
from .calendar import (
    CALENDAR_TOOL_DESCRIPTION,
    CALENDAR_TOOL_NAME,
    CALENDAR_TOOL_SCHEMA,
    CalendarEventDraft,
    CalendarPort,
    build_calendar_tool,
    google_event_id,
)
from .calendar_core import CalendarError, parse_moment, parse_range
from .fake_calendar import InMemoryCalendar
from .registry import (
    DEFAULT_TOOL_TIMEOUT_SECONDS,
    Tool,
    ToolRegistry,
    ToolResult,
    validate_arguments,
)

__all__ = [
    "AGENDA_TOOL_DESCRIPTION",
    "AGENDA_TOOL_NAME",
    "AGENDA_TOOL_SCHEMA",
    "CALENDAR_TOOL_DESCRIPTION",
    "CALENDAR_TOOL_NAME",
    "CALENDAR_TOOL_SCHEMA",
    "DEFAULT_TOOL_TIMEOUT_SECONDS",
    "CalendarError",
    "CalendarEventDraft",
    "CalendarEventSummary",
    "CalendarPort",
    "CalendarQueryPort",
    "CalendarWindow",
    "InMemoryCalendar",
    "Tool",
    "ToolRegistry",
    "ToolResult",
    "ambiguous_hour_question",
    "build_agenda_tool",
    "build_calendar_tool",
    "google_event_id",
    "parse_moment",
    "parse_range",
    "validate_arguments",
]
