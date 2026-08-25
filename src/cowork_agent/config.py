"""Runtime configuration loaded from environment variables."""

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from dotenv import load_dotenv

from cowork_agent.integrations.key_rotation import APIKeyRotator

if TYPE_CHECKING:
    from cowork_agent.features.batch_evaluation.bootstrap import EvaluationRuntimeConfig

GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
MICROSOFT_MAIL_READ_SCOPE = "https://graph.microsoft.com/Mail.Read"
MICROSOFT_DEFAULT_SCOPES = (
    "openid",
    "profile",
    "email",
    "offline_access",
    MICROSOFT_MAIL_READ_SCOPE,
)
LOCAL_POSTGRES_DEFAULT_URL = "postgresql://cowork:cowork_dev_only@127.0.0.1:5432/cowork"
_POSTGRES_MODES = frozenset({"local", "cloud", "off"})


def load_runtime_environment(directory: Path | None = None) -> None:
    """Load secrets from ``.env`` and non-secret feature flags from ``config``."""
    root = Path.cwd() if directory is None else directory
    # Neither file overrides a variable the process already has: the shell (and
    # a test's monkeypatch) stays authoritative, which is what keeps an
    # integration test from silently reaching the real Supabase database.
    load_dotenv(root / ".env", override=False)
    load_dotenv(root / "config", override=False)


def postgres_mode(environ: Mapping[str, str] | None = None) -> str:
    """Control-plane target: ``local``, ``cloud``, ``off``, or empty (legacy)."""
    source = os.environ if environ is None else environ
    mode = source.get("POSTGRES_MODE", "").strip().lower()
    if not mode:
        return ""
    if mode not in _POSTGRES_MODES:
        raise ValueError("POSTGRES_MODE must be 'local', 'cloud', or 'off'")
    return mode


def database_url(environ: Mapping[str, str] | None = None) -> str:
    """PostgreSQL connection URL; empty string selects local SQLite adapters.

    ``POSTGRES_MODE`` selects the URL when set and wins over ``DATABASE_URL``:

    * ``local`` — ``DATABASE_URL_LOCAL``, else the Docker Compose app DB
    * ``cloud`` — ``DATABASE_URL_CLOUD`` (session or direct ``:5432``)
    * ``off`` — empty (SQLite mailbox/runs/tasks/chat history/chat memory)

    With no mode, ``DATABASE_URL`` is used unchanged (tests and older .env files).
    """
    source = os.environ if environ is None else environ
    mode = postgres_mode(source)
    if mode == "off":
        return ""
    if mode == "local":
        return source.get("DATABASE_URL_LOCAL", "").strip() or LOCAL_POSTGRES_DEFAULT_URL
    if mode == "cloud":
        url = source.get("DATABASE_URL_CLOUD", "").strip()
        if not url:
            raise ValueError("POSTGRES_MODE=cloud requires DATABASE_URL_CLOUD")
        port = urlsplit(url).port
        if port == 6543:
            raise ValueError(
                "POSTGRES_MODE=cloud must use session or direct :5432, "
                "not Supavisor transaction :6543"
            )
        return url
    return source.get("DATABASE_URL", "").strip()


@dataclass(frozen=True, slots=True)
class SupabaseStorageSettings:
    """Server-only private Supabase Storage configuration."""

    url: str
    secret_key: str = field(repr=False)
    bucket: str = "project-documents"

    @classmethod
    def from_env(
        cls, environ: Mapping[str, str] | None = None, *, load_env_file: bool = True
    ) -> "SupabaseStorageSettings":
        if environ is None:
            if load_env_file:
                load_runtime_environment()
            environ = os.environ
        url = environ.get("SUPABASE_URL", "").strip().rstrip("/")
        if not url.startswith("https://"):
            raise ValueError("SUPABASE_URL must use HTTPS")
        return cls(
            url=url,
            secret_key=_required_secret(environ, "SUPABASE_SECRET_KEY"),
            bucket=_non_empty_value(environ, "SUPABASE_STORAGE_BUCKET", "project-documents"),
        )


