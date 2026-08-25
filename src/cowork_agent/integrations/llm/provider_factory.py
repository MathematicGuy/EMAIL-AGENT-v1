"""Compose Email Action Plan and Chat providers from ``LLM_PROVIDER``."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from cowork_agent.config import (
    ChatIntentSettings,
    GeminiSettings,
    JinaEmbeddingSettings,
    MimoSettings,
    MistralSettings,
    OpenRouterSettings,
)
from cowork_agent.features.ai_chat.ports import ChatReplyPort, IntentClassifierPort
from cowork_agent.features.ai_chat.tools.arguments import ToolArgumentCompletion
from cowork_agent.features.email_action_plan.ports import (
    ActionPlanGeneratorPort,
    RouteClassifierPort,
    SemanticMemoryPort,
)
from cowork_agent.integrations.llm.chat_intent import (
    GeminiIntentClassifier,
    MimoIntentClassifier,
    MistralIntentClassifier,
    OpenRouterIntentClassifier,
)
from cowork_agent.integrations.llm.chat_reply import (
    GeminiChatReply,
    MimoChatReply,
    MistralChatReply,
    OpenRouterChatReply,
)
from cowork_agent.integrations.llm.last_resort import load_optional_gemini_settings
from cowork_agent.integrations.llm.providers.gemini import (
    GeminiActionPlanGenerator,
    GeminiRetrievalQueryRewriter,
    GeminiRouteClassifier,
)
from cowork_agent.integrations.llm.providers.mimo import (
    MimoActionPlanGenerator,
    MimoRouteClassifier,
)
from cowork_agent.integrations.llm.providers.mistral import (
    MistralActionPlanGenerator,
    MistralRouteClassifier,
)
from cowork_agent.integrations.llm.providers.openrouter import (
    OpenRouterActionPlanGenerator,
    OpenRouterRouteClassifier,
)
from cowork_agent.integrations.llm.tool_arguments import (
    gemini_tool_arguments,
    mimo_tool_arguments,
    mistral_tool_arguments,
    openrouter_tool_arguments,
)
from cowork_agent.integrations.rag.null_memory import NullSemanticMemory

_LOGGER = logging.getLogger(__name__)
_SUPPORTED = frozenset({"gemini", "mistral", "openrouter", "mimo"})


@dataclass(frozen=True)
class EmailProviderBundle:
    classifier: RouteClassifierPort
    generator: ActionPlanGeneratorPort
    semantic_memory: SemanticMemoryPort
    query_rewriter: GeminiRetrievalQueryRewriter | None
    generation_concurrency: int


@dataclass(frozen=True)
class ChatProviderBundle:
    intent_classifier: IntentClassifierPort
    chat_reply: ChatReplyPort
    intent_settings: ChatIntentSettings
    tool_arguments: ToolArgumentCompletion


def normalize_llm_provider(provider: str) -> str:
    name = provider.strip().lower()
    if name not in _SUPPORTED:
        raise ValueError("LLM_PROVIDER must be 'gemini', 'mistral', 'openrouter', or 'mimo'")
    return name


def _openrouter_last_resort(*, log_status: bool) -> GeminiSettings | None:
    last_resort = load_optional_gemini_settings()
    if log_status:
        if last_resort is None:
            _LOGGER.info(
                "OpenRouter Gemini last-resort is off; no numbered GEMINI_API_KEY_* configured"
            )
        else:
            _LOGGER.info("OpenRouter Gemini last-resort is on (%s)", last_resort.model)
    return last_resort


async def resolve_email_providers(provider: str) -> EmailProviderBundle:
    name = normalize_llm_provider(provider)
    if name == "gemini":
        from cowork_agent.integrations.rag.bootstrap import build_semantic_memory

        gemini_settings = GeminiSettings.from_env()
        return EmailProviderBundle(
            classifier=GeminiRouteClassifier(gemini_settings),
            generator=GeminiActionPlanGenerator(gemini_settings),
            semantic_memory=await build_semantic_memory(JinaEmbeddingSettings.from_env()),
            query_rewriter=GeminiRetrievalQueryRewriter(gemini_settings),
            generation_concurrency=gemini_settings.action_plan_concurrency,
        )
    if name == "mimo":
        mimo_settings = MimoSettings.from_env()
        return EmailProviderBundle(
            classifier=MimoRouteClassifier(mimo_settings),
            generator=MimoActionPlanGenerator(mimo_settings),
            semantic_memory=NullSemanticMemory(),
            query_rewriter=None,
            generation_concurrency=1,
        )
    if name == "mistral":
        mistral_settings = MistralSettings.from_env()
        return EmailProviderBundle(
            classifier=MistralRouteClassifier(mistral_settings),
            generator=MistralActionPlanGenerator(mistral_settings),
            semantic_memory=NullSemanticMemory(),
            query_rewriter=None,
            generation_concurrency=1,
        )
    openrouter_settings = OpenRouterSettings.from_env()
    last_resort = _openrouter_last_resort(log_status=True)
    return EmailProviderBundle(
        classifier=OpenRouterRouteClassifier(openrouter_settings, last_resort=last_resort),
        generator=OpenRouterActionPlanGenerator(openrouter_settings, last_resort=last_resort),
        semantic_memory=NullSemanticMemory(),
        query_rewriter=None,
        generation_concurrency=1,
    )


def resolve_chat_providers(provider: str) -> ChatProviderBundle:
    name = normalize_llm_provider(provider)
    if name == "gemini":
        gemini_settings = GeminiSettings.from_env()
        intent_settings = ChatIntentSettings.from_env(default_model=gemini_settings.model)
        return ChatProviderBundle(
            intent_classifier=GeminiIntentClassifier.from_settings(
                gemini_settings, intent_settings
            ),
            chat_reply=GeminiChatReply.from_settings(gemini_settings),
            intent_settings=intent_settings,
            tool_arguments=gemini_tool_arguments(gemini_settings, intent_settings),
        )
    if name == "mimo":
        mimo_settings = MimoSettings.from_env()
        intent_settings = ChatIntentSettings.from_env(default_model=mimo_settings.model)
        return ChatProviderBundle(
            intent_classifier=MimoIntentClassifier.from_settings(mimo_settings, intent_settings),
            chat_reply=MimoChatReply.from_settings(mimo_settings),
            intent_settings=intent_settings,
            tool_arguments=mimo_tool_arguments(mimo_settings, intent_settings),
        )
    if name == "mistral":
        mistral_settings = MistralSettings.from_env()
        intent_settings = ChatIntentSettings.from_env(default_model=mistral_settings.model)
        return ChatProviderBundle(
            intent_classifier=MistralIntentClassifier.from_settings(
                mistral_settings, intent_settings
            ),
            chat_reply=MistralChatReply.from_settings(mistral_settings),
            intent_settings=intent_settings,
            tool_arguments=mistral_tool_arguments(mistral_settings, intent_settings),
        )
    openrouter_settings = OpenRouterSettings.from_env()
    last_resort = _openrouter_last_resort(log_status=False)
    intent_settings = ChatIntentSettings.from_env(default_model=openrouter_settings.model)
    return ChatProviderBundle(
        intent_classifier=OpenRouterIntentClassifier.from_settings(
            openrouter_settings, intent_settings, last_resort=last_resort
        ),
        chat_reply=OpenRouterChatReply.from_settings(openrouter_settings, last_resort=last_resort),
        intent_settings=intent_settings,
        tool_arguments=openrouter_tool_arguments(
            openrouter_settings, intent_settings, last_resort=last_resort
        ),
    )
