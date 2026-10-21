from __future__ import annotations

from typing import Callable, Optional

from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

from fivecent.adapters.llm.anthropic_chat import AnthropicChat
from fivecent.adapters.llm.openai_chat import OpenAIChat
from fivecent.application.ports import LLMPort
from fivecent.config import Settings


def build_llm_plugins(
    settings: Settings,
    closers: list[Callable],
    *,
    llm: Optional[LLMPort] = None,
) -> dict[str, LLMPort]:
    providers: dict[str, LLMPort] = {}
    if llm is not None:
        providers["openai"] = llm
    elif settings.openai_api_key:
        openai_client = AsyncOpenAI(
            api_key=settings.openai_api_key, timeout=30.0, max_retries=1
        )
        providers["openai"] = OpenAIChat(openai_client)
        closers.append(openai_client.close)
    if settings.anthropic_api_key:
        anthropic_client = AsyncAnthropic(
            api_key=settings.anthropic_api_key, timeout=30.0, max_retries=1
        )
        providers["anthropic"] = AnthropicChat(anthropic_client)
        closers.append(anthropic_client.close)
    return providers
