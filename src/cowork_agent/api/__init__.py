"""HTTP and event entry points."""

from fastapi import FastAPI

from .handlers import MailTodoApi


def create_app() -> FastAPI:
    """Load the composition root lazily to avoid an API package import cycle."""
    from ..app import create_app as create_application

    return create_application()


__all__ = ["MailTodoApi", "create_app"]
