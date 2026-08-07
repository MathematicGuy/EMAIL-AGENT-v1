"""HTTP and event entry points."""

from fastapi import FastAPI


def create_app() -> FastAPI:
    """Load the composition root lazily to avoid an API package import cycle."""
    from ..app import create_app as create_application

    return create_application()


__all__ = ["create_app"]