@dataclass(frozen=True, slots=True)
class SessionSettings:
    """Opaque FastAPI session-cookie policy."""

    session_ttl_seconds: int
    cookie_name: str
    cookie_secure: bool

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        load_env_file: bool = True,
    ) -> "SessionSettings":
        if environ is None:
            if load_env_file:
                load_runtime_environment()
            environ = os.environ
        cookie_name = environ.get("APP_SESSION_COOKIE_NAME", "cowork_session").strip()
        if not cookie_name:
            raise ValueError("APP_SESSION_COOKIE_NAME must not be empty")
        # Local HTTP on 127.0.0.1 drops Secure cookies in some browsers.
        cookie_secure_default = postgres_mode(environ) not in {"local", "off"}
        return cls(
            session_ttl_seconds=_positive_int(environ, "APP_SESSION_TTL_SECONDS", 2_592_000),
            cookie_name=cookie_name,
            cookie_secure=_boolean(
                environ, "APP_SESSION_COOKIE_SECURE", cookie_secure_default
            ),
        )


@dataclass(frozen=True, slots=True)
class SecuritySettings:
    """Configuration for email security scanning, threat feeds, and quarantine policy."""

    enabled: bool
    webrisk_api_key: str = field(repr=False)
    cache_ttl_seconds: int
    quarantine_malicious_emails: bool
    virustotal_api_key: str = field(default="", repr=False)
    malwarebazaar_enabled: bool = True

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        load_env_file: bool = True,
    ) -> "SecuritySettings":
        if environ is None:
            if load_env_file:
                load_runtime_environment()
            environ = os.environ
        return cls(
            enabled=_boolean(environ, "SECURITY_SCAN_ENABLED", True),
            webrisk_api_key=environ.get("SECURITY_WEBRISK_API_KEY", "").strip(),
            cache_ttl_seconds=_positive_int(environ, "SECURITY_CACHE_TTL_SECONDS", 86_400),
            quarantine_malicious_emails=_boolean(
                environ, "SECURITY_QUARANTINE_ENABLED", True
            ),
            virustotal_api_key=environ.get("SECURITY_VIRUSTOTAL_API_KEY", "").strip(),
            malwarebazaar_enabled=_boolean(
                environ, "SECURITY_MALWAREBAZAAR_ENABLED", True
            ),
        )


@dataclass(frozen=True, slots=True)
class KnowledgeIngestionSettings:
    """Configuration for the administrator-operated knowledge ingestion CLI."""

    api_key: str = field(repr=False)
    ocr_enabled: bool
    model: str
    timeout_seconds: int
    max_attempts: int
    max_bytes: int
    max_pdf_pages: int
    max_ocr_pages: int
    extraction_mode: str = "adaptive"

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        load_env_file: bool = True,
    ) -> "KnowledgeIngestionSettings":
        if environ is None:
            if load_env_file:
                load_runtime_environment()
            environ = os.environ

        extraction_mode_env = environ.get("EXTRACTION_MODE", "").strip().lower()
        if extraction_mode_env in ("advance", "advanced"):
            ocr_enabled = True
            extraction_mode = "advance"
        elif extraction_mode_env in ("adaptive", "basic", "simple"):
            ocr_enabled = False
            extraction_mode = "adaptive"
        elif extraction_mode_env:
            msg = (
                f"Invalid EXTRACTION_MODE: {extraction_mode_env}. "
                "Must be 'adaptive' or 'advance'."
            )
            raise ValueError(msg)
        else:
            ocr_enabled = _boolean(environ, "KNOWLEDGE_INGEST_OCR_ENABLED", True)
            extraction_mode = "advance" if ocr_enabled else "adaptive"

        api_key = environ.get("MISTRAL_API_KEY", "").strip()
        if ocr_enabled and (not api_key or api_key.startswith("replace-with-")):
            raise ValueError("MISTRAL_API_KEY must be configured when OCR is enabled")
        return cls(
            api_key=api_key,
            ocr_enabled=ocr_enabled,
            model=_non_empty_value(environ, "KNOWLEDGE_INGEST_MODEL", "mistral-ocr-latest"),
            timeout_seconds=_positive_int(environ, "KNOWLEDGE_INGEST_TIMEOUT_SECONDS", 60),
            max_attempts=_positive_int(environ, "KNOWLEDGE_INGEST_MAX_ATTEMPTS", 3),
            max_bytes=_positive_int(environ, "KNOWLEDGE_INGEST_MAX_BYTES", 26_214_400),
            max_pdf_pages=_positive_int(environ, "KNOWLEDGE_INGEST_MAX_PDF_PAGES", 100),
            max_ocr_pages=_positive_int(environ, "KNOWLEDGE_INGEST_MAX_OCR_PAGES", 100),
            extraction_mode=extraction_mode,
        )


