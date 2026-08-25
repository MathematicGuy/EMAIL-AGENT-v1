"""Report artifact contracts: one filename rule, one store interface.

A report artifact is a document the user keeps — written either by the AI Chat
turn that generated it or by an explicit save from the artifacts view, and read
back by the same view. Both writers used to resolve ``data/reports`` for
themselves and decide separately what a safe name was; the chat writer decided
nothing at all and joined a model-supplied string straight onto the path.

``ReportFilename`` exists so that decision cannot be made twice. It is the only
way to name a report, it cannot hold a traversing or otherwise unusable name,
and every store operation takes one.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Protocol

#: Long enough for a descriptive Vietnamese slug, short enough to stay under the
#: 255-byte component limit once NFC-composed diacritics are counted as UTF-8.
MAX_REPORT_FILENAME_LENGTH = 120

#: What a report degrades to when a caller supplies nothing usable at all.
DEFAULT_REPORT_STEM = "bao-cao-tong-hop"
DEFAULT_REPORT_SUFFIX = ".md"

_RESERVED_NAMES = frozenset({"", ".", ".."})


class InvalidReportFilename(ValueError):
    """A candidate name cannot address a file inside the report folder."""


@dataclass(frozen=True, slots=True)
class ReportFilename:
    """A bare filename that addresses one direct child of the report folder.

    Construct through :meth:`parse` (raises on anything unusable) or
    :meth:`sanitize` (never raises, degrades to a safe slug). Constructing the
    dataclass directly validates too, so an unchecked string cannot reach a
    store by way of ``ReportFilename(untrusted)``.
    """

    value: str

    def __post_init__(self) -> None:
        value = self.value
        if value in _RESERVED_NAMES:
            raise InvalidReportFilename("Report filename is empty or reserved")
        if len(value) > MAX_REPORT_FILENAME_LENGTH:
            raise InvalidReportFilename(
                f"Report filename exceeds {MAX_REPORT_FILENAME_LENGTH} characters"
            )
        if "\x00" in value:
            raise InvalidReportFilename("Report filename contains a NUL byte")
        # Both flavours, not ``os.sep``: a POSIX server must still reject the
        # backslash form, or a Windows client can smuggle a directory part past
        # a Linux deployment.
        if PurePosixPath(value).name != value or PureWindowsPath(value).name != value:
            raise InvalidReportFilename("Report filename may not contain a path")
        if value.startswith("."):
            # The listing already skips dotfiles; allowing one to be written
            # would create a report nothing can read back.
            raise InvalidReportFilename("Report filename may not start with a dot")

    def __str__(self) -> str:
        return self.value

    @property
    def suffix(self) -> str:
        return PurePosixPath(self.value).suffix.lower()

    @classmethod
    def parse(cls, raw: str) -> ReportFilename:
        """Strip any directory part from ``raw`` and validate what is left."""
        # Strip under both flavours so ``a\\b/c`` reduces to ``c`` either way.
        stripped = PureWindowsPath(PurePosixPath(raw.strip()).name).name
        return cls(stripped)

    @classmethod
    def sanitize(
        cls,
        raw: str,
        *,
        default_stem: str = DEFAULT_REPORT_STEM,
        default_suffix: str = DEFAULT_REPORT_SUFFIX,
    ) -> ReportFilename:
        """Coerce untrusted text into a usable filename without ever raising.

        This is the path for provider-supplied names. A report the model named
        badly is still a report the user asked for, so a bad name degrades to a
        slug instead of dropping the document.
        """
        candidate = PureWindowsPath(PurePosixPath(raw.strip()).name).name
        suffix = PurePosixPath(candidate).suffix
        stem = candidate[: len(candidate) - len(suffix)] if suffix else candidate

        stem = _slugify(stem) or _slugify(default_stem) or DEFAULT_REPORT_STEM
        suffix = suffix.lower() if _is_usable_suffix(suffix) else default_suffix

        stem = stem[: MAX_REPORT_FILENAME_LENGTH - len(suffix)].strip("-._") or stem[:1]
        return cls(f"{stem}{suffix}")


def _slugify(text: str) -> str:
    """ASCII slug; Vietnamese diacritics fold rather than disappear."""
    folded = unicodedata.normalize("NFKD", text)
    folded = "".join(char for char in folded if not unicodedata.combining(char))
    slug = re.sub(r"[^\w\s-]", "", folded.lower()).strip()
    return re.sub(r"[-\s_]+", "-", slug).strip("-")


def _is_usable_suffix(suffix: str) -> bool:
    return bool(re.fullmatch(r"\.[A-Za-z0-9]{1,10}", suffix))


@dataclass(frozen=True, slots=True)
class ReportArtifact:
    """A report on its way into the store."""

    filename: ReportFilename
    content: str
    title: str | None = None


@dataclass(frozen=True, slots=True)
class StoredReport:
    """A report as the store holds it."""

    filename: ReportFilename
    content: str
    size: int
    updated_at: datetime


class ReportArtifactStore(Protocol):
    """Everything the application may do to the report folder."""

    @property
    def location(self) -> Path:
        """The folder reports live in, for the reveal-in-file-manager action."""
        ...

    async def save(self, artifact: ReportArtifact) -> StoredReport: ...

    async def list_reports(self) -> tuple[StoredReport, ...]: ...

    async def read(self, filename: ReportFilename) -> StoredReport | None: ...

    async def delete(self, filename: ReportFilename) -> bool: ...

    def path_for(self, filename: ReportFilename) -> Path | None:
        """The on-disk file, when the store has one and it exists."""
        ...


class ReportPdfRenderer(Protocol):
    """Renders a stored report to PDF bytes.

    No implementation ships today: a faithful render of a Vietnamese Markdown
    report needs an embedded Unicode font, which is a dependency decision rather
    than a refactor. The port exists so ``GET /api/v1/reports/{name}/pdf`` has a
    seam to grow one behind, and so the route can report its own absence.
    """

    def render(self, report: StoredReport, *, title: str | None = None) -> bytes: ...


__all__ = [
    "DEFAULT_REPORT_STEM",
    "DEFAULT_REPORT_SUFFIX",
    "MAX_REPORT_FILENAME_LENGTH",
    "InvalidReportFilename",
    "ReportArtifact",
    "ReportArtifactStore",
    "ReportFilename",
    "ReportPdfRenderer",
    "StoredReport",
]
