"""Composition checks for the optional executable chat-tool surface."""

import cowork_agent.app as app_module
from cowork_agent.integrations.google_calendar import GoogleCalendarSettings


def _settings(*, enabled: bool) -> GoogleCalendarSettings:
    return GoogleCalendarSettings(
        client_id="test-client",
        client_secret="test-secret",
        refresh_token="test-refresh",
        timezone="Asia/Ho_Chi_Minh",
        enabled=enabled,
    )


def test_enabled_calendar_exposes_its_tool_spec_to_the_classifier() -> None:
    """Returning no spec makes the live classifier blind to the enabled action."""
    (tool,) = app_module._calendar_classifier_tools(_settings(enabled=True))

    assert tool.name == "create_calendar_event"
    assert "calendar" in tool.description.lower()


def test_disabled_calendar_exposes_no_tool_spec_to_the_classifier() -> None:
    assert app_module._calendar_classifier_tools(_settings(enabled=False)) == ()