@dataclass(frozen=True, slots=True)
class ChatMemorySettings:
    """Bounded local working-memory policy for V2 chat sessions."""

    max_turns: int
    ttl_seconds: int
    profile_retention_seconds: int | None = None
    episode_retention_seconds: int | None = None

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        load_env_file: bool = True,
    ) -> "ChatMemorySettings":
        if environ is None:
            if load_env_file:
                load_runtime_environment()
            environ = os.environ
        return cls(
            max_turns=_positive_int(environ, "CHAT_MEMORY_MAX_TURNS", 20),
            ttl_seconds=_positive_int(environ, "CHAT_MEMORY_TTL_SECONDS", 1800),
            profile_retention_seconds=_optional_retention_seconds(
                environ, "CHAT_PROFILE_RETENTION_SECONDS"
            ),
            episode_retention_seconds=_optional_retention_seconds(
                environ, "CHAT_EPISODE_RETENTION_SECONDS"
            ),
        )


@dataclass(frozen=True, slots=True)
class ChatIntentSettings:
    """Classifier routing policy for user-document-aware AI Chat turns."""

    enabled: bool
    model: str
    timeout_ms: int
    max_attempts: int
    tool_axis_enabled: bool
    company_rag_enabled: bool

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        default_model: str,
        load_env_file: bool = True,
    ) -> "ChatIntentSettings":
        if environ is None:
            if load_env_file:
                load_runtime_environment()
            environ = os.environ
        model = environ.get("CHAT_INTENT_CLASSIFIER_MODEL", "").strip() or default_model
        if not model or model.startswith("replace-with-"):
            raise ValueError("CHAT_INTENT_CLASSIFIER_MODEL must be a real model name")
        timeout_ms = _positive_int(environ, "CHAT_INTENT_CLASSIFIER_TIMEOUT_MS", 10_000)
        if timeout_ms > 120_000:
            raise ValueError("CHAT_INTENT_CLASSIFIER_TIMEOUT_MS must not exceed 120000")
        return cls(
            enabled=_boolean(environ, "CHAT_INTENT_CLASSIFIER_ENABLED", True),
            model=model,
            timeout_ms=timeout_ms,
            max_attempts=2,
            tool_axis_enabled=_boolean(environ, "USER_DOCUMENTS_TOOL_AXIS_ENABLED", False),
            company_rag_enabled=_boolean(environ, "CHAT_COMPANY_RAG_ENABLED", False),
        )


@dataclass(frozen=True, slots=True)
class UserDocumentsSettings:
    """Project-document plane limits and dependency configuration."""

    enabled: bool
    index_root: str
    max_file_bytes: int
    max_pages: int
    max_documents_per_project: int
    max_project_bytes: int
    retention_days: int
    top_k: int
    min_score: float
    retrieval_timeout_ms: int
    ingestion_stream: str

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        load_env_file: bool = True,
    ) -> "UserDocumentsSettings":
        if environ is None:
            if load_env_file:
                load_runtime_environment()
            environ = os.environ
        enabled = _boolean(environ, "USER_DOCUMENTS_ENABLED", True)
        min_score = float(environ.get("USER_DOCUMENTS_MIN_SCORE", "0.6"))
        if not 0 <= min_score <= 1:
            raise ValueError("USER_DOCUMENTS_MIN_SCORE must be between 0 and 1")
        return cls(
            enabled=enabled,
            # Local cache directory for the per-project Turbovec .tvim files
            # (ADR-008). The durable copy lives in Supabase Storage; this is
            # only where each process materializes it.
            index_root=_non_empty_value(
                environ, "USER_DOCUMENTS_INDEX_ROOT", "var/project-indexes"
            ),
            max_file_bytes=_positive_int(
                environ, "USER_DOCUMENTS_MAX_FILE_BYTES", 25 * 1024 * 1024
            ),
            max_pages=_positive_int(environ, "USER_DOCUMENTS_MAX_PAGES", 100),
            max_documents_per_project=_positive_int(
                environ, "USER_DOCUMENTS_MAX_DOCUMENTS_PER_PROJECT", 50
            ),
            max_project_bytes=_positive_int(
                environ, "USER_DOCUMENTS_MAX_PROJECT_BYTES", 500 * 1024 * 1024
            ),
            retention_days=_positive_int(environ, "USER_DOCUMENTS_RETENTION_DAYS", 30),
            top_k=_bounded_positive_int(environ, "USER_DOCUMENTS_TOP_K", 8, maximum=20),
            min_score=min_score,
            retrieval_timeout_ms=_bounded_positive_int(
                environ, "USER_DOCUMENTS_RETRIEVAL_TIMEOUT_MS", 10_000, maximum=10_000
            ),
            ingestion_stream=_non_empty_value(
                environ, "USER_DOCUMENTS_INGESTION_STREAM", "cowork:project-document-ingestion"
            ),
        )


