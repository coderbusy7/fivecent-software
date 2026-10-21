from types import SimpleNamespace
from unittest.mock import AsyncMock
from typing import cast

from anthropic import AsyncAnthropic
from fivecent.adapters.llm.anthropic_chat import AnthropicChat
from fivecent.application.ports import ChatMessage
from fivecent.application.tools import ToolCall, ToolSpec


async def test_anthropic_adapter_translates_messages_and_tool_calls() -> None:
    response = SimpleNamespace(
        content=[
            SimpleNamespace(type="text", text="I found a quote."),
            SimpleNamespace(
                type="tool_use",
                id="call-1",
                name="quote",
                input={"days": 2},
            ),
        ],
        usage=SimpleNamespace(input_tokens=8, output_tokens=4),
    )
    create = AsyncMock(return_value=response)
    client = SimpleNamespace(messages=SimpleNamespace(create=create))
    provider = AnthropicChat(cast(AsyncAnthropic, client))

    result = await provider.complete(
        [
            ChatMessage(role="system", content="Be accurate."),
            ChatMessage(role="user", content="quote two days"),
        ],
        [ToolSpec("quote", "Create a quote", {"type": "object"})],
        model="test-model",
    )

    kwargs = create.call_args.kwargs
    assert kwargs["model"] == "test-model"
    assert kwargs["system"] == "Be accurate."
    assert kwargs["messages"] == [{"role": "user", "content": "quote two days"}]
    assert kwargs["tools"] == [{
        "name": "quote",
        "description": "Create a quote",
        "input_schema": {"type": "object"},
    }]
    assert result.content == "I found a quote."
    assert result.tool_calls == [ToolCall("call-1", "quote", '{"days": 2}')]
    assert result.tokens_in == 8
    assert result.tokens_out == 4


async def test_anthropic_none_tool_choice_omits_tools() -> None:
    response = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="Done.")],
        usage=SimpleNamespace(input_tokens=1, output_tokens=1),
    )
    create = AsyncMock(return_value=response)
    provider = AnthropicChat(cast(
        AsyncAnthropic,
        SimpleNamespace(messages=SimpleNamespace(create=create)),
    ))

    await provider.complete(
        [ChatMessage(role="user", content="hello")],
        [ToolSpec("quote", "Create a quote", {"type": "object"})],
        model="test-model",
        tool_choice="none",
    )

    assert "tools" not in create.call_args.kwargs
    assert "tool_choice" not in create.call_args.kwargs
