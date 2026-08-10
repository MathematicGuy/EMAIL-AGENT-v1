"""Runtime configuration loaded from environment variables."""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"


def database_url(environ: Mapping[str, str] | None = None) -> str:
    """PostgreSQL connection URL (V1-H); empty string keeps local adapters."""
    source = os.environ if environ is None else environ
    return source.get("DATABASE_URL", "").strip()


def redis_url(environ: Mapping[str, str] | None = None) -> str:
    """Redis connection URL (V1-H T5.2); empty keeps BackgroundTasks dispatch."""
    source = os.environ if environ is None else environ
    return source.get("REDIS_URL", "").strip()


@dataclass(frozen=True, slots=True)
class ChatMemorySettings:
    """Bounded local working-memory policy for V2 chat sessions."""

    max_turns: int
    ttl_seconds: int

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        load_env_file: bool = True,
    ) -> "ChatMemorySettings":
        if environ is None:
            if load_env_file:
                load_dotenv(override=False)
            environ = os.environ
        return cls(
            max_turns=_positive_int(environ, "CHAT_MEMORY_MAX_TURNS", 20),
            ttl_seconds=_positive_int(environ, "CHAT_MEMORY_TTL_SECONDS", 1800),
        )


@dataclass(frozen=True, slots=True)
class QdrantSettings:
    """Qdrant vector store configuration for Semantic Memory retrieval.

    Absent configuration is not an error: ``enabled`` is false when
    ``QDRANT_URL`` is empty, and the RAG bootstrap degrades to
    ``NullSemanticMemory`` rather than blocking a digest run (invariant 4).
    """

    url: str
    api_key: str = field(repr=False)
    collection_name: str
    enabled: bool
    vector_size: int
    reindex: bool

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        load_env_file: bool = True,
    ) -> "QdrantSettings":
        if environ is None:
            if load_env_file:
                load_dotenv(override=False)
            environ = os.environ
        url = environ.get("QDRANT_URL", "").strip()
        if url.startswith("replace-with-"):
            url = ""
        return cls(
            url=url,
            api_key=environ.get("QDRANT_API_KEY", "").strip(),
            collection_name=environ.get("QDRANT_COLLECTION", "company_knowledge").strip()
            or "company_knowledge",
            enabled=bool(url) and _boolean(environ, "QDRANT_ENABLED", False),
            vector_size=_positive_int(environ, "QDRANT_VECTOR_SIZE", 768),
            reindex=_boolean(environ, "QDRANT_REINDEX", False),
        )


@dataclass(frozen=True, slots=True)
class GmailSettings:
    client_id: str = field(repr=False)
    client_secret: str = field(repr=False)
    redirect_uri: str
    scopes: tuple[str, ...]
    connection_db_path: Path
    token_encryption_key: str = field(repr=False)
    oauth_state_secret: str = field(repr=False)
    oauth_state_ttl_seconds: int

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        load_env_file: bool = True,
    ) -> "GmailSettings":
        if environ is None:
            if load_env_file:
                load_dotenv(override=False)
            environ = os.environ

        client_id = _required_secret(environ, "GMAIL_CLIENT_ID")
        client_secret = _required_secret(environ, "GMAIL_CLIENT_SECRET")
        encryption_key = _required_secret(environ, "TOKEN_ENCRYPTION_KEY")
        state_secret = _required_secret(environ, "OAUTH_STATE_SECRET")
        redirect_uri = environ.get(
            "GMAIL_REDIRECT_URI",
            "http://localhost:8000/v1/mail-todo/oauth/gmail/callback",
        ).strip()
        if not redirect_uri.startswith(("http://localhost", "https://")):
            raise ValueError("GMAIL_REDIRECT_URI must use HTTPS, except for localhost")
        scopes = tuple(environ.get("GMAIL_SCOPES", GMAIL_READONLY_SCOPE).split())
        if scopes != (GMAIL_READONLY_SCOPE,):
            raise ValueError("Gmail v1 must use only the gmail.readonly scope")
        return cls(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            scopes=scopes,
            connection_db_path=Path(environ.get("GMAIL_CONNECTION_DB_PATH", ".data/mail_todo.db")),
            token_encryption_key=encryption_key,
            oauth_state_secret=state_secret,
            oauth_state_ttl_seconds=_positive_int(environ, "OAUTH_STATE_TTL_SECONDS", 600),
        )