@dataclass(frozen=True, slots=True)
class GmailSettings:
    client_id: str = field(repr=False)
    client_secret: str = field(repr=False)
    redirect_uri: str
    frontend_url: str | None
    scopes: tuple[str, ...]
    connection_db_path: Path
    token_encryption_key: str = field(repr=False)
    oauth_state_secret: str = field(repr=False)
    oauth_state_ttl_seconds: int
    fetch_concurrency: int = 6

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        load_env_file: bool = True,
    ) -> "GmailSettings":
        if environ is None:
            if load_env_file:
                load_runtime_environment()
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
        frontend_url = environ.get("FRONTEND_URL", "").strip().rstrip("/") or None
        if frontend_url is not None:
            frontend_parts = urlsplit(frontend_url)
            secure_remote = frontend_parts.scheme == "https" and bool(frontend_parts.hostname)
            local_http = frontend_parts.scheme == "http" and frontend_parts.hostname in {
                "localhost",
                "127.0.0.1",
            }
            if not (secure_remote or local_http):
                raise ValueError("FRONTEND_URL must use HTTPS, except for localhost")
        scopes = tuple(environ.get("GMAIL_SCOPES", GMAIL_READONLY_SCOPE).split())
        if scopes != (GMAIL_READONLY_SCOPE,):
            raise ValueError("Gmail v1 must use only the gmail.readonly scope")
        return cls(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            frontend_url=frontend_url,
            scopes=scopes,
            connection_db_path=Path(environ.get("GMAIL_CONNECTION_DB_PATH", ".data/mail_todo.db")),
            token_encryption_key=encryption_key,
            oauth_state_secret=state_secret,
            oauth_state_ttl_seconds=_positive_int(environ, "OAUTH_STATE_TTL_SECONDS", 600),
            fetch_concurrency=_bounded_positive_int(
                environ, "GMAIL_FETCH_CONCURRENCY", 6, maximum=8
            ),
        )


