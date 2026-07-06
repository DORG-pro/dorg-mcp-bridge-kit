"""Execution context passed to tier-2 custom tool handlers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from manifest import ManifestToolEntry, ManifestToolRegistry


ForwardFn = Callable[[str, dict[str, Any], Any], Awaitable[dict[str, Any]]]


@dataclass
class HandlerContext:
    """Context for custom tool handlers."""

    tool_name: str
    entry: ManifestToolEntry
    competency_id: str
    forward_upstream: ForwardFn
    req_id: Any

    async def forward(
        self,
        upstream_name: str | None = None,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        name = upstream_name or self.entry.resolved_upstream_name()
        return await self.forward_upstream(name, arguments or {}, self.req_id)

    def map_injected_to_upstream(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Apply manifest upstream_injections (decoupled injected_key → upstream_key)."""
        return ManifestToolRegistry.apply_upstream_injections(
            arguments, self.entry.upstream_injections
        )
