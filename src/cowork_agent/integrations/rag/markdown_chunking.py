"""Structure-aware Markdown chunking shared by knowledge and project RAG.

Boundaries come from the document's own structure; size is only a constraint
applied where structure runs out. A section that fits stays whole, and a
section that does not is cut at the deepest boundary available — block, then
clause, then sentence, then characters.

Every chunk repeats its heading breadcrumb inside its own text. Retrieval sees
only chunk text: a fragment of an article that does not name the article is
unreachable by dense search and by BM25 alike, and cannot be cited.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Final

from .structure_profile import DEFAULT_PROFILE, StructureProfile

DEFAULT_MAX_CHARS: Final = 2_000
DEFAULT_TARGET_CHARS: Final = 1_200
DEFAULT_MIN_CHARS: Final = 300
DEFAULT_OVERLAP_CHARS: Final = 180
MIN_SUPPORTED_MAX_CHARS: Final = 200

_MAX_BREADCRUMB_DEPTH: Final = 3
_MAX_BREADCRUMB_CHARS: Final = 240

_ATX = re.compile(r"^\s*(#{1,6})\s+(.+?)\s*$")
_PAGE_MARKER = re.compile(r"^<!--\s*Page\s+(\d+)\s*-->\s*$")
_FENCE = re.compile(r"^\s*(?:```|~~~)")
_TABLE_ROW = re.compile(r"^\s*\|")
_TABLE_SEPARATOR = re.compile(r"^\s*\|[\s:|-]+\|?\s*$")
_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+(?:\.\d+)*[.)])\s+")
_QUOTE = re.compile(r"^\s*>")
_COMMENT_ONLY = re.compile(r"^\s*(?:<!--.*?-->\s*)+$", re.S)
_CLAUSE_START = re.compile(r"(?m)^(?=\d+(?:\.\d+)*[.)]\s|[a-zđ][.)]\s)")
# A period preceded by a digit or a lone capital ends a clause number or an
# initial, not a sentence: splitting there shreds "Điều 1." and "1." alike.
_SENTENCE_BOUNDARY = re.compile(r"(?<!\d\.)(?<![A-ZĐ]\.)(?<=[.!?])\s+")

_HEADING = "heading"
_TABLE = "table"
_CODE = "code"
_LIST = "list"
_BOILERPLATE = "boilerplate"
_PARAGRAPH = "paragraph"


@dataclass(frozen=True, slots=True)
class MarkdownPage:
    """One Markdown fragment with an optional one-based source page."""

    markdown: str
    page_number: int | None = None


@dataclass(frozen=True, slots=True)
class MarkdownChunk:
    """One structure-aligned chunk and its inclusive page coordinates."""

    text: str
    section: str | None
    page_start: int | None
    page_end: int | None
    heading_path: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ChunkingPolicy:
    """Size budget applied where structure alone cannot bound a chunk."""

    max_chars: int = DEFAULT_MAX_CHARS
    target_chars: int = DEFAULT_TARGET_CHARS
    min_chars: int = DEFAULT_MIN_CHARS
    overlap_chars: int = DEFAULT_OVERLAP_CHARS

    def __post_init__(self) -> None:
        if self.max_chars < MIN_SUPPORTED_MAX_CHARS:
            raise ValueError("max_chars must be at least 200")
        if not 0 < self.target_chars <= self.max_chars:
            raise ValueError("target_chars must be positive and at most max_chars")
        if not 0 <= self.min_chars <= self.target_chars:
            raise ValueError("min_chars must not exceed target_chars")
        if not 0 <= self.overlap_chars < self.target_chars:
            raise ValueError("overlap_chars must be smaller than target_chars")

    @classmethod
    def scaled_to(cls, max_chars: int) -> ChunkingPolicy:
        """Derive a whole policy from a caller that states only a ceiling."""
        return cls(
            max_chars=max_chars,
            target_chars=min(DEFAULT_TARGET_CHARS, max_chars),
            min_chars=min(DEFAULT_MIN_CHARS, max_chars // 4),
            overlap_chars=min(DEFAULT_OVERLAP_CHARS, max_chars // 8),
        )


def split_markdown_pages(markdown: str) -> tuple[MarkdownPage, ...]:
    """Split Markdown on `<!-- Page N -->` markers into page fragments."""

    normalized = markdown.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    if not any(_PAGE_MARKER.match(line) for line in lines):
        return (MarkdownPage(markdown=normalized, page_number=None),)

    pages: list[MarkdownPage] = []
    current_number: int | None = None
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_lines
        body = "\n".join(current_lines)
        if current_number is None and not body:
            current_lines = []
            return
        pages.append(MarkdownPage(markdown=body, page_number=current_number))
        current_lines = []

    for line in lines:
        match = _PAGE_MARKER.match(line)
        if match is not None:
            flush()
            current_number = int(match.group(1))
            continue
        current_lines.append(line)
    flush()
    return tuple(pages)


def chunk_markdown(
    markdown: str,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    profile: StructureProfile = DEFAULT_PROFILE,
    policy: ChunkingPolicy | None = None,
) -> tuple[MarkdownChunk, ...]:
    """Chunk one Markdown document along its heading hierarchy."""
    return chunk_markdown_pages(
        (MarkdownPage(markdown),), max_chars=max_chars, profile=profile, policy=policy
    )


def chunk_markdown_pages(
    pages: Iterable[MarkdownPage],
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    profile: StructureProfile = DEFAULT_PROFILE,
    policy: ChunkingPolicy | None = None,
) -> tuple[MarkdownChunk, ...]:
    """Chunk ordered page fragments, keeping sections whole where they fit."""
    resolved = policy or ChunkingPolicy.scaled_to(max_chars)
    blocks = _parse_blocks(pages)
    root = _build_tree(blocks, profile)
    drafts = _merge_undersized(_emit(root, resolved), resolved)
    return tuple(_to_chunk(draft) for draft in drafts if draft.body)


@dataclass(frozen=True, slots=True)
class _Block:
    kind: str
    lines: tuple[str, ...]
    page_number: int | None
    level: int = 0
    title: str = ""

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


@dataclass(slots=True)
class _Node:
    level: int
    title: str | None
    path: tuple[str, ...]
    blocks: list[_Block] = field(default_factory=list)
    children: list[_Node] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class _Draft:
    path: tuple[str, ...]
    body: str
    blocks: tuple[_Block, ...]


def _parse_blocks(pages: Iterable[MarkdownPage]) -> list[_Block]:
    blocks: list[_Block] = []
    for page in pages:
        if page.page_number is not None and page.page_number < 1:
            raise ValueError("page numbers must be positive")
        normalized = page.markdown.replace("\r\n", "\n").replace("\r", "\n")
        blocks.extend(_parse_page(normalized.split("\n"), page.page_number))
    return blocks


def _parse_page(lines: Sequence[str], page_number: int | None) -> list[_Block]:
    """Group lines into typed blocks so tables, code and lists stay atomic."""
    blocks: list[_Block] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue
        if _FENCE.match(line):
            start = index
            index += 1
            while index < len(lines) and not _FENCE.match(lines[index]):
                index += 1
            index = min(index + 1, len(lines))
            blocks.append(_Block(_CODE, tuple(lines[start:index]), page_number))
            continue
        heading = _ATX.match(line)
        if heading is not None:
            blocks.append(
                _Block(
                    _HEADING,
                    (line,),
                    page_number,
                    level=len(heading.group(1)),
                    title=heading.group(2).strip(),
                )
            )
            index += 1
            continue
        if _TABLE_ROW.match(line):
            start = index
            while index < len(lines) and _TABLE_ROW.match(lines[index]):
                index += 1
            blocks.append(_Block(_TABLE, tuple(lines[start:index]), page_number))
            continue
        start = index
        index += 1
        while index < len(lines) and _continues(lines[index]):
            index += 1
        body = tuple(lines[start:index])
        blocks.append(_Block(_classify(body), body, page_number))
    return blocks


def _continues(line: str) -> bool:
    """Blank lines end a block; so does anything that opens a different one.

    A list marker does not, so a list hanging off a paragraph line stays in one
    block and keeps the author's indentation byte for byte.
    """
    if not line.strip():
        return False
    return not (_ATX.match(line) or _TABLE_ROW.match(line) or _FENCE.match(line))


def _classify(lines: tuple[str, ...]) -> str:
    if _COMMENT_ONLY.match("\n".join(lines)):
        return _BOILERPLATE
    return _LIST if any(_LIST_ITEM.match(line) for line in lines) else _PARAGRAPH


def _build_tree(blocks: Sequence[_Block], profile: StructureProfile) -> _Node:
    root = _Node(level=0, title=None, path=())
    stack = [root]
    for block in blocks:
        if block.kind == _BOILERPLATE:
            continue
        if block.kind != _HEADING:
            stack[-1].blocks.append(block)
            continue
        while len(stack) > 1 and stack[-1].level >= block.level:
            stack.pop()
        parent = stack[-1]
        node = _Node(
            level=block.level, title=block.title, path=(*parent.path, block.title)
        )
        parent.children.append(node)
        stack.append(node)
    return root


def _emit(node: _Node, policy: ChunkingPolicy) -> list[_Draft]:
    """Chunk one node and its descendants, deepest heading first.

    Emitting at the leaf and merging back up (rather than collapsing a subtree
    that happens to fit) is what keeps ``section`` pinned to the most specific
    heading a chunk actually sits under. Collapsing early would label a chunk
    with its chapter when the article is the answer to the reader's question.
    """
    overhead = _breadcrumb_cost(node.path)
    budget = max(policy.max_chars - overhead, MIN_SUPPORTED_MAX_CHARS)
    body = _render_blocks(node.blocks)
    if body and len(body) + overhead <= policy.max_chars:
        drafts = [_Draft(node.path, body, tuple(node.blocks))]
    else:
        drafts = _pack_blocks(node.blocks, node.path, budget, policy)
    for child in node.children:
        drafts.extend(_emit(child, policy))
    return drafts


def _pack_blocks(
    blocks: Sequence[_Block],
    path: tuple[str, ...],
    budget: int,
    policy: ChunkingPolicy,
) -> list[_Draft]:
    """Fill chunks with a node's own blocks, overlapping consecutive cuts."""
    pieces = [
        (part, block) for block in blocks for part in _split_block(block, budget)
    ]
    drafts: list[_Draft] = []
    current: list[str] = []
    used: list[_Block] = []
    length = 0
    carry = ""

    def flush() -> None:
        nonlocal current, used, length, carry
        if not current:
            return
        drafts.append(_Draft(path, "\n\n".join(current), tuple(used)))
        # Overlap restores prose continuity across a cut. Carrying the tail of
        # a table or a fenced block instead would duplicate rows and strand an
        # unmatched fence in the next chunk.
        carry = (
            _overlap_tail(current[-1], policy.overlap_chars)
            if used and used[-1].kind == _PARAGRAPH
            else ""
        )
        current, used, length = [], [], 0

    for part, block in pieces:
        if current and length + len(part) + 2 > budget:
            flush()
        if not current and carry and len(carry) + len(part) + 2 <= budget:
            current.append(carry)
            length = len(carry)
        current.append(part)
        used.append(block)
        length += len(part) + (2 if length else 0)
    flush()
    return drafts


