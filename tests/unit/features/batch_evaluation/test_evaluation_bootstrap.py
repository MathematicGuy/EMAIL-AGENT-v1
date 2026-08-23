from __future__ import annotations

from pathlib import Path

import pytest

from cowork_agent.features.batch_evaluation.bootstrap import (
    EvaluationRuntimeConfig,
    build_evaluation_runtime,
)


def test_runtime_config_rejects_a_scratch_directory_outside_artifacts(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="scratch_root"):
        EvaluationRuntimeConfig(
            job_db_path=tmp_path / "jobs.db",
            artifact_root=tmp_path / "artifacts",
            scratch_root=tmp_path / "scratch",
        )


@pytest.mark.asyncio
async def test_bootstrap_initializes_the_static_memory_runtime_without_secret_repr(
    tmp_path: Path,
) -> None:
    secret = "mistral-bootstrap-secret"
    config = EvaluationRuntimeConfig(
        job_db_path=tmp_path / "jobs.db",
        artifact_root=tmp_path / "artifacts",
    )

    runtime = build_evaluation_runtime(
        config,
        {
            "MISTRAL_API_KEY": secret,
            "MISTRAL_API_KEY2": "mistral-bootstrap-secret-2",
        },
    )

    assert runtime.credential_pool.aliases == ("mistral-1", "mistral-2")
    assert secret not in repr(runtime)
    assert config.scratch_root == tmp_path / "artifacts" / ".runtime" / "scratch"

    await runtime.initialize()
    assert [item["type"] for item in await runtime.service.list_types()] == ["memory-eval"]
    await runtime.recover()
    await runtime.close()
