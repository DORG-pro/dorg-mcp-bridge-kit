"""Tier-2 handler example — copy into bridge/competency_handlers.py."""

from __future__ import annotations

import json
from typing import Any

from handlers.registry import register


@register(
    "assign_ticket_to_me",
    input_schema={
        "type": "object",
        "properties": {
            "ticket_id": {"type": "string", "description": "Ticket ID to assign."},
        },
        "required": ["ticket_id"],
    },
)
async def assign_ticket_to_me(ctx, arguments: dict[str, Any]) -> dict[str, Any]:
    """Maps user_email orchestrator injection to upstream assignee field."""
    email = arguments.get("user_email")
    ticket_id = arguments.get("ticket_id")
    if not email:
        return {
            "content": [{"type": "text", "text": json.dumps({"error": "No user context."})}],
            "isError": True,
        }
    if not ticket_id:
        return {
            "content": [{"type": "text", "text": json.dumps({"error": "ticket_id is required."})}],
            "isError": True,
        }

    upstream = await ctx.forward("update_ticket", {
        "ticket_id": ticket_id,
        "assignee": email,
    })
    if "result" in upstream:
        return upstream["result"]
    if "error" in upstream:
        return {
            "content": [{"type": "text", "text": json.dumps(upstream["error"])}],
            "isError": True,
        }
    return upstream