@dataclass(frozen=True, slots=True)
class GeminiSettings:
    api_keys: tuple[str, ...] = field(repr=False)
    model: str
    rotate_on_rate_limit: bool
    max_attempts: int
    max_emails_per_batch: int
    max_input_tokens: int
    timeout_seconds: int

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        load_env_file: bool = True,
    ) -> "GeminiSettings":
        if environ is None:
            if load_env_file:
                load_dotenv(override=False)
            environ = os.environ

        keys = tuple(
            value.strip()
            for index in range(1, 4)
            if (value := environ.get(f"GEMINI_API_KEY_{index}", "")).strip()
            and not value.strip().startswith("replace-with-")
        )
        if not keys:
            raise ValueError("At least one GEMINI_API_KEY_1..3 must be configured")
        if len(set(keys)) != len(keys):
            raise ValueError("GEMINI_API_KEY_1..3 must be unique")

        strategy = environ.get("GEMINI_KEY_ROTATION_STRATEGY", "round_robin").lower()
        if strategy != "round_robin":
            raise ValueError("Only round_robin Gemini key rotation is supported")

        max_attempts = _positive_int(environ, "GEMINI_MAX_ATTEMPTS_PER_REQUEST", 3)
        model = environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite").strip()
        if not model or model.startswith("replace-with-"):
            raise ValueError("GEMINI_MODEL must be a real Gemini model name")
        return cls(
            api_keys=keys,
            model=model,
            rotate_on_rate_limit=_boolean(environ, "GEMINI_ROTATE_ON_RATE_LIMIT", True),
            max_attempts=min(max_attempts, len(keys)),
            max_emails_per_batch=_positive_int(
                environ, "GEMINI_MAX_EMAILS_PER_BATCH", 5
            ),
            max_input_tokens=_positive_int(environ, "GEMINI_MAX_INPUT_TOKENS", 40_000),
            timeout_seconds=_positive_int(environ, "GEMINI_TIMEOUT_SECONDS", 60),
        )


@dataclass(frozen=True, slots=True)
class GroqSettings:
    api_key: str = field(repr=False)
    model: str
    max_emails_per_batch: int
    timeout_seconds: int

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        load_env_file: bool = True,
    ) -> "GroqSettings":
        if environ is None:
            if load_env_file:
                load_dotenv(override=False)
            environ = os.environ
        model = environ.get("GROQ_MODEL", "qwen/qwen3.6-27b").strip()
        if not model or model.startswith("replace-with-"):
            raise ValueError("GROQ_MODEL must be a real Groq model name")
        return cls(
            api_key=_required_secret(environ, "GROQ_API_KEY"),
            model=model,
            max_emails_per_batch=_positive_int(environ, "GROQ_MAX_EMAILS_PER_BATCH", 5),
            timeout_seconds=_positive_int(environ, "GROQ_TIMEOUT_SECONDS", 60),
        )


@dataclass(frozen=True, slots=True)
class FaucetSettings:
    """Fixed-endpoint configuration for the Faucet chat-completions provider."""

    api_key: str = field(repr=False)
    model: str
    max_emails_per_batch: int
    max_output_tokens: int
    timeout_seconds: int

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        load_env_file: bool = True,
    ) -> "FaucetSettings":
        if environ is None:
            if load_env_file:
                load_dotenv(override=False)
            environ = os.environ
        model = environ.get("FAUCET_MODEL", "").strip()
        if not model or model.startswith("replace-with-"):
            raise ValueError("FAUCET_MODEL must be a real Faucet model name")
        return cls(
            api_key=_required_secret(environ, "FAUCET_API_KEY"),
            model=model,
            max_emails_per_batch=_positive_int(environ, "FAUCET_MAX_EMAILS_PER_BATCH", 5),
            max_output_tokens=_bounded_positive_int(
                environ, "FAUCET_MAX_OUTPUT_TOKENS", 2048, maximum=4096
            ),
            timeout_seconds=_bounded_positive_int(
                environ, "FAUCET_TIMEOUT_SECONDS", 60, maximum=120
            ),
        )


def _positive_int(environ: Mapping[str, str], name: str, default: int) -> int:
    value = int(environ.get(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _bounded_positive_int(
    environ: Mapping[str, str], name: str, default: int, *, maximum: int
) -> int:
    value = _positive_int(environ, name, default)
    if value > maximum:
        raise ValueError(f"{name} must not exceed {maximum}")
    return value


def _boolean(environ: Mapping[str, str], name: str, default: bool) -> bool:
    value = environ.get(name, str(default)).strip().lower()
    if value not in {"true", "false"}:
        raise ValueError(f"{name} must be true or false")
    return value == "true"


def _required_secret(environ: Mapping[str, str], name: str) -> str:
    value = environ.get(name, "").strip()
    if not value or value.startswith("replace-with-"):
        raise ValueError(f"{name} must be configured")
    return value
