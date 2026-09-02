"""Async PostgreSQL pool defaults for the control plane (ADR-010)."""

from __future__ import annotations

from typing import Any


def control_plane_pool_kwargs() -> dict[str, Any]:
    """Warm, long-lived connections. Do not ping the server on every checkout."""
    return {
        "min_size": 3,
        "max_size": 8,
        "open": False,
        "check": None,
        "max_idle": 600.0,
        "max_lifetime": 3600.0,
        "kwargs": {"prepare_threshold": None},
    }
