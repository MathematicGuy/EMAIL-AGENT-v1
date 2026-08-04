"""Adapters for Gmail, persistence, queue, attachments, and AI providers."""

from .config import GeminiSettings, GmailSettings, GroqSettings
from .connections import SQLiteMailboxConnectionRepository
from .gemini import GeminiActionExtractor, GeminiKeyRotator, GoogleGenAITransport
from .gmail import GmailConnectionService, GmailMailboxAdapter
from .groq import GroqActionExtractor
from .memory import (
    FakeActionExtractor,
    FakeMailbox,
    InMemoryOutbox,
    InMemoryQueue,
    InMemoryResultRepository,
    InMemoryRunRepository,
    SafeTextAttachmentExtractor,
)

__all__ = [
    "FakeActionExtractor",
    "FakeMailbox",
    "GeminiActionExtractor",
    "GeminiKeyRotator",
    "GeminiSettings",
    "GroqActionExtractor",
    "GroqSettings",
    "GmailConnectionService",
    "GmailMailboxAdapter",
    "GmailSettings",
    "GoogleGenAITransport",
    "InMemoryOutbox",
    "InMemoryQueue",
    "InMemoryResultRepository",
    "InMemoryRunRepository",
    "SafeTextAttachmentExtractor",
    "SQLiteMailboxConnectionRepository",
]
