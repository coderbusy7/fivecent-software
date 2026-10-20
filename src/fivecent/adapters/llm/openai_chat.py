from __future__ import annotations

from typing import Any, Optional

from openai import AsyncOpenAI

from fivecent.application.ports import LLMResult, LLMToolCall


class OpenAIChat:
    def __init__(self, client: AsyncOpenAI, model: str) -> None:
        self._client = client
        self._model = model

    async def complete(
        self,
        messages: list[dict],
        tools: list[dict],
        *,
        tool_choice: str | dict = "auto",
    ) -> LLMResult:
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "tools": tools,
            "tool_choice": tool_choice,
        }
        response = await self._client.chat.completions.create(**kwargs)
        choice = response.choices[0].message
        usage = response.usage
        tokens_in = usage.prompt_tokens if usage else 0
        tokens_out = usage.completion_tokens if usage else 0
        tool_calls: list[LLMToolCall] = []
        for call in choice.tool_calls or []:
            if call.type != "function":
                raise RuntimeError("OpenAI returned an unsupported tool call type.")
            tool_calls.append(
                LLMToolCall(id=call.id, name=call.function.name, arguments=call.function.arguments)
            )
        return LLMResult(
            content=choice.content,
            tool_calls=tool_calls,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        )


class ScriptedLLM:
    """Deterministic LLM for tests."""

    def __init__(self, replies: Optional[list[LLMResult]] = None) -> None:
        self.replies = list(replies or [])
        self.calls: list[dict] = []

    def enqueue(self, result: LLMResult) -> None:
        self.replies.append(result)

    async def complete(
        self,
        messages: list[dict],
        tools: list[dict],
        *,
        tool_choice: str | dict = "auto",
    ) -> LLMResult:
        self.calls.append({"messages": messages, "tools": tools, "tool_choice": tool_choice})
        if not self.replies:
            return LLMResult(content="How can I help with a charter?", tool_calls=[], tokens_in=1, tokens_out=1)
        return self.replies.pop(0)
