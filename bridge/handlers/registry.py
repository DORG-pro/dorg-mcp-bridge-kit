"""Registry for tier-2 custom tool handlers."""

from __future__ import annotations

import importlib
import logging
import os
from typing import Any, Awaitable, Callable

from handlers.context import HandlerContext

logger = logging.getLogger("dorg-mcp-bridge.handlers")

# handler_name -> async (ctx, arguments) -> MCP tool result dict
HandlerFn = Callable[[HandlerContext, dict[str, Any]], Awaitable[dict[str, Any]]]

HANDLERS: dict[str, HandlerFn] = {}
# Optional static inputSchema for tools/list when handler owns the schema.
HANDLER_SCHEMAS: dict[str, dict[str, Any]] = {}


def register(
    name: str,
    *,
    input_schema: dict[str, Any] | None = None,
) -> Callable[[HandlerFn], HandlerFn]:
    def decorator(fn: HandlerFn) -> HandlerFn:
        HANDLERS[name] = fn
        if input_schema is not None:
            HANDLER_SCHEMAS[name] = input_schema
        return fn

    return decorator


def load_handlers(module_name: str | None = None) -> None:
    """Import handler module; no-op if missing (tier-1 only deployments)."""
    name = module_name or os.getenv("HANDLERS_MODULE", "competency_handlers")
    try:
        importlib.import_module(name)
        logger.info("Loaded custom handlers from %s (%d registered)", name, len(HANDLERS))
    except ImportError:
        logger.debug("No custom handler module %s — tier-1 only mode.", name)


def get_handler(name: str) -> HandlerFn | None:
    return HANDLERS.get(name)


def get_handler_schemas() -> dict[str, dict[str, Any]]:
    return dict(HANDLER_SCHEMAS)
