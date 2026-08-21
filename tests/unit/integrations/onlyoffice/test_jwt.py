"""HS256 round-trip and the rejections that keep the save callback honest."""

from __future__ import annotations

import pytest

from cowork_agent.integrations.onlyoffice.jwt import (
    OnlyOfficeTokenError,
    decode,
    encode,
)

SECRET = "s3cret-shared-with-document-server"


def test_round_trip_preserves_the_payload() -> None:
    payload = {"status": 2, "url": "http://docserver/cache/x.docx", "key": "abc"}
    assert decode(encode(payload, SECRET), SECRET) == payload


def test_a_different_secret_is_rejected() -> None:
    token = encode({"status": 2}, SECRET)
    with pytest.raises(OnlyOfficeTokenError):
        decode(token, "another-secret")


def test_a_tampered_payload_is_rejected() -> None:
    header, body, signature = encode({"status": 2}, SECRET).split(".")
    forged = encode({"status": 6}, SECRET).split(".")[1]
    with pytest.raises(OnlyOfficeTokenError):
        decode(f"{header}.{forged}.{signature}", SECRET)


def test_alg_none_is_rejected() -> None:
    """The classic JWT bypass: an unsigned token claiming it needs no signature."""
    from base64 import urlsafe_b64encode

    def seg(raw: bytes) -> str:
        return urlsafe_b64encode(raw).rstrip(b"=").decode()

    header = seg(b'{"alg":"none","typ":"JWT"}')
    body = seg(b'{"status":2}')
    token = f"{header}.{body}."
    with pytest.raises(OnlyOfficeTokenError):
        decode(token, SECRET)


@pytest.mark.parametrize("token", ["", "not-a-jwt", "a.b", "a.b.c.d", "!!!.???.***"])
def test_malformed_tokens_are_rejected(token: str) -> None:
    with pytest.raises(OnlyOfficeTokenError):
        decode(token, SECRET)


def test_a_non_object_payload_is_rejected() -> None:
    import hashlib
    import hmac
    from base64 import urlsafe_b64encode

    def seg(raw: bytes) -> str:
        return urlsafe_b64encode(raw).rstrip(b"=").decode()

    header = seg(b'{"alg":"HS256","typ":"JWT"}')
    body = seg(b'"just-a-string"')
    signature = hmac.new(
        SECRET.encode(), f"{header}.{body}".encode("ascii"), hashlib.sha256
    ).digest()
    with pytest.raises(OnlyOfficeTokenError):
        decode(f"{header}.{body}.{seg(signature)}", SECRET)


def test_a_non_ascii_segment_is_a_token_error_not_a_crash() -> None:
    # The signing input is rebuilt from the raw segments, so a non-ASCII body
    # reaches .encode("ascii"). That raises UnicodeEncodeError -- a sibling of
    # UnicodeDecodeError, not a subclass -- which used to escape decode() as a 500.
    from base64 import urlsafe_b64encode

    header = urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').rstrip(b"=").decode()

    with pytest.raises(OnlyOfficeTokenError):
        decode(f"{header}.payéload.{header}", SECRET)
