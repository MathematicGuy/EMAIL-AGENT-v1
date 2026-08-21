"""HS256 verification for the JWTs OnlyOffice Document Server signs its callbacks with.

Document Server only ever issues HS256, so the twenty lines here are cheaper than a
PyJWT dependency. The signing idiom matches ``integrations/gmail/auth.py``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from base64 import urlsafe_b64decode, urlsafe_b64encode
from typing import Any


class OnlyOfficeTokenError(ValueError):
    """The presented token is missing, malformed, or not signed by our secret."""


def _b64url_decode(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    return urlsafe_b64decode(segment + padding)


def _b64url_encode(raw: bytes) -> str:
    return urlsafe_b64encode(raw).rstrip(b"=").decode()


def encode(payload: dict[str, Any], secret: str) -> str:
    """Sign ``payload`` as an HS256 JWT. Used by the tests and by outbound configs."""
    header = _b64url_encode(b'{"alg":"HS256","typ":"JWT"}')
    body = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header}.{body}".encode("ascii")
    signature = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{header}.{body}.{_b64url_encode(signature)}"


def decode(token: str, secret: str) -> dict[str, Any]:
    """Return the payload of ``token`` when our secret signed it, else raise.

    The algorithm is pinned to HS256: a token whose header asks for ``none`` (or for
    an asymmetric algorithm we would then verify with the shared secret as a public
    key) is rejected before any comparison happens.
    """
    try:
        header_text, body_text, signature_text = token.split(".")
        header = json.loads(_b64url_decode(header_text))
        signature = _b64url_decode(signature_text)
    except (AttributeError, ValueError, UnicodeDecodeError) as exc:
        raise OnlyOfficeTokenError("Malformed OnlyOffice token") from exc

    if not isinstance(header, dict) or header.get("alg") != "HS256":
        raise OnlyOfficeTokenError("Unsupported OnlyOffice token algorithm")

    signing_input = f"{header_text}.{body_text}".encode("ascii")
    expected = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    if not hmac.compare_digest(signature, expected):
        raise OnlyOfficeTokenError("OnlyOffice token signature mismatch")

    try:
        payload = json.loads(_b64url_decode(body_text))
    except (ValueError, UnicodeDecodeError) as exc:
        raise OnlyOfficeTokenError("Malformed OnlyOffice token payload") from exc
    if not isinstance(payload, dict):
        raise OnlyOfficeTokenError("Malformed OnlyOffice token payload")
    return payload
