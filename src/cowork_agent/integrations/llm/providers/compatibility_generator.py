"""Compatibility plan generators bridging V1-M2 to the target Generator.

Interim state per master-comparison §3.8: one generation call per resolved
non-``no_action`` Task Candidate, reusing the legacy combined-extraction
prompt machinery inside the provider adapters. V1-M3 T3.3 replaces this
bridge with the ActionPlanOutput-based Generator; until then the word
"extraction" survives only in these compatibility internals.
"""

from collections.abc import Sequence
from datetime import datetime

from cowork_agent.domain.target_contracts import EphemeralEmailEnvelope
from cowork_agent.features.email_action_plan.schemas import ExtractionBatch

from .gemini import GeminiActionExtractor
from .groq import GroqActionExtractor


class GeminiCompatibilityPlanGenerator:
    """ActionPlanGeneratorPort bridge delegating to the legacy Gemini extractor."""

    def __init__(self, extractor: GeminiActionExtractor) -> None:
        self._extractor = extractor

    async def generate(
        self,
        user_timezone: str,
        current_time: datetime,
        messages: Sequence[EphemeralEmailEnvelope],
    ) -> ExtractionBatch:
        return await self._extractor.extract(user_timezone, current_time, messages)


class GroqCompatibilityPlanGenerator:
    """ActionPlanGeneratorPort bridge delegating to the legacy Groq extractor."""

    def __init__(self, extractor: GroqActionExtractor) -> None:
        self._extractor = extractor

    async def generate(
        self,
        user_timezone: str,
        current_time: datetime,
        messages: Sequence[EphemeralEmailEnvelope],
    ) -> ExtractionBatch:
        return await self._extractor.extract(user_timezone, current_time, messages)