def _merge_undersized(
    drafts: Sequence[_Draft], policy: ChunkingPolicy
) -> list[_Draft]:
    """Rejoin stub pieces of a section that splitting cut too finely.

    Merging is deliberately confined to chunks under the *same* heading. A
    short section is not a defect once it carries its breadcrumb — ``Lệ phí /
    Không.`` answers a real question — whereas folding it into its parent would
    cost the very section label that makes it citable.
    """
    merged: list[_Draft] = []
    for draft in drafts:
        combined = _combine(merged[-1], draft, policy) if merged else None
        if combined is None:
            merged.append(draft)
        else:
            merged[-1] = combined
    return merged


def _combine(
    previous: _Draft, draft: _Draft, policy: ChunkingPolicy
) -> _Draft | None:
    if previous.path != draft.path:
        return None
    if len(previous.body) >= policy.min_chars and len(draft.body) >= policy.min_chars:
        return None
    body = f"{previous.body}\n\n{draft.body}"
    if len(body) + _breadcrumb_cost(draft.path) > policy.max_chars:
        return None
    return _Draft(draft.path, body, previous.blocks + draft.blocks)


def _render_blocks(blocks: Sequence[_Block]) -> str:
    return "\n\n".join(block.text for block in blocks)


def _breadcrumb(path: tuple[str, ...]) -> str:
    """The heading trail a chunk carries, trimmed to the nearest ancestors."""
    kept = path[-_MAX_BREADCRUMB_DEPTH:]
    while len(kept) > 1 and len("\n".join(kept)) > _MAX_BREADCRUMB_CHARS:
        kept = kept[1:]
    return "\n".join(kept)


