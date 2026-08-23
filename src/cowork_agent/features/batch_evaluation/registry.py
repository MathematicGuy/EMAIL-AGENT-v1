"""Startup-only registry for statically linked evaluation plug-ins."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from .contracts import (
    EvaluationPlugin,
    ExecutionMode,
    _freeze_safe_mapping,
    _require_identifier,
)


class PluginRegistry:
    """Register plug-ins once at startup; never load code from request data."""

    def __init__(self) -> None:
        self._plugins: dict[str, EvaluationPlugin] = {}

    def register(self, plugin: EvaluationPlugin) -> None:
        evaluation_type = _require_identifier(plugin.evaluation_type, "evaluation_type")
        _require_identifier(plugin.version, "version")
        if not isinstance(plugin.supported_modes, frozenset) or not plugin.supported_modes:
            raise ValueError("supported_modes must be a non-empty frozenset")
        if not all(isinstance(mode, ExecutionMode) for mode in plugin.supported_modes):
            raise TypeError("supported_modes must contain ExecutionMode values")
        _freeze_safe_mapping(plugin.parameter_schema, "parameter_schema")
        if evaluation_type in self._plugins:
            raise ValueError("duplicate evaluation type")
        self._plugins[evaluation_type] = plugin

    def require(self, evaluation_type: str) -> EvaluationPlugin:
        try:
            return self._plugins[evaluation_type]
        except KeyError:
            raise ValueError("unknown evaluation type") from None

    def list_types(self) -> tuple[Mapping[str, object], ...]:
        """Return only safe static discovery metadata in deterministic order."""

        return tuple(
            MappingProxyType(
                {
                    "type": evaluation_type,
                    "version": plugin.version,
                    "modes": tuple(sorted(mode.value for mode in plugin.supported_modes)),
                    "parameter_schema": _freeze_safe_mapping(
                        plugin.parameter_schema, "parameter_schema"
                    ),
                }
            )
            for evaluation_type, plugin in sorted(self._plugins.items())
        )
