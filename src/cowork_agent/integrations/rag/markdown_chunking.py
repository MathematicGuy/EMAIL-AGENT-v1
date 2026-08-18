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

from .markdown_syntax import ATX_HEADING as _ATX
from .markdown_syntax import FENCE as _FENCE
from .markdown_syntax import LIST_ITEM as _LIST_ITEM
from .markdown_syntax import TABLE_ROW as _TABLE_ROW
from .markdown_syntax import TABLE_SEPARATOR as _TABLE_SEPARATOR
from .markdown_syntax import normalize_newlines
from .structure_normalizer import normalize_lines
from .structure_profile import DEFAULT_PROFILE, StructureProfile

DEFAULT_MAX_CHARS: Final = 2_000
DEFAULT_MIN_CHARS: Final = 300
DEFAULT_OVERLAP_CHARS: Final = 180
MIN_SUPPORTED_MAX_CHARS: Final = 200

_MAX_BREADCRUMB_DEPTH: Final = 3
_MAX_BREADCRUMB_CHARS: Final = 240
#: ``section`` is a label — it names the chunk in citations and evidence lists,
#: it is not content. Every consumer bounds it at 300 characters
#: (``MAX_CHAT_RAG_SECTION_LENGTH``, ``MAX_EPISODE_CITATION_SECTION_LENGTH``),
#: and statute titles do run past that, so the label is fitted here where it is
#: made rather than at each boundary that would otherwise reject the chunk. The
#: untruncated trail stays available in ``heading_path`` and in the breadcrumb
#: that opens ``text``.
_MAX_SECTION_CHARS: Final = 300
_ELLIPSIS: Final = "…"

