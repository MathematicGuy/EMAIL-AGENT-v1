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


def test_enabled_calendar_exposes_both_tool_specs_to_the_classifier() -> None:
    """Returning no spec makes the live classifier blind to the enabled action.

    Both, not one: the classifier picks a `tool_name`, and a name it was never
    shown is narrowed away by `finalize_route` no matter how well it fits.
    """
    tools = app_module._calendar_classifier_tools(_settings(enabled=True))

    assert {tool.name for tool in tools} == {"create_calendar_event", "list_calendar_events"}
    assert all("calendar" in tool.description.lower() for tool in tools)


def test_the_two_calendar_specs_do_not_share_a_schema() -> None:
    """A read takes a window; a write takes a titled event.

    Identical schemas would mean the argument filler could not tell them apart,
    and the model would be choosing between two names with no other difference.
    """
    create, agenda = sorted(
        app_module._calendar_classifier_tools(_settings(enabled=True)), key=lambda t: t.name
    )

    assert "title" in create.parameters["properties"]  # type: ignore[operator,index]
    assert "title" not in agenda.parameters["properties"]  # type: ignore[operator,index]


def test_disabled_calendar_exposes_no_tool_spec_to_the_classifier() -> None:
    assert app_module._calendar_classifier_tools(_settings(enabled=False)) == ()