@dataclass(frozen=True, slots=True)
class OutlookSettings:
    """Read-only Microsoft identity and Graph configuration.

    Outlook is an optional mailbox connector. The composition root decides
    whether the current persistence mode may enable it; settings validation
    only owns credential, URL, and least-privilege scope validation.
    """

    client_id: str = field(repr=False)
    client_secret: str = field(repr=False)
    tenant: str
    redirect_uri: str
    frontend_url: str | None
    scopes: tuple[str, ...]
    token_encryption_key: str = field(repr=False)
    oauth_state_secret: str = field(repr=False)
    oauth_state_ttl_seconds: int
    graph_base_url: str = "https://graph.microsoft.com/v1.0"

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        load_env_file: bool = True,
    ) -> "OutlookSettings":
        if environ is None:
            if load_env_file:
                load_runtime_environment()
            environ = os.environ

        redirect_uri = environ.get(
            "MICROSOFT_REDIRECT_URI",
            "http://localhost:8000/v1/mail-todo/oauth/outlook/callback",
        ).strip()
        if not redirect_uri.startswith(("http://localhost", "https://")):
            raise ValueError("MICROSOFT_REDIRECT_URI must use HTTPS, except for localhost")

        frontend_url = environ.get("FRONTEND_URL", "").strip().rstrip("/") or None
        if frontend_url is not None:
            frontend_parts = urlsplit(frontend_url)
            secure_remote = frontend_parts.scheme == "https" and bool(frontend_parts.hostname)
            local_http = frontend_parts.scheme == "http" and frontend_parts.hostname in {
                "localhost",
                "127.0.0.1",
            }
            if not (secure_remote or local_http):
                raise ValueError("FRONTEND_URL must use HTTPS, except for localhost")

        scopes = tuple(
            environ.get("MICROSOFT_SCOPES", " ".join(MICROSOFT_DEFAULT_SCOPES)).split()
        )
        if set(scopes) != set(MICROSOFT_DEFAULT_SCOPES) or len(scopes) != len(
            MICROSOFT_DEFAULT_SCOPES
        ):
            raise ValueError(
                "Outlook must use only Mail.Read and standard OIDC/offline scopes"
            )

        tenant = environ.get("MICROSOFT_TENANT", "common").strip() or "common"
        if any(character in tenant for character in "/?#"):
            raise ValueError("MICROSOFT_TENANT must be a tenant id, domain, or common")

        return cls(
            client_id=_required_secret(environ, "MICROSOFT_CLIENT_ID"),
            client_secret=_required_secret(environ, "MICROSOFT_CLIENT_SECRET"),
            tenant=tenant,
            redirect_uri=redirect_uri,
            frontend_url=frontend_url,
            scopes=scopes,
            token_encryption_key=_required_secret(environ, "TOKEN_ENCRYPTION_KEY"),
            oauth_state_secret=_required_secret(environ, "OAUTH_STATE_SECRET"),
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
    action_plan_concurrency: int = 3

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        load_env_file: bool = True,
    ) -> "GeminiSettings":
        if environ is None:
            if load_env_file:
                load_runtime_environment()
            environ = os.environ

        numbered_keys = sorted(
            (int(name.removeprefix("GEMINI_API_KEY_")), value)
            for name, value in environ.items()
            if name.startswith("GEMINI_API_KEY_")
            and name.removeprefix("GEMINI_API_KEY_").isdecimal()
        )
        keys = tuple(
            value.strip()
            for _, value in numbered_keys
            if value.strip() and not value.strip().startswith("replace-with-")
        )
        if not keys:
            raise ValueError("At least one numbered GEMINI_API_KEY must be configured")
        if len(set(keys)) != len(keys):
            raise ValueError("Numbered GEMINI_API_KEY values must be unique")

        strategy = environ.get("GEMINI_KEY_ROTATION_STRATEGY", "round_robin").lower()
        if strategy != "round_robin":
            raise ValueError("Only round_robin Gemini key rotation is supported")

        rotate_on_rate_limit = _boolean(environ, "GEMINI_ROTATE_ON_RATE_LIMIT", True)
        raw_max_attempts = _positive_int(environ, "GEMINI_MAX_ATTEMPTS_PER_REQUEST", 3)
        max_attempts = (
            max(raw_max_attempts, len(keys)) if rotate_on_rate_limit else raw_max_attempts
        )
        model = environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite").strip()
        if not model or model.startswith("replace-with-"):
            raise ValueError("GEMINI_MODEL must be a real Gemini model name")
        return cls(
            api_keys=keys,
            model=model,
            rotate_on_rate_limit=rotate_on_rate_limit,
            max_attempts=min(max_attempts, len(keys)),
            max_emails_per_batch=_positive_int(environ, "GEMINI_MAX_EMAILS_PER_BATCH", 5),
            max_input_tokens=_positive_int(environ, "GEMINI_MAX_INPUT_TOKENS", 40_000),
            timeout_seconds=_positive_int(environ, "GEMINI_TIMEOUT_SECONDS", 60),
            action_plan_concurrency=_bounded_positive_int(
                environ, "GEMINI_ACTION_PLAN_CONCURRENCY", 3, maximum=8
            ),
        )


@dataclass(frozen=True, slots=True)
class GeminiEmbeddingSettings:
    """Gemini configuration dedicated to project-document embeddings."""

    api_keys: tuple[str, ...] = field(repr=False)
    model: str
    dimensions: int
    timeout_seconds: int
    batch_size: int
    rotate_on_rate_limit: bool
    max_attempts: int

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        load_env_file: bool = True,
    ) -> "GeminiEmbeddingSettings":
        if environ is None:
            if load_env_file:
                load_runtime_environment()
            environ = os.environ
        generation = GeminiSettings.from_env(environ, load_env_file=False)
        dimensions = _bounded_positive_int(
            environ, "GEMINI_EMBEDDING_DIMENSIONS", 1024, maximum=3072
        )
        if dimensions < 128:
            raise ValueError("GEMINI_EMBEDDING_DIMENSIONS must be at least 128")
        return cls(
            api_keys=generation.api_keys,
            model=_non_empty_value(environ, "GEMINI_EMBEDDING_MODEL", "gemini-embedding-2"),
            dimensions=dimensions,
            timeout_seconds=_positive_int(environ, "GEMINI_EMBEDDING_TIMEOUT_SECONDS", 30),
            batch_size=_bounded_positive_int(
                environ, "GEMINI_EMBEDDING_BATCH_SIZE", 100, maximum=100
            ),
            rotate_on_rate_limit=generation.rotate_on_rate_limit,
            max_attempts=generation.max_attempts,
        )


