from __future__ import annotations

from typing import Generic, TypeVar

T = TypeVar("T")


class PluginRegistry(Generic[T]):
    def __init__(self, plugins: dict[str, T]) -> None:
        self._plugins = dict(plugins)

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(self._plugins)

    def get(self, key: str) -> T:
        try:
            return self._plugins[key]
        except KeyError as exc:
            available = ", ".join(self._plugins) or "none"
            raise LookupError(
                f"Plugin '{key}' is not registered. Available plugins: {available}."
            ) from exc
