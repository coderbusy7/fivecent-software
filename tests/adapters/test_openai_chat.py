from types import SimpleNamespace
from unittest.mock import AsyncMock
from typing import cast

from openai import AsyncOpenAI
from fivecent.adapters.llm.openai_chat import OpenAIChat
from fivecent.application.ports import ChatMessage
from fivecent.application.tools import ToolCall, ToolSpec


async def test_openai_adapter_translates_neutral_messages_and_tools() -> None:
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=None,
                    tool_calls=[
                        SimpleNamespace(
                            id="call-1",
                            type="function",
                            function=SimpleNamespace(
                                name="quote",
                                arguments='{"days":2}',
                            ),
                        )
                    ],
                )
            )
        ],
        usage=SimpleNamespace(prompt_tokens=7, completion_tokens=3),
    )
    create = AsyncMock(return_value=response)
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    provider = OpenAIChat(cast(AsyncOpenAI, client))
    messages = [
        ChatMessage(role="user", content="quote two days"),
        ChatMessage(
            role="assistant",
            content=None,
            tool_calls=(ToolCall("call-1", "quote", '{"days":2}'),),
        ),
        ChatMessage(role="tool", tool_call_id="call-1", content='{"total":20}'),
    ]
    tools = [ToolSpec("quote", "Create a quote", {"type": "object"})]

    result = await provider.complete(messages, tools, model="test-model")

    kwargs = create.call_args.kwargs
    assert kwargs["model"] == "test-model"
    assert kwargs["messages"] == [
        {"role": "user", "content": "quote two days"},
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "quote", "arguments": '{"days":2}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call-1", "content": '{"total":20}'},
    ]
    assert kwargs["tools"] == [{
        "type": "function",
        "function": {
            "name": "quote",
            "description": "Create a quote",
            "parameters": {"type": "object"},
        },
    }]
    assert result.tool_calls == [ToolCall("call-1", "quote", '{"days":2}')]
    assert result.tokens_in == 7
    assert result.tokens_out == 3
