from __future__ import annotations

from typing import Any, Optional, Sequence

from openai import AsyncOpenAI

from fivecent.application.ports import ChatMessage, LLMResult, ToolChoice
from fivecent.application.tools import ToolCall, ToolSpec


def _message_to_openai(message: ChatMessage) -> dict[str, Any]:
    result: dict[str, Any] = {"role": message.role}
    if message.content is not None:
        result["content"] = message.content
    if message.tool_calls:
        result["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": call.arguments},
            }
            for call in message.tool_calls
        ]
    if message.tool_call_id is not None:
        result["tool_call_id"] = message.tool_call_id
    return result


def _tool_to_openai(tool: ToolSpec) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        },
    }


class OpenAIChat:
    def __init__(self, client: AsyncOpenAI) -> None:
        self._client = client

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec],
        *,
        model: str,
        tool_choice: ToolChoice = "auto",
    ) -> LLMResult:
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": [_message_to_openai(message) for message in messages],
            "tools": [_tool_to_openai(tool) for tool in tools],
            "tool_choice": tool_choice,
        }
        response = await self._client.chat.completions.create(**kwargs)
        choice = response.choices[0].message
        usage = response.usage
        tokens_in = usage.prompt_tokens if usage else 0
        tokens_out = usage.completion_tokens if usage else 0
        tool_calls: list[ToolCall] = []
        for call in choice.tool_calls or []:
            if call.type != "function":
                raise RuntimeError("OpenAI returned an unsupported tool call type.")
            tool_calls.append(
                ToolCall(id=call.id, name=call.function.name, arguments=call.function.arguments)
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
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec],
        *,
        model: str,
        tool_choice: ToolChoice = "auto",
    ) -> LLMResult:
        self.calls.append({
            "messages": messages,
            "tools": tools,
            "model": model,
            "tool_choice": tool_choice,
        })
        if not self.replies:
            return LLMResult(content="How can I help with a charter?", tool_calls=[], tokens_in=1, tokens_out=1)
        return self.replies.pop(0)
