"""Helpers for filtering which tools the server exposes."""

import logging
import os

logger = logging.getLogger("aio-tests-mcp.tools")


def get_enabled_tools() -> list[str] | None:
    """Read the ENABLED_TOOLS environment variable.

    Returns:
        A list of tool names, or None when the variable is unset or empty,
        meaning every tool is enabled.
    """
    enabled_tools_str = os.getenv("ENABLED_TOOLS")
    logger.debug(f"Raw ENABLED_TOOLS environment variable: {enabled_tools_str!r}")
    if not enabled_tools_str:
        return None
    tools = [tool.strip() for tool in enabled_tools_str.split(",") if tool.strip()]
    logger.debug(f"Parsed enabled tools: {tools}")
    return tools or None


def should_include_tool(tool_name: str, enabled_tools: list[str] | None) -> bool:
    """Check whether a tool should be exposed.

    Args:
        tool_name: The registered tool name.
        enabled_tools: The list from :func:`get_enabled_tools`, or None.

    Returns:
        True when the tool should be included.
    """
    if enabled_tools is None:
        return True
    return tool_name in enabled_tools
