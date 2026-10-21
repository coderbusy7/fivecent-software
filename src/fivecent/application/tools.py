from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol

from fivecent.domain.tenancy import TenantContext


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class ToolContext:
    tenant: TenantContext
    conversation_id: str


ToolHandler = Callable[[dict[str, Any], ToolContext], Awaitable[str]]


@dataclass(frozen=True)
class Tool:
    spec: ToolSpec
    handler: ToolHandler


class ToolRegistry:
    def __init__(self, tools: list[Tool]) -> None:
        self._tools = {tool.spec.name: tool for tool in tools}
        if len(self._tools) != len(tools):
            raise ValueError("Tool names must be unique.")

    def specs(self) -> list[ToolSpec]:
        return [tool.spec for tool in self._tools.values()]

    async def execute(self, call: ToolCall, context: ToolContext) -> str:
        tool = self._tools.get(call.name)
        if tool is None:
            return json.dumps({
                "ok": False,
                "code": "unknown_tool",
                "error": f"Unknown tool: {call.name}",
            })
        try:
            arguments = json.loads(call.arguments or "{}")
            if not isinstance(arguments, dict):
                raise ValueError("Tool arguments must be a JSON object.")
            return await tool.handler(arguments, context)
        except (ValueError, TypeError) as exc:
            return json.dumps({"ok": False, "error": str(exc)})


@dataclass(frozen=True)
class PreparedService:
    system_prompt: str
    tools: ToolRegistry


class ServiceModule(Protocol):
    async def prepare(self, tenant: TenantContext) -> PreparedService: ...
