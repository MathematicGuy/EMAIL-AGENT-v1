"""Encryption and one-time OAuth state handling."""

import asyncio
import hashlib
import hmac
import secrets
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode
from collections.abc import Callable

from cryptography.fernet import Fernet, InvalidToken
from langfuse import observe


class TokenCipher:
    def __init__(self, key: str) -> None:
        try:
            self._fernet = Fernet(key.encode())
        except (TypeError, ValueError) as exc:
            raise ValueError("TOKEN_ENCRYPTION_KEY must be a URL-safe base64 Fernet key") from exc

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext.encode()).decode()
        except InvalidToken as exc:
            raise ValueError("Stored Gmail token cannot be decrypted") from exc


class OAuthStateManager:
    """Signed, expiring and single-use OAuth state tokens."""

    def __init__(
        self,
        secret: str,
        ttl_seconds: int,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if len(secret) < 32:
            raise ValueError("OAUTH_STATE_SECRET must contain at least 32 characters")
        self._secret = secret.encode()
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._used_nonces: dict[str, float] = {}
        self._pending_context: dict[str, tuple[float, str | None]] = {}
        self._lock = asyncio.Lock()

    def issue(self, *, context: str | None = None) -> str:
        issued_at = int(self._clock())
        nonce = secrets.token_urlsafe(18)
        self._pending_context[nonce] = (issued_at + self._ttl_seconds, context)
        payload = f"{issued_at}\x1f{nonce}".encode()
        encoded = urlsafe_b64encode(payload).rstrip(b"=")
        signature = hmac.new(self._secret, encoded, hashlib.sha256).digest()
        return f"{encoded.decode()}.{urlsafe_b64encode(signature).rstrip(b'=').decode()}"

    @observe(name="gmail_oauth_consume_state")
    async def consume(self, state: str) -> str | None:
        """Validate signature, expiry and single use, then return the pending context."""
        try:
            encoded_text, signature_text = state.split(".", 1)
            encoded = encoded_text.encode()
            signature = _decode_base64(signature_text)
            expected = hmac.new(self._secret, encoded, hashlib.sha256).digest()
            if not hmac.compare_digest(signature, expected):
                raise ValueError
            payload = _decode_base64(encoded_text).decode()
            issued_text, nonce = payload.split("\x1f", 1)
            issued_at = int(issued_text)
        except (UnicodeDecodeError, ValueError) as exc:
            raise ValueError("Invalid OAuth state") from exc
        age = self._clock() - issued_at
        if age < 0 or age > self._ttl_seconds:
            raise ValueError("OAuth state has expired")
        async with self._lock:
            now = self._clock()
            self._used_nonces = {
                item: expiry for item, expiry in self._used_nonces.items() if expiry >= now
            }
            self._pending_context = {
                item: pending
                for item, pending in self._pending_context.items()
                if pending[0] >= now
            }
            if nonce in self._used_nonces:
                raise ValueError("OAuth state has already been used")
            pending = self._pending_context.pop(nonce, None)
            if pending is None:
                raise ValueError("OAuth state is not pending on this server")
            self._used_nonces[nonce] = issued_at + self._ttl_seconds
        return pending[1]


def _decode_base64(value: str) -> bytes:
    return urlsafe_b64decode(value + "=" * (-len(value) % 4))