def document_embedding_provider(
    environ: Mapping[str, str] | None = None,
    *,
    load_env_file: bool = True,
) -> str:
    """Resolve active document embedding provider ('gemini' | 'jina')."""
    if environ is None:
        if load_env_file:
            load_runtime_environment()
        environ = os.environ
    provider = environ.get("DOCUMENT_EMBEDDING_PROVIDER", "gemini").strip().lower()
    if provider in {"jina", "gemini"}:
        return provider
    return "gemini"


@dataclass(frozen=True, slots=True)
class JinaEmbeddingSettings:
    """Jina embedding API configuration with rate-limit key rotation."""

    rotator: APIKeyRotator = field(repr=False)
    model: str
    dimensions: int
    timeout_seconds: int
    rotate_on_rate_limit: bool = True
    max_attempts: int = 3

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        load_env_file: bool = True,
    ) -> "JinaEmbeddingSettings":
        if environ is None:
            if load_env_file:
                load_runtime_environment()
            environ = os.environ
        rotator = APIKeyRotator.from_env(
            "JINA_API_KEY", environ=environ, provider_name="Jina"
        )
        return cls(
            rotator=rotator,
            model=_non_empty_value(
                environ, "JINA_EMBEDDING_MODEL", "jina-embeddings-v5-omni-small"
            ),
            dimensions=_positive_int(environ, "JINA_EMBEDDING_DIMENSIONS", 1024),
            timeout_seconds=_positive_int(environ, "JINA_EMBEDDING_TIMEOUT_SECONDS", 30),
            rotate_on_rate_limit=_boolean(
                environ, "JINA_EMBEDDING_ROTATE_ON_RATE_LIMIT", True
            ),
            max_attempts=len(rotator.keys),
        )


@dataclass(frozen=True, slots=True)
class RerankerSettings:
    """Configuration for RerankerAdapter with key rotation (Cohere default)."""

    model: str
    rotator: APIKeyRotator
    timeout_seconds: float = 10.0
    rotate_on_rate_limit: bool = True
    max_attempts: int = 3

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        load_env_file: bool = True,
    ) -> "RerankerSettings":
        if environ is None:
            if load_env_file:
                load_runtime_environment()
            environ = os.environ

        model = environ.get("RERANKER_MODEL", "rerank-v4.0-fast").strip() or "rerank-v4.0-fast"
        model_lower = model.lower()
        if model_lower.startswith("rerank-") or "cohere" in model_lower:
            prefix = "COHERE_API_KEY"
            provider_name = "Cohere"
        else:
            prefix = "JINA_API_KEY"
            provider_name = "Jina"

        rotator = APIKeyRotator.from_env(prefix, environ=environ, provider_name=provider_name)
        rotate_on_rate_limit = _boolean(environ, "RERANKER_ROTATE_ON_RATE_LIMIT", True)
        timeout_seconds = float(_positive_int(environ, "RERANKER_TIMEOUT_SECONDS", 10))

        return cls(
            model=model,
            rotator=rotator,
            timeout_seconds=timeout_seconds,
            rotate_on_rate_limit=rotate_on_rate_limit,
            max_attempts=len(rotator.keys),
        )


@dataclass(frozen=True, slots=True)
class EmailRagQualitySettings:
    """Evidence-gate settings for company Email RAG."""

    min_rerank_score: float = 0.30
    relative_cutoff_ratio: float = 0.85

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        load_env_file: bool = True,
    ) -> "EmailRagQualitySettings":
        if environ is None:
            if load_env_file:
                load_runtime_environment()
            environ = os.environ
        return cls(
            min_rerank_score=_bounded_float(
                environ, "EMAIL_RAG_MIN_RERANK_SCORE", 0.30, minimum=0.0, maximum=1.0
            ),
            relative_cutoff_ratio=_bounded_float(
                environ,
                "EMAIL_RAG_RELATIVE_CUTOFF_RATIO",
                0.85,
                minimum=0.0,
                maximum=1.0,
            ),
        )


