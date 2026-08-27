from __future__ import annotations

from pathlib import Path

import pytest

from cowork_agent.config import EvaluationSettings

TOKEN = "test-evaluation-token-with-32-characters"


def test_evaluation_api_is_disabled_by_default() -> None:
    settings = EvaluationSettings.from_env({})

    assert settings.enabled is False
    assert settings.api_token == ""
    assert settings.job_db_path == ".data/evaluation-jobs.db"
    assert settings.artifact_root == ".data/evaluation-jobs"


def test_evaluation_settings_enabled_disabled_and_validation() -> None:
    for flag in ("0", "false", "False"):
        assert EvaluationSettings.from_env({"EVALUATION_API_ENABLED": flag}).enabled is False

    for flag in ("1", "true", "True"):
        settings = EvaluationSettings.from_env(
            {"EVALUATION_API_ENABLED": flag, "EVALUATION_API_TOKEN": TOKEN}
        )
        assert settings.enabled is True
        assert settings.api_token == TOKEN

    for bad_env, match in [
        ({"EVALUATION_API_ENABLED": "maybe"}, "EVALUATION_API_ENABLED"),
        ({"EVALUATION_API_ENABLED": "1"}, "EVALUATION_API_TOKEN"),
        ({"EVALUATION_API_ENABLED": "1", "EVALUATION_API_TOKEN": "short"}, "EVALUATION_API_TOKEN"),
    ]:
        with pytest.raises(ValueError, match=match):
            EvaluationSettings.from_env(bad_env)


def test_token_never_appears_in_representation() -> None:
    settings = EvaluationSettings.from_env(
        {"EVALUATION_API_ENABLED": "1", "EVALUATION_API_TOKEN": TOKEN},
    )

    assert TOKEN not in repr(settings)
    assert TOKEN not in str(settings)


def test_custom_storage_locations_are_read_from_env() -> None:
    settings = EvaluationSettings.from_env(
        {
            "EVALUATION_API_ENABLED": "1",
            "EVALUATION_API_TOKEN": TOKEN,
            "EVALUATION_JOB_DB_PATH": ".data/evaluation-jobs.db",
            "EVALUATION_ARTIFACT_ROOT": ".data/evaluation-jobs",
        },
    )

    assert settings.job_db_path == ".data/evaluation-jobs.db"
    assert settings.artifact_root == ".data/evaluation-jobs"


def test_to_runtime_config_builds_resolved_runtime_locations(tmp_path: Path) -> None:
    settings = EvaluationSettings.from_env(
        {
            "EVALUATION_API_ENABLED": "1",
            "EVALUATION_API_TOKEN": TOKEN,
            "EVALUATION_JOB_DB_PATH": str(tmp_path / "evaluation-jobs.db"),
            "EVALUATION_ARTIFACT_ROOT": str(tmp_path / "evaluation-jobs"),
        },
    )

    runtime_config = settings.to_runtime_config()

    assert runtime_config.job_db_path == tmp_path / "evaluation-jobs.db"
    assert runtime_config.artifact_root == tmp_path / "evaluation-jobs"
    assert runtime_config.scratch_root is not None
    assert runtime_config.scratch_root.is_relative_to(runtime_config.artifact_root)
    assert TOKEN not in repr(runtime_config)