def _breadcrumb_cost(path: tuple[str, ...]) -> int:
    breadcrumb = _breadcrumb(path)
    return len(breadcrumb) + 2 if breadcrumb else 0


def _to_chunk(draft: _Draft) -> MarkdownChunk:
    breadcrumb = _breadcrumb(draft.path)
    pages = [
        block.page_number for block in draft.blocks if block.page_number is not None
    ]
    return MarkdownChunk(
        text=f"{breadcrumb}\n\n{draft.body}" if breadcrumb else draft.body,
        section=draft.path[-1] if draft.path else None,
        page_start=min(pages) if pages else None,
        page_end=max(pages) if pages else None,
        heading_path=draft.path,
    )


def _split_block(block: _Block, budget: int) -> list[str]:
    text = block.text
    if len(text) <= budget:
        return [text]
    if block.kind == _TABLE:
        parts = _split_table(block.lines, budget)
    elif block.kind == _CODE:
        parts = _split_code(block.lines, budget)
    elif block.kind == _LIST:
        parts = _split_list(block.lines, budget)
    else:
        parts = _split_prose(text, budget)
    return [
        piece
        for part in parts
        for piece in ([part] if len(part) <= budget else _hard_split(part, budget))
    ]


def _split_table(lines: Sequence[str], budget: int) -> list[str]:
    """Cut a table between rows, repeating its header on every part."""
    header = [lines[0]]
    rows = list(lines[1:])
    if rows and _TABLE_SEPARATOR.match(rows[0]):
        header.append(rows.pop(0))
    prefix_length = sum(len(line) + 1 for line in header)
    parts: list[str] = []
    current: list[str] = []
    length = prefix_length
    for row in rows:
        if current and length + len(row) + 1 > budget:
            parts.append("\n".join(header + current))
            current, length = [], prefix_length
        current.append(row)
        length += len(row) + 1
    if current:
        parts.append("\n".join(header + current))
    return parts or ["\n".join(header)]