_PAGE_MARKER = re.compile(r"^<!--\s*Page\s+(\d+)\s*-->\s*$")
_COMMENT_OPEN: Final = "<!--"
_COMMENT_ONLY = re.compile(r"^\s*(?:<!--.*?-->\s*)+$", re.S)
_CLAUSE_START = re.compile(r"(?m)^(?=\d+(?:\.\d+)*[.)]\s|[a-zđ][.)]\s)")
_CLAUSE_MARKER = re.compile(r"^\d+(?:\.\d+)*[.)]\s")
_POINT_MARKER = re.compile(r"^[a-zđ][.)]\s")
#: A repeated clause stem is spent out of the chunk it re-opens, so it is only
#: worth carrying while it stays a heading-sized line. A quarter of the budget
#: admits the openers statutes actually write ("5. Trách nhiệm của Ủy ban nhân
#: dân cấp tỉnh đối với quốc lộ được phân cấp") and refuses a clause that is a
#: substantive paragraph in its own right.
_MAX_STEM_SHARE: Final = 4
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
    min_chars: int = DEFAULT_MIN_CHARS
    overlap_chars: int = DEFAULT_OVERLAP_CHARS

    def __post_init__(self) -> None:
        if self.max_chars < MIN_SUPPORTED_MAX_CHARS:
            raise ValueError("max_chars must be at least 200")
        if not 0 <= self.min_chars <= self.max_chars:
            raise ValueError("min_chars must not exceed max_chars")
        if not 0 <= self.overlap_chars < self.max_chars:
            raise ValueError("overlap_chars must be smaller than max_chars")

    @classmethod
    def scaled_to(cls, max_chars: int) -> ChunkingPolicy:
        """Derive a whole policy from a caller that states only a ceiling."""
        return cls(
            max_chars=max_chars,
            min_chars=min(DEFAULT_MIN_CHARS, max_chars // 4),
            overlap_chars=min(DEFAULT_OVERLAP_CHARS, max_chars // 8),
        )


def split_markdown_pages(markdown: str) -> tuple[MarkdownPage, ...]:
    """Split Markdown on `<!-- Page N -->` markers into page fragments."""

    normalized = normalize_newlines(markdown)
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
    blocks = _parse_blocks(pages, profile)
    root = _build_tree(blocks)
    drafts = _merge_undersized(_emit(root, resolved), resolved)
    return tuple(_to_chunk(draft) for draft in drafts if draft.body)


@dataclass(frozen=True, slots=True)
class _Block:
    kind: str
    lines: tuple[str, ...]
    page_number: int | None
    #: Joined once at construction. As a property this was re-joined on every
    #: read — classification, packing and rendering each want the same string.
    text: str
    level: int = 0
    title: str = ""


@dataclass(slots=True)
class _Node:
    level: int
    path: tuple[str, ...]
    blocks: list[_Block] = field(default_factory=list)
    children: list[_Node] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class _Draft:
    path: tuple[str, ...]
    body: str
    blocks: tuple[_Block, ...]
    #: Derived from ``path`` alone, so it is computed once where the node is
    #: known rather than again per merge and per emitted chunk.
    breadcrumb: str = ""


def _parse_blocks(
    pages: Iterable[MarkdownPage], profile: StructureProfile
) -> list[_Block]:
    """Turn pages into blocks, recovering plain-text headings on the way.

    Normalization happens here rather than in each caller so there is one
    answer to "does this document have structure?". Doing it per page, after
    page markers are already stripped, also keeps a heading that opens a page
    promotable — it is not glued to the marker line above it. Promotion is
    idempotent, so a caller that normalized already is unaffected.
    """
    blocks: list[_Block] = []
    for page in pages:
        if page.page_number is not None and page.page_number < 1:
            raise ValueError("page numbers must be positive")
        lines = normalize_lines(normalize_newlines(page.markdown).split("\n"), profile)
        blocks.extend(_parse_page(lines, page.page_number))
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
            body = tuple(lines[start:index])
            blocks.append(_Block(_CODE, body, page_number, "\n".join(body)))
            continue
        heading = _ATX.match(line)
        if heading is not None:
            blocks.append(
                _Block(
                    _HEADING,
                    (line,),
                    page_number,
                    line,
                    len(heading.group(1)),
                    heading.group(2).strip(),
                )
            )
            index += 1
            continue
        if _TABLE_ROW.match(line):
            start = index
            while index < len(lines) and _TABLE_ROW.match(lines[index]):
                index += 1
            body = tuple(lines[start:index])
            blocks.append(_Block(_TABLE, body, page_number, "\n".join(body)))
            continue
        start = index
        index += 1
        while index < len(lines) and _continues(lines[index]):
            index += 1
        body = tuple(lines[start:index])
        blocks.append(_Block(_classify(body), body, page_number, "\n".join(body)))
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
    # Only a block that opens with a comment can be comment-only, and the
    # backtracking `.*?` scan is the most expensive pattern here — so pay for
    # it on the handful of blocks that could possibly match.
    if lines[0].lstrip().startswith(_COMMENT_OPEN) and _COMMENT_ONLY.match(
        "\n".join(lines)
    ):
        return _BOILERPLATE
    return _LIST if any(_LIST_ITEM.match(line) for line in lines) else _PARAGRAPH


def _build_tree(blocks: Sequence[_Block]) -> _Node:
    root = _Node(level=0, path=())
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
        node = _Node(level=block.level, path=(*parent.path, block.title))
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
    breadcrumb = _breadcrumb(node.path)
    overhead = len(breadcrumb) + 2 if breadcrumb else 0
    budget = max(policy.max_chars - overhead, MIN_SUPPORTED_MAX_CHARS)
    # Measure before joining: over half the nodes overflow, and rendering a
    # body only to discard it is the single largest source of wasted work here.
    size = _rendered_length(node.blocks)
    if size and size + overhead <= policy.max_chars:
        drafts = [
            _Draft(node.path, _render_blocks(node.blocks), tuple(node.blocks), breadcrumb)
        ]
    else:
        drafts = _pack_blocks(node.blocks, node.path, budget, policy, breadcrumb)
    for child in node.children:
        drafts.extend(_emit(child, policy))
    return drafts


def _pack_blocks(
    blocks: Sequence[_Block],
    path: tuple[str, ...],
    budget: int,
    policy: ChunkingPolicy,
    breadcrumb: str,
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
    stem = ""

    def flush() -> None:
        nonlocal current, used, length, carry
        if not current:
            return
        drafts.append(_Draft(path, "\n\n".join(current), tuple(used), breadcrumb))
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
        if _CLAUSE_MARKER.match(part):
            stem = _clause_stem(part, budget)
        if current and length + len(part) + 2 > budget:
            flush()
        if not current:
            for line in _resume_prefix(stem, carry, part, budget):
                current.append(line)
                length += len(line) + (2 if length else 0)
        current.append(part)
        used.append(block)
        length += len(part) + (2 if length else 0)
    flush()
    return drafts


def _clause_stem(part: str, budget: int) -> str:
    """The opening line of a clause, kept only while a chunk can repeat it."""
    line = part.split("\n", 1)[0].strip()
    return line if len(line) <= budget // _MAX_STEM_SHARE else ""


def _resume_prefix(stem: str, carry: str, part: str, budget: int) -> list[str]:
    """Lines that re-open a cut before the piece that resumes it.

    A lettered point cut away from its clause is the one fragment structure
    cannot speak for: ``a) Đầu tư, xây dựng quốc lộ được phân cấp`` names
    neither the duty it belongs to nor who owes it, and no reader — human or
    retrieval — can put it back. So the clause stem is repeated ahead of it,
    the way a split table repeats its header. Overlap is the cheaper of the
    two and is dropped first when both will not fit.
    """
    if not _POINT_MARKER.match(part):
        return [carry] if carry and len(carry) + len(part) + 2 <= budget else []
    prefix = [line for line in (stem, carry) if line]
    while prefix and sum(len(line) + 2 for line in prefix) + len(part) > budget:
        prefix.pop()
    return prefix


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
    overhead = len(draft.breadcrumb) + 2 if draft.breadcrumb else 0
    if len(body) + overhead > policy.max_chars:
        return None
    return _Draft(
        draft.path, body, previous.blocks + draft.blocks, draft.breadcrumb
    )


def _render_blocks(blocks: Sequence[_Block]) -> str:
    return "\n\n".join(block.text for block in blocks)


def _rendered_length(blocks: Sequence[_Block]) -> int:
    """Length ``_render_blocks`` would produce, without building the string."""
    if not blocks:
        return 0
    return sum(len(block.text) for block in blocks) + 2 * (len(blocks) - 1)


def _breadcrumb(path: tuple[str, ...]) -> str:
    """The heading trail a chunk carries, trimmed to the nearest ancestors."""
    kept = path[-_MAX_BREADCRUMB_DEPTH:]
    while len(kept) > 1 and len("\n".join(kept)) > _MAX_BREADCRUMB_CHARS:
        kept = kept[1:]
    return "\n".join(kept)


def _to_chunk(draft: _Draft) -> MarkdownChunk:
    breadcrumb = draft.breadcrumb
    pages = [
        block.page_number for block in draft.blocks if block.page_number is not None
    ]
    return MarkdownChunk(
        text=f"{breadcrumb}\n\n{draft.body}" if breadcrumb else draft.body,
        section=_section_label(draft.path[-1]) if draft.path else None,
        page_start=min(pages) if pages else None,
        page_end=max(pages) if pages else None,
        heading_path=draft.path,
    )


def _section_label(title: str) -> str:
    """Fit a heading to the length every citation consumer allows for a label.

    Cut on a word boundary where one is near the limit: ``Điều 100. Bồi thường
    về đất…`` still names its article, while a mid-word cut reads as corruption.
    """
    if len(title) <= _MAX_SECTION_CHARS:
        return title
    keep = _MAX_SECTION_CHARS - len(_ELLIPSIS)
    boundary = title.rfind(" ", keep // 2, keep + 1)
    return title[: boundary if boundary > 0 else keep].rstrip() + _ELLIPSIS


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


def _split_wrapped(
    units: Sequence[str],
    budget: int,
    prefix: Sequence[str],
    suffix: Sequence[str] = (),
) -> list[str]:
    """Cut lines between units, repeating a fixed wrapper around every part.

    A table's header and a fenced block's delimiters are the same problem:
    every part has to carry them, so every part pays for them out of its
    budget.
    """
    overhead = sum(len(line) + 1 for line in (*prefix, *suffix))
    parts: list[str] = []
    current: list[str] = []
    length = overhead
    for unit in units:
        if current and length + len(unit) + 1 > budget:
            parts.append("\n".join([*prefix, *current, *suffix]))
            current, length = [], overhead
        current.append(unit)
        length += len(unit) + 1
    if current:
        parts.append("\n".join([*prefix, *current, *suffix]))
    return parts or ["\n".join([*prefix, *suffix])]


def _split_table(lines: Sequence[str], budget: int) -> list[str]:
    """Cut a table between rows, repeating its header on every part."""
    header = [lines[0]]
    rows = list(lines[1:])
    if rows and _TABLE_SEPARATOR.match(rows[0]):
        header.append(rows.pop(0))
    return _split_wrapped(rows, budget, header)


def _split_code(lines: Sequence[str], budget: int) -> list[str]:
    """Cut a fenced block between lines, re-fencing every part."""
    opening = lines[0] if _FENCE.match(lines[0]) else "```"
    closed = len(lines) > 1 and _FENCE.match(lines[-1]) is not None
    body = lines[1:-1] if closed else lines[1:]
    return _split_wrapped(body, budget, [opening], ["```"])


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
