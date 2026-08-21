#!/usr/bin/env python3
"""Pre-flight checks for the memory evaluation. See evaluations/MEMORIES/RUNBOOK.md.

Every check answers the same question: can this run produce a report a person is
allowed to believe?

The failure this exists for is a dependency that is *configured* but not
*working*. A present-but-exhausted embedding key does not crash the harness — it
produces a report in which semantic memory found nothing, which is the exact
confusion SPEC 12.2 rule 2 forbids. Checking that a key is set proves nothing;
these checks spend one call to prove each dependency answers.

Exit codes:
  0 - nothing failed; warnings may still be present
  1 - at least one check failed; do not run the evaluation
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

OK = "ok"
WARN = "warn"
FAIL = "fail"

#: A database name that does not look like a throwaway gets a warning. The
#: harness fills memory and then deletes it, so a name suggesting a real store
#: is worth a second look even when the host is local.
_THROWAWAY_MARKERS = ("memeval", "test", "scratch", "tmp")


@dataclass(frozen=True, slots=True)
class Check:
    """One question about the environment, and what answering it found."""

    name: str
    status: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"name": self.name, "status": self.status, "detail": self.detail}


def cause_chain(error: BaseException) -> str:
    """Every exception in `error`'s cause chain, outermost first.

    The chat adapters raise `ChatReplyUnavailable("configured chat provider is
    unavailable")` from whatever actually went wrong, so the message a caller
    sees names no cause at all. A run that fails this way reports twenty-four
    identical unusable arms and no reason for any of them.
    """

    parts: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        parts.append(f"{type(current).__name__}: {current}")
        current = current.__cause__ or current.__context__
    return " <- ".join(parts)


def describe_target(url: str) -> str:
    """Host and database of `url`, never its password."""

    parts = urlsplit(url)
    return f"{parts.hostname or '<none>'}:{parts.port or 5432}/{(parts.path or '/').lstrip('/')}"


def looks_throwaway(url: str) -> bool:
    """Whether the database name says it is disposable."""

    name = (urlsplit(url).path or "").lstrip("/").casefold()
    return any(marker in name for marker in _THROWAWAY_MARKERS)


def exit_code(checks: Sequence[Check]) -> int:
    """0 unless something failed. A warning is for a person to weigh, not a stop."""

    return 1 if any(check.status == FAIL for check in checks) else 0


def check_checkout(root: Path) -> Check:
    """That we are in a checkout holding this harness, not somewhere adjacent."""

    required = (
        root / "scripts" / "evaluate_memory.py",
        root / "evaluations" / "MEMORIES" / "RUNBOOK.md",
        root / "evaluations" / "MEMORIES" / "probes",
    )
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        return Check("checkout", FAIL, f"not the harness root; missing {missing}")
    return Check("checkout", OK, str(root))


def check_probe_set(path: Path) -> Check:
    """That the question file loads and validates before a single model call."""

    from cowork_agent.features.ai_chat.memory_eval.probes import load_probe_set

    try:
        probe_set = load_probe_set(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError) as error:
        return Check("probe_set", FAIL, f"{path}: {error}")
    return Check(
        "probe_set",
        OK,
        f"{probe_set.probe_set_id}, {len(probe_set.probes)} questions, from {path}",
    )


def check_target(environ: Mapping[str, str]) -> tuple[Check, str]:
    """Which store this run would write to, and whether it is allowed to.

    Repeats `probe_environment`'s guard deliberately. Reading the answer here,
    before anything is seeded, is the difference between a refusal and a
    write-and-delete against a database somebody cares about.
    """

    from cowork_agent.features.ai_chat.memory_eval.live_env import (
        ALLOW_REMOTE_ENV_VAR,
        is_local_postgres,
        resolve_postgres_url,
    )

    url = resolve_postgres_url(environ)
    if not url:
        return (
            Check("target", WARN, "no PostgreSQL configured; the run would use scratch SQLite"),
            "",
        )
    where = describe_target(url)
    if environ.get(ALLOW_REMOTE_ENV_VAR) == "1":
        return Check("target", FAIL, f"{ALLOW_REMOTE_ENV_VAR}=1 is set; unset it ({where})"), url
    if not is_local_postgres(url):
        return Check("target", FAIL, f"{where} is not local; the harness would be refused"), url
    if not looks_throwaway(url):
        return Check("target", WARN, f"{where} is local but not named like a throwaway"), url
    return Check("target", OK, where), url


def check_postgres(url: str) -> tuple[Check, ...]:
    """That the target answers, and that no killed run left its migration lock held."""

    if not url:
        return ()
    try:
        import psycopg
    except ImportError:  # pragma: no cover - psycopg is a hard dependency here
        return (Check("postgres", FAIL, "psycopg is not installed in this interpreter"),)
    try:
        with psycopg.connect(url, connect_timeout=3) as connection:
            version = connection.execute("SELECT version()").fetchone()
            blocked = connection.execute(
                # A killed pytest or evaluation run can leave the migration
                # advisory lock held by an idle backend. Advisory locks are
                # session-scoped and survive rollback, so the next run blocks
                # on apply_migrations forever and looks like a hung provider.
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE datname = current_database() AND pid <> pg_backend_pid() "
                "AND state = 'idle in transaction'"
            ).fetchone()
    except psycopg.Error as error:
        return (Check("postgres", FAIL, f"{describe_target(url)}: {error}"),)
    server = str(version[0]).split(" on ")[0] if version else "unknown"
    checks = [Check("postgres", OK, f"{describe_target(url)} - {server}")]
    idle = int(blocked[0]) if blocked else 0
    if idle:
        checks.append(
            Check(
                "postgres_locks",
                WARN,
                f"{idle} backend(s) idle in transaction on this database; a killed run may "
                f"still hold the schema_migrations advisory lock (see RUNBOOK step 6)",
            )
        )
    return tuple(checks)


def check_embeddings(environ: Mapping[str, str], *, live: bool) -> Check:
    """That the corpus can actually be embedded, not merely that a key is set."""

    from cowork_agent.config import document_embedding_provider
    from cowork_agent.features.ai_chat.memory_eval.live_env import probe_environment

    provider = document_embedding_provider(environ, load_env_file=False)
    env = probe_environment(dict(environ))
    if not env.embeddings_ready:
        return Check("embeddings", FAIL, f"provider {provider}: no {env.embedding_key_name}")
    if not live:
        return Check("embeddings", WARN, f"provider {provider}: key present, not called")
    from cowork_agent.features.ai_chat.memory_eval.live_env import run_with_selector_loop
    from cowork_agent.integrations.rag.bootstrap import build_document_embedder

    try:
        embedder, dimensions = build_document_embedder()
        vectors = run_with_selector_loop(embedder.embed(["kiểm tra trước khi chạy"]))
    except Exception as error:  # noqa: BLE001 - any failure here is the finding
        return Check("embeddings", FAIL, f"provider {provider}: {cause_chain(error)}")
    if not vectors or len(vectors[0]) != dimensions:
        got = len(vectors[0]) if vectors else 0
        return Check("embeddings", FAIL, f"provider {provider}: got {got} dims, want {dimensions}")
    return Check("embeddings", OK, f"provider {provider}: {dimensions}-dim vector returned")


def _one_reply(reply: Any) -> str:
    """Ask the configured provider one throwaway question and return its answer."""

    from cowork_agent.domain.chat_contracts import ChatMessageRequest, MemoryContextResponse
    from cowork_agent.features.ai_chat.generation_context import assemble_generation_context
    from cowork_agent.features.ai_chat.memory_eval.live_env import run_with_selector_loop

    request = ChatMessageRequest(
        session_id="memeval-preflight",
        user_message="Xin chào, bạn có đang hoạt động không?",
        idempotency_key="memeval-preflight",
    )
    context = assemble_generation_context(
        request, MemoryContextResponse((), None, (), None, False, ())
    )

    async def ask() -> str:
        texts = [chunk.text async for chunk in reply.stream_reply(request, context)]
        return "".join(texts)

    return run_with_selector_loop(ask())


def check_chat(provider: str | None, environ: Mapping[str, str], *, live: bool) -> Check:
    """That the model answers. Without a reply there is no run at all."""

    from scripts.evaluate_memory import _build_chat_reply, _default_provider

    name = provider or _default_provider(environ)
    try:
        reply, name, model = _build_chat_reply(name, dict(environ))
    except (ValueError, KeyError) as error:
        return Check("chat", FAIL, f"{name} is configured but unusable: {error}")
    if not live:
        return Check("chat", WARN, f"{name}/{model}: built, not called")
    try:
        text = _one_reply(reply)
    except Exception as error:  # noqa: BLE001 - any failure here is the finding
        return Check("chat", FAIL, f"{name}/{model}: {cause_chain(error)}")
    if not text.strip():
        return Check("chat", FAIL, f"{name}/{model}: answered with empty text")
    return Check("chat", OK, f"{name}/{model}: {len(text)} characters returned")


def run_checks(
    environ: Mapping[str, str],
    *,
    root: Path,
    probe_set: Path,
    provider: str | None,
    live: bool,
) -> tuple[Check, ...]:
    """Every check, in the order a failure should stop you."""

    from cowork_agent.features.ai_chat.memory_eval.live_env import UnsafeTargetError

    checks = [check_checkout(root)]
    if checks[0].status == FAIL:
        return tuple(checks)
    checks.append(check_probe_set(probe_set))
    target, url = check_target(environ)
    checks.append(target)
    if target.status != FAIL:
        checks.extend(check_postgres(url))
        try:
            checks.append(check_embeddings(environ, live=live))
        except UnsafeTargetError as error:
            checks.append(Check("embeddings", FAIL, str(error)))
    checks.append(check_chat(provider, environ, live=live))
    return tuple(checks)


def render(checks: Sequence[Check]) -> str:
    """A table a person reads, worst thing first in each line."""

    marks = {OK: "PASS", WARN: "WARN", FAIL: "FAIL"}
    width = max((len(check.name) for check in checks), default=0)
    lines = [f"{marks[c.status]}  {c.name.ljust(width)}  {c.detail}" for c in checks]
    failed = [check.name for check in checks if check.status == FAIL]
    warned = [check.name for check in checks if check.status == WARN]
    lines.append("")
    if failed:
        lines.append("NOT READY - " + ", ".join(failed))
    elif warned:
        lines.append("READY, with something to weigh first - " + ", ".join(warned))
    else:
        lines.append("READY - every dependency answered")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    from scripts.evaluate_memory import resolve_latest_probe_set

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--probe-set",
        type=Path,
        default=None,
        help="Path to probe set JSON definition; defaults to the latest version found in probes/.",
    )
    parser.add_argument("--provider", help="Chat provider to check; defaults to LLM_PROVIDER.")
    parser.add_argument(
        "--no-live",
        action="store_true",
        help="Skip the two calls that prove the keys work. Downgrades them to warnings.",
    )
    parser.add_argument("--json", action="store_true", help="Machine-readable output.")
    args = parser.parse_args(argv)

    probe_set_path = args.probe_set or resolve_latest_probe_set()
    checks = run_checks(
        os.environ,
        root=Path.cwd(),
        probe_set=probe_set_path,
        provider=args.provider,
        live=not args.no_live,
    )
    if args.json:
        print(json.dumps([check.as_dict() for check in checks], indent=2, ensure_ascii=False))
    else:
        print(render(checks))
    return exit_code(checks)


if __name__ == "__main__":
    sys.path.insert(0, str(Path.cwd()))
    from cowork_agent.config import load_runtime_environment

    load_runtime_environment()
    raise SystemExit(main())
