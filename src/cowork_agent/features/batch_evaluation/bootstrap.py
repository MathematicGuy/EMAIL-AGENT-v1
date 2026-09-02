"""Composition root for the local pluggable evaluation runtime."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from cowork_agent.features.ai_chat.memory_eval.live_env import probe_environment
from cowork_agent.features.batch_evaluation.artifacts import FilesystemEvaluationArtifactStore
from cowork_agent.features.batch_evaluation.credentials import CredentialLeasingPool
from cowork_agent.features.batch_evaluation.plugins.memory_eval import MemoryEvalPlugin
from cowork_agent.features.batch_evaluation.registry import PluginRegistry
from cowork_agent.features.batch_evaluation.runner import (
    EvaluationJobRunner,
    RunnerRepository,
)
from cowork_agent.features.batch_evaluation.service import EvaluationJobService
from cowork_agent.features.batch_evaluation.supervisor import EvaluationSupervisor
from cowork_agent.integrations.llm.evaluation_mistral import MistralEvaluationReplyFactory
from cowork_agent.persistence.repositories.evaluation_jobs import SQLiteEvaluationJobRepository


@dataclass(frozen=True, slots=True)
class EvaluationRuntimeConfig:
    """Filesystem locations owned by one local evaluation runtime."""

    job_db_path: Path
    artifact_root: Path
    scratch_root: Path | None = None

    def __post_init__(self) -> None:
        job_db_path = _resolve_path(self.job_db_path, "job_db_path")
        artifact_root = _resolve_path(self.artifact_root, "artifact_root")
        scratch_root = (
            artifact_root / ".runtime" / "scratch"
            if self.scratch_root is None
            else _resolve_path(self.scratch_root, "scratch_root")
        )
        if job_db_path.exists() and job_db_path.is_dir():
            raise ValueError("job_db_path must name a file")
        if artifact_root.exists() and not artifact_root.is_dir():
            raise ValueError("artifact_root must name a directory")
        if scratch_root == artifact_root or not _is_descendant(scratch_root, artifact_root):
            raise ValueError("scratch_root must be a directory below artifact_root")
        object.__setattr__(self, "job_db_path", job_db_path)
        object.__setattr__(self, "artifact_root", artifact_root)
        object.__setattr__(self, "scratch_root", scratch_root)


@dataclass(slots=True)
class EvaluationRuntime:
    """Own Level 1 runtime resources and their local lifecycle."""

    service: EvaluationJobService = field(repr=False)
    supervisor: EvaluationSupervisor = field(repr=False)
    repository: SQLiteEvaluationJobRepository = field(repr=False)
    credential_pool: CredentialLeasingPool = field(repr=False)
    artifact_store: FilesystemEvaluationArtifactStore = field(repr=False)

    async def initialize(self) -> None:
        """Initialize durable local storage before submitting or recovering work."""

        await self.repository.initialize()

    async def recover(self) -> None:
        """Resume durable work only after local storage has been initialized."""

        await self.supervisor.recover()

    async def close(self) -> None:
        """Cancel local runners and wait for their cleanup."""

        await self.supervisor.close()


def build_evaluation_runtime(
    config: EvaluationRuntimeConfig, environ: Mapping[str, str]
) -> EvaluationRuntime:
    """Build the static Mistral memory-evaluation runtime without logging secrets."""

    probe_environ = _environment_probe_environ(environ)
    registry = PluginRegistry()
    registry.register(
        MemoryEvalPlugin(environment_resolver=lambda: probe_environment(probe_environ))
    )
    repository = SQLiteEvaluationJobRepository(config.job_db_path)
    credential_pool = CredentialLeasingPool.from_env("MISTRAL_API_KEY", environ)
    artifact_store = FilesystemEvaluationArtifactStore(config.artifact_root)
    scratch_root = config.scratch_root
    assert scratch_root is not None
    runner = EvaluationJobRunner(
        registry=registry,
        repository=cast(RunnerRepository, repository),
        credential_pool=credential_pool,
        artifact_store=artifact_store,
        scratch_root=scratch_root,
        reply_factory=MistralEvaluationReplyFactory(),
    )
    supervisor = EvaluationSupervisor(repository=repository, runner=runner)
    service = EvaluationJobService(
        registry=registry,
        repository=repository,
        credential_pool=credential_pool,
        artifact_store=artifact_store,
        supervisor=supervisor,
    )
    return EvaluationRuntime(
        service=service,
        supervisor=supervisor,
        repository=repository,
        credential_pool=credential_pool,
        artifact_store=artifact_store,
    )


def _resolve_path(value: Path, name: str) -> Path:
    if not isinstance(value, Path):
        raise TypeError(f"{name} must be a Path")
    return value.resolve()


def _is_descendant(candidate: Path, parent: Path) -> bool:
    try:
        candidate.relative_to(parent)
    except ValueError:
        return False
    return True


def _environment_probe_environ(environ: Mapping[str, str]) -> dict[str, str]:
    """Keep Mistral credentials in the pool while preserving probe semantics."""

    probe_environ = {
        name: value for name, value in environ.items() if not name.startswith("MISTRAL_API_KEY")
    }
    for key_name in ("GEMINI_API_KEY", "JINA_API_KEY"):
        if probe_environ.get(key_name):
            probe_environ[key_name] = "configured"
    return probe_environ
