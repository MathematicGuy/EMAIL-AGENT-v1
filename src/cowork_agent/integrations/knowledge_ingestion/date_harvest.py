"""Harvest a calendar date from binary DOCX/PDF metadata only."""

from __future__ import annotations

import re
import zlib
from datetime import date, datetime
from pathlib import Path

from docx import Document

_PDF_INFO_REF = re.compile(rb"/Info\s+(\d+)\s+(\d+)\s+R")
_PDF_DATE = re.compile(r"^D:(\d{4})(\d{2})(\d{2})")
_PDF_STREAM = re.compile(
    rb"<<(.*?)>>\s*stream(?:\r\n|\n|\r)(.*?)(?:\r\n|\n|\r)?endstream",
    re.DOTALL,
)
_PDF_LITERAL_OR_HEX = re.compile(
    rb"\((?P<lit>(?:\\.|[^\\)])*)\)|<(?P<hex>[0-9A-Fa-f\s]*)>"
)
_PDF_LITERAL_ESCAPE = re.compile(rb"\\([nrtbf()\\]|[0-7]{1,3})")


def harvest_document_date(path: Path) -> date | None:
    """Return the binary creation (else modification) date, or None."""
    try:
        if not path.is_file():
            return None
        suffix = path.suffix.lower()
        if suffix == ".docx":
            return _harvest_docx(path)
        if suffix == ".pdf":
            return _harvest_pdf(path)
    except Exception:
        return None
    return None


def _harvest_docx(path: Path) -> date | None:
    properties = Document(str(path)).core_properties
    for value in (properties.created, properties.modified):
        if isinstance(value, datetime):
            return value.date()
    return None


def _harvest_pdf(path: Path) -> date | None:
    data = path.read_bytes()
    info_ref = _info_object_ref(data)
    if info_ref is None:
        return None
    body = _object_body(data, *info_ref)
    if body is None:
        return None
    decoded = _decode_object(body)
    for name in (b"CreationDate", b"ModDate"):
        raw = _pdf_name_string(decoded, name, data)
        parsed = _parse_pdf_date(raw) if raw is not None else None
        if parsed is not None:
            return parsed
    return None


def _info_object_ref(data: bytes) -> tuple[int, int] | None:
    matches = list(_PDF_INFO_REF.finditer(data))
    if not matches:
        return None
    last = matches[-1]
    return int(last.group(1)), int(last.group(2))


def _object_body(data: bytes, obj_num: int, gen: int) -> bytes | None:
    pattern = re.compile(
        rf"(?<!\d){obj_num}\s+{gen}\s+obj".encode("ascii") + rb"(.*?)" + rb"endobj",
        re.DOTALL,
    )
    match = pattern.search(data)
    if match is None:
        return None
    return match.group(1)


def _decode_object(body: bytes) -> bytes:
    stream_match = _PDF_STREAM.search(body)
    if stream_match is None:
        return body
    dictionary, stream_data = stream_match.group(1), stream_match.group(2)
    length_match = re.search(rb"/Length\s+(\d+)(?!\s+\d+\s+R)", dictionary)
    if length_match is not None:
        stream_data = stream_data[: int(length_match.group(1))]
    if b"FlateDecode" not in dictionary:
        return stream_data
    try:
        return zlib.decompress(stream_data)
    except zlib.error:
        return zlib.decompress(stream_data, wbits=-15)


def _pdf_name_string(decoded: bytes, name: bytes, data: bytes, *, depth: int = 0) -> str | None:
    if depth > 4:
        return None
    pattern = re.compile(
        rb"/"
        + name
        + rb"\s*(?:"
        + rb"\((?P<lit>(?:\\.|[^\\)])*)\)"
        + rb"|<(?P<hex>[0-9A-Fa-f\s]*)>"
        + rb"|(?P<ref>\d+)\s+(?P<gen>\d+)\s+R"
        + rb")"
    )
    match = pattern.search(decoded)
    if match is None:
        return None
    if match.group("lit") is not None:
        return _unescape_pdf_literal(match.group("lit"))
    if match.group("hex") is not None:
        return _decode_pdf_hex(match.group("hex"))
    body = _object_body(data, int(match.group("ref")), int(match.group("gen")))
    if body is None:
        return None
    resolved = _decode_object(body)
    string_match = _PDF_LITERAL_OR_HEX.search(resolved)
    if string_match is None:
        return _pdf_name_string(resolved, name, data, depth=depth + 1)
    if string_match.group("lit") is not None:
        return _unescape_pdf_literal(string_match.group("lit"))
    return _decode_pdf_hex(string_match.group("hex") or b"")


def _unescape_pdf_literal(raw: bytes) -> str:
    mapping = {
        b"n": b"\n",
        b"r": b"\r",
        b"t": b"\t",
        b"b": b"\b",
        b"f": b"\f",
        b"(": b"(",
        b")": b")",
        b"\\": b"\\",
    }

    def _replace(match: re.Match[bytes]) -> bytes:
        token = match.group(1)
        if token in mapping:
            return mapping[token]
        return bytes([int(token, 8)])

    return _PDF_LITERAL_ESCAPE.sub(_replace, raw).decode("latin-1")


def _decode_pdf_hex(raw: bytes) -> str:
    digits = re.sub(rb"\s+", b"", raw)
    if len(digits) % 2:
        digits += b"0"
    return bytes.fromhex(digits.decode("ascii")).decode("latin-1")


def _parse_pdf_date(raw: str) -> date | None:
    match = _PDF_DATE.match(raw.strip())
    if match is None:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None
