"""Local stdio MCP server for Telegram Ads tools.

The agent (Claude Code, Cursor, Hermes mcp_servers, …) spawns this as a
child process and talks JSON-RPC on stdin/stdout. Nothing is bound to a
public port. You do not host or keep a server running.

    python -m hermes_telegram_ads.mcp
    # or: telegram-ads-mcp
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("hermes_telegram_ads.mcp")


def public_tool_catalog() -> list[dict[str, Any]]:
    """Ads + watcher tool descriptors (no Telegram research)."""
    from hermes_telegram_ads.hermes_tools import TELEGRAM_ADS_TOOLS
    from hermes_telegram_ads.watcher.hermes_tools import watcher_tool_schemas

    tools: list[dict[str, Any]] = []
    for spec in TELEGRAM_ADS_TOOLS:
        tools.append(
            {
                "name": spec.name,
                "description": spec.description,
                "input_schema": spec.input_schema,
                "mutating": bool(spec.mutating),
            }
        )
    for schema in watcher_tool_schemas():
        tools.append(
            {
                "name": schema["name"],
                "description": schema["description"],
                "input_schema": schema.get("parameters") or {"type": "object", "properties": {}},
                "mutating": False,
            }
        )
    return tools


def _mcp_missing() -> SystemExit:
    return SystemExit(
        "MCP extra is not installed. From this repo run: "
        "pip install -e '.[mcp]'   then: python -m hermes_telegram_ads.mcp"
    )


def main() -> None:
    """stdio entrypoint. Local process only — do not expose on 0.0.0.0."""
    try:
        from mcp.server.lowlevel import NotificationOptions, Server
        from mcp.server.stdio import stdio_server
        from mcp.types import TextContent, Tool
    except ImportError as exc:
        raise _mcp_missing() from exc

    from hermes_telegram_ads.plugin_runtime import (
        dispatch_registered,
        start_watcher_background,
    )

    server = Server("telegram-ads")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        out: list[Tool] = []
        for item in public_tool_catalog():
            kwargs: dict[str, Any] = {
                "name": item["name"],
                "description": item["description"],
                "inputSchema": item["input_schema"],
            }
            try:
                from mcp.types import ToolAnnotations

                mutating = bool(item.get("mutating"))
                kwargs["annotations"] = ToolAnnotations(
                    readOnlyHint=not mutating,
                    destructiveHint=mutating,
                    openWorldHint=True,
                )
            except Exception:  # noqa: BLE001 — older mcp may lack annotations
                pass
            out.append(Tool(**kwargs))
        return out

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any] | None):
        result = dispatch_registered(name, dict(arguments or {}))
        return [
            TextContent(
                type="text",
                text=json.dumps(result, ensure_ascii=False, default=str),
            )
        ]

    start_watcher_background()
    logger.info("telegram-ads MCP stdio server starting (%s tools)", len(public_tool_catalog()))

    import asyncio

    async def _run() -> None:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            )

    asyncio.run(_run())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
