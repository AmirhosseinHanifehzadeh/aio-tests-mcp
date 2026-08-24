"""Exceptions raised by the AIO Tests MCP server."""


class AIOAuthenticationError(Exception):
    """Raised when AIO Tests authentication fails (401/403)."""