@dataclass(frozen=True, slots=True)
class EvaluationSettings:
    """Internal-only evaluation control-plane API configuration.

    The API is disabled unless explicitly enabled, and enabling it without a
    strong bearer token is a startup configuration error. The token is kept
    out of every representation and log line.
    """

    enabled: bool
    api_token: str = field(repr=False, default="")
    job_db_path: str = ".data/evaluation-jobs.db"
    artifact_root: str = ".data/evaluation-jobs"

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        load_env_file: bool = True,
    ) -> "EvaluationSettings":
        if environ is None:
            if load_env_file:
                load_runtime_environment()
            environ = os.environ
        enabled = _evaluation_flag(environ, "EVALUATION_API_ENABLED", default=False)
        api_token = environ.get("EVALUATION_API_TOKEN", "").strip()
        if enabled:
            if not api_token:
                raise ValueError(
                    "EVALUATION_API_TOKEN must be configured when the evaluation API is enabled"
                )
            if len(api_token) < 32:
                raise ValueError("EVALUATION_API_TOKEN must be at least 32 characters long")
        return cls(
            enabled=enabled,
            api_token=api_token,
            job_db_path=_non_empty_value(
                environ, "EVALUATION_JOB_DB_PATH", ".data/evaluation-jobs.db"
            ),
            artifact_root=_non_empty_value(
                environ, "EVALUATION_ARTIFACT_ROOT", ".data/evaluation-jobs"
            ),
        )

    def to_runtime_config(self) -> "EvaluationRuntimeConfig":
        """Resolve the configured storage locations for one local runtime."""

        from cowork_agent.features.batch_evaluation.bootstrap import (
            EvaluationRuntimeConfig,
        )

        return EvaluationRuntimeConfig(
            job_db_path=Path(self.job_db_path),
            artifact_root=Path(self.artifact_root),
        )


@dataclass(frozen=True, slots=True)
class MimoSettings:
    """Configuration for the Xiaomi MiMo chat-completions provider with key rotation."""

    rotator: APIKeyRotator = field(repr=False)
    model: str
    base_url: str
    max_emails_per_batch: int
    max_output_tokens: int
    timeout_seconds: int
    rotate_on_rate_limit: bool = True
    max_attempts: int = 3

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        load_env_file: bool = True,
    ) -> "MimoSettings":
        if environ is None:
            if load_env_file:
                load_runtime_environment()
            environ = os.environ
        key_prefix = "MIMO_API_KEY"
        rotator = APIKeyRotator.from_env(
            key_prefix, environ=environ, provider_name="Mimo"
        )
        model = (
            environ.get("MIMO_MODEL")
            or "mimo-v2.5-pro"
        ).strip()
        if not model or model.startswith("replace-with-"):
            raise ValueError("MIMO_MODEL must be a real Mimo model name")
        base_url = (
            environ.get("MIMO_BASE_URL")
            or "https://token-plan-ams.xiaomimimo.com/v1"
        ).strip()
        rotate_on_rate_limit = _boolean(
            environ,
            "MIMO_ROTATE_ON_RATE_LIMIT",
            True,
        )
        max_emails = _positive_int(
            environ,
            "MIMO_MAX_EMAILS_PER_BATCH",
            5,
        )
        max_tokens = _bounded_positive_int(
            environ,
            "MIMO_MAX_OUTPUT_TOKENS",
            4096,
            maximum=8192,
        )
        timeout = _bounded_positive_int(
            environ,
            "MIMO_TIMEOUT_SECONDS",
            60,
            maximum=120,
        )
        return cls(
            rotator=rotator,
            model=model,
            base_url=base_url,
            max_emails_per_batch=max_emails,
            max_output_tokens=max_tokens,
            timeout_seconds=timeout,
            rotate_on_rate_limit=rotate_on_rate_limit,
            max_attempts=len(rotator.keys),
        )


@dataclass(frozen=True, slots=True)
class MistralSettings:
    """Configuration for the Mistral chat-completions provider."""

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
    ) -> "MistralSettings":
        if environ is None:
            if load_env_file:
                load_runtime_environment()
            environ = os.environ
        model = environ.get("MISTRAL_MODEL", "mistral-small-2603").strip()
        if not model or model.startswith("replace-with-"):
            raise ValueError("MISTRAL_MODEL must be a real Mistral model name")
        return cls(
            api_key=_required_secret(environ, "MISTRAL_API_KEY"),
            model=model,
            max_emails_per_batch=_positive_int(environ, "MISTRAL_MAX_EMAILS_PER_BATCH", 5),
            max_output_tokens=_bounded_positive_int(
                environ, "MISTRAL_MAX_OUTPUT_TOKENS", 2048, maximum=4096
            ),
            timeout_seconds=_bounded_positive_int(
                environ, "MISTRAL_TIMEOUT_SECONDS", 60, maximum=120
            ),
        )