def _split_code(lines: Sequence[str], budget: int) -> list[str]:
    """Cut a fenced block between lines, re-fencing every part."""
    opening = lines[0] if _FENCE.match(lines[0]) else "```"
    closed = len(lines) > 1 and _FENCE.match(lines[-1]) is not None
    body = list(lines[1:-1] if closed else lines[1:])
    overhead = len(opening) + 5
    parts: list[str] = []
    current: list[str] = []
    length = overhead
    for line in body:
        if current and length + len(line) + 1 > budget:
            parts.append("\n".join([opening, *current, "```"]))
            current, length = [], overhead
        current.append(line)
        length += len(line) + 1
    if current:
        parts.append("\n".join([opening, *current, "```"]))
    return parts or [opening + "\n```"]


def _split_list(lines: Sequence[str], budget: int) -> list[str]:
    """Cut a list between items, never inside one."""
    items: list[list[str]] = []
    for line in lines:
        if _LIST_ITEM.match(line) or not items:
            items.append([line])
        else:
            items[-1].append(line)
    return _pack_parts(
        ["\n".join(item) for item in items], budget, "\n", _split_prose
    )


def _split_prose(text: str, budget: int) -> list[str]:
    clauses = [part.strip() for part in _CLAUSE_START.split(text) if part.strip()]
    if len(clauses) > 1:
        return _pack_parts(clauses, budget, "\n", _split_sentences)
    return _split_sentences(text, budget)


def _split_sentences(text: str, budget: int) -> list[str]:
    sentences = [
        part.strip() for part in _SENTENCE_BOUNDARY.split(text) if part.strip()
    ]
    if len(sentences) > 1:
        return _pack_parts(sentences, budget, " ", _hard_split)
    return _hard_split(text, budget)


def _pack_parts(
    parts: Sequence[str],
    budget: int,
    separator: str,
    fallback: Callable[[str, int], list[str]],
) -> list[str]:
    packed: list[str] = []
    current = ""
    for part in parts:
        if len(part) > budget:
            if current:
                packed.append(current)
                current = ""
            packed.extend(fallback(part, budget))
            continue
        candidate = f"{current}{separator}{part}" if current else part
        if len(candidate) > budget:
            packed.append(current)
            current = part
        else:
            current = candidate
    if current:
        packed.append(current)
    return packed


def _hard_split(text: str, budget: int) -> list[str]:
    parts: list[str] = []
    remaining = text.strip()
    while len(remaining) > budget:
        boundary = max(
            remaining.rfind("\n", 0, budget + 1),
            remaining.rfind(" ", budget // 2, budget + 1),
        )
        if boundary < budget // 2:
            boundary = budget
        part = remaining[:boundary].strip()
        if part:
            parts.append(part)
        remaining = remaining[boundary:].strip()
    if remaining:
        parts.append(remaining)
    return parts


def _overlap_tail(text: str, limit: int) -> str:
    """Carry the tail of a chunk into the next cut of the same section."""
    if limit <= 0 or not text:
        return ""
    tail = text[-limit:]
    boundary = _SENTENCE_BOUNDARY.search(tail)
    if boundary is not None:
        tail = tail[boundary.end() :]
    return tail.strip()
