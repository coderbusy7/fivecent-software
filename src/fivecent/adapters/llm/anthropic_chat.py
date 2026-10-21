from __future__ import annotations

import json
from typing import Any, Sequence

from anthropic import AsyncAnthropic

from fivecent.application.ports import ChatMessage, LLMResult, ToolChoice
from fivecent.application.tools import ToolCall, ToolSpec


def _serialize_messages(messages: Sequence[ChatMessage]) -> tuple[str, list[dict[str, Any]]]:
    system = "\n\n".join(
        message.content or "" for message in messages if message.role == "system"
    )
    serialized: list[dict[str, Any]] = []
    for message in messages:
        if message.role == "system":
            continue
        if message.role == "tool":
            serialized.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": message.tool_call_id,
                    "content": message.content or "",
                }],
            })
            continue
        if message.role == "assistant" and message.tool_calls:
            content: list[dict[str, Any]] = []
            if message.content:
                content.append({"type": "text", "text": message.content})
            for call in message.tool_calls:
                arguments = json.loads(call.arguments or "{}")
                if not isinstance(arguments, dict):
                    raise ValueError("Tool arguments must be a JSON object.")
                content.append({
                    "type": "tool_use",
                    "id": call.id,
                    "name": call.name,
                    "input": arguments,
                })
            serialized.append({"role": "assistant", "content": content})
            continue
        serialized.append({
            "role": message.role,
            "content": message.content or "",
        })
    return system, serialized


def _serialize_tools(tools: Sequence[ToolSpec]) -> list[dict[str, Any]]:
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.parameters,
        }
        for tool in tools
    ]


class AnthropicChat:
    def __init__(self, client: AsyncAnthropic, *, max_tokens: int = 1024) -> None:
        self._client = client
        self._max_tokens = max_tokens

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec],
        *,
        model: str,
        tool_choice: ToolChoice = "auto",
    ) -> LLMResult:
        system, serialized_messages = _serialize_messages(messages)
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": self._max_tokens,
            "messages": serialized_messages,
        }
        if system:
            kwargs["system"] = system
        if tool_choice != "none" and tools:
            kwargs["tools"] = _serialize_tools(tools)
            kwargs["tool_choice"] = {"type": "auto"}

        response = await self._client.messages.create(**kwargs)
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(
                    id=block.id,
                    name=block.name,
                    arguments=json.dumps(block.input),
                ))
        content = "\n".join(text_parts) or None
        return LLMResult(
            content=content,
            tool_calls=tool_calls,
            tokens_in=response.usage.input_tokens,
            tokens_out=response.usage.output_tokens,
        )