@dataclass(frozen=True, slots=True)
class OpenRouterSettings:
    """Configuration for the OpenRouter chat-completions provider."""

    api_key: str = field(repr=False)
    model: str
    max_emails_per_batch: int
    max_output_tokens: int
    timeout_seconds: int
    allowed_models: tuple[str, ...]

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        load_env_file: bool = True,
    ) -> "OpenRouterSettings":
        if environ is None:
            if load_env_file:
                load_runtime_environment()
            environ = os.environ
        model = environ.get("OPENROUTER_MODEL", "").strip()
        if not model or model.startswith("replace-with-"):
            raise ValueError("OPENROUTER_MODEL must be a real OpenRouter model name")
        return cls(
            api_key=_required_secret(environ, "OPENROUTER_API_KEY"),
            model=model,
            max_emails_per_batch=_positive_int(
                environ, "OPENROUTER_MAX_EMAILS_PER_BATCH", 5
            ),
            max_output_tokens=_bounded_positive_int(
                environ, "OPENROUTER_MAX_OUTPUT_TOKENS", 2048, maximum=4096
            ),
            timeout_seconds=_bounded_positive_int(
                environ, "OPENROUTER_TIMEOUT_SECONDS", 60, maximum=120
            ),
            allowed_models=_openrouter_allowed_models(environ),
        )

    def fallback_models(self) -> tuple[str, ...]:
        """Allowed OpenRouter slugs excluding the configured primary, order preserved."""
        return tuple(slug for slug in self.allowed_models if slug != self.model)


def _positive_int(environ: Mapping[str, str], name: str, default: int) -> int:
    raw = environ.get(name, str(default)).strip().replace(",", "")
    value = int(raw)
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _optional_retention_seconds(environ: Mapping[str, str], name: str) -> int | None:
    raw = environ.get(name, "").strip()
    if not raw:
        return None
    value = int(raw)
    if value <= 0 or value > 31_536_000:
        raise ValueError(f"{name} must be between 1 and 31536000")
    return value


def _bounded_positive_int(
    environ: Mapping[str, str], name: str, default: int, *, maximum: int
) -> int:
    value = _positive_int(environ, name, default)
    if value > maximum:
        raise ValueError(f"{name} must not exceed {maximum}")
    return value


def _bounded_float(
    environ: Mapping[str, str], name: str, default: float, *, minimum: float, maximum: float
) -> float:
    value = float(environ.get(name, str(default)))
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _boolean(environ: Mapping[str, str], name: str, default: bool) -> bool:
    value = environ.get(name, str(default)).strip().lower()
    if value not in {"true", "false"}:
        raise ValueError(f"{name} must be true or false")
    return value == "true"


def _evaluation_flag(environ: Mapping[str, str], name: str, *, default: bool) -> bool:
    value = environ.get(name, "").strip().lower()
    if not value:
        return default
    if value in {"1", "true"}:
        return True
    if value in {"0", "false"}:
        return False
    raise ValueError(f"{name} must be one of 0, 1, true, or false")


def _non_empty_value(environ: Mapping[str, str], name: str, default: str) -> str:
    value = environ.get(name, default).strip()
    if not value or value.startswith("replace-with-"):
        raise ValueError(f"{name} must be configured")
    return value


def _required_secret(environ: Mapping[str, str], name: str) -> str:
    value = environ.get(name, "").strip()
    if not value or value.startswith("replace-with-"):
        raise ValueError(f"{name} must be configured")
    return value


def _openrouter_allowed_models(environ: Mapping[str, str]) -> tuple[str, ...]:
    raw = environ.get("OPENROUTER_ALLOWED_MODELS", "").strip()
    if not raw:
        return ()
    try:
        parsed: object = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            "OPENROUTER_ALLOWED_MODELS must be a JSON list of non-empty strings"
        ) from exc
    if not isinstance(parsed, list):
        raise ValueError(
            "OPENROUTER_ALLOWED_MODELS must be a JSON list of non-empty strings"
        )
    models: list[str] = []
    for item in parsed:
        if not isinstance(item, str):
            raise ValueError(
                "OPENROUTER_ALLOWED_MODELS must be a JSON list of non-empty strings"
            )
        slug = item.strip()
        if not slug:
            raise ValueError(
                "OPENROUTER_ALLOWED_MODELS must be a JSON list of non-empty strings"
            )
        models.append(slug)
    return tuple(models)
