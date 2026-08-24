"""Top-level FastMCP application: lifespan, tool filtering and HTTP transport."""

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Literal, Optional

from fastmcp import FastMCP
from fastmcp.tools import Tool as FastMCPTool
from mcp.types import Tool as MCPTool
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .config import AIOConfig, is_aio_enabled
from .context import AppContext
from .server import aio_mcp
from .utils.io import is_read_only_mode
from .utils.logging import mask_sensitive
from .utils.tools import get_enabled_tools, should_include_tool

logger = logging.getLogger("aio-tests-mcp.app")


async def health_check(request: Request) -> JSONResponse:
    """Report server liveness.

    Args:
        request: The incoming HTTP request.

    Returns:
        A JSON response with an ``ok`` status.
    """
    return JSONResponse({"status": "ok"})


@asynccontextmanager
async def main_lifespan(app: FastMCP[AppContext]) -> AsyncIterator[dict]:
    """Load configuration once at startup and expose it to every request.

    Args:
        app: The FastMCP application.

    Yields:
        A dict carrying the :class:`AppContext` under ``app_lifespan_context``.
    """
    logger.info("AIO Tests MCP server lifespan starting...")
    read_only = is_read_only_mode()
    enabled_tools = get_enabled_tools()

    loaded_aio_config: AIOConfig | None = None
    if is_aio_enabled():
        try:
            aio_config = AIOConfig.from_env()
            if aio_config.is_auth_configured():
                loaded_aio_config = aio_config
                logger.info(
                    "AIO Tests configuration loaded and authentication is configured."
                )
            else:
                logger.warning(
                    "AIO Tests URL found, but authentication is not fully configured. "
                    "AIO Tests tools will be unavailable."
                )
        except Exception as e:
            logger.error(f"Failed to load AIO Tests configuration: {e}", exc_info=True)
    else:
        logger.warning(
            "AIO Tests is not configured. Set AIO_API_TOKEN (Cloud), or "
            "AIO_ENABLED=true with JIRA_URL and credentials (Server/Data Center)."
        )

    app_context = AppContext(
        full_aio_config=loaded_aio_config,
        read_only=read_only,
        enabled_tools=enabled_tools,
    )
    logger.info(f"Read-only mode: {'ENABLED' if read_only else 'DISABLED'}")
    logger.info(f"Enabled tools filter: {enabled_tools or 'All tools enabled'}")

    try:
        yield {"app_lifespan_context": app_context}
    finally:
        logger.info("AIO Tests MCP server lifespan shutdown complete.")


class AIOTestsMCP(FastMCP[AppContext]):
    """FastMCP server that filters tools by read-only mode and configuration."""

    async def _list_tools_mcp(self) -> list[MCPTool]:
        """List the tools available for the current request.

        Returns:
            The tools that survive the enabled-tools, read-only and
            authentication filters.
        """
        req_context = self._mcp_server.request_context
        if req_context is None or req_context.lifespan_context is None:
            logger.warning(
                "Lifespan context not available during _list_tools_mcp call."
            )
            return []

        lifespan_ctx_dict = req_context.lifespan_context
        app_lifespan_state: AppContext | None = (
            lifespan_ctx_dict.get("app_lifespan_context")
            if isinstance(lifespan_ctx_dict, dict)
            else None
        )
        read_only = (
            getattr(app_lifespan_state, "read_only", False)
            if app_lifespan_state
            else False
        )
        enabled_tools_filter = (
            getattr(app_lifespan_state, "enabled_tools", None)
            if app_lifespan_state
            else None
        )

        # A per-request token makes the tools usable even without global config.
        header_auth = False
        if hasattr(req_context, "request") and hasattr(req_context.request, "state"):
            service_headers = getattr(
                req_context.request.state, "aio_service_headers", {}
            )
            header_auth = bool(service_headers.get("X-Aio-Api-Token"))

        aio_available = (
            app_lifespan_state is not None
            and app_lifespan_state.full_aio_config is not None
        ) or header_auth

        all_tools: dict[str, FastMCPTool] = await self.get_tools()
        filtered_tools: list[MCPTool] = []
        for registered_name, tool_obj in all_tools.items():
            if not should_include_tool(registered_name, enabled_tools_filter):
                logger.debug(f"Excluding tool '{registered_name}' (not enabled)")
                continue
            if read_only and "write" in tool_obj.tags:
                logger.debug(
                    f"Excluding tool '{registered_name}' due to read-only mode "
                    "and 'write' tag"
                )
                continue
            if not aio_available:
                logger.debug(
                    f"Excluding tool '{registered_name}' as AIO Tests "
                    "configuration/authentication is incomplete."
                )
                continue
            filtered_tools.append(tool_obj.to_mcp_tool(name=registered_name))

        logger.debug(
            f"_list_tools_mcp: Total tools after filtering: {len(filtered_tools)}"
        )
        return filtered_tools

    def http_app(
        self,
        path: str | None = None,
        middleware: list[Middleware] | None = None,
        json_response: bool | None = None,
        stateless_http: bool | None = None,
        transport: Literal["streamable-http", "sse"] = "streamable-http",
        **kwargs: Any,
    ) -> "Starlette":
        """Build the ASGI app with the per-request token middleware installed.

        Args:
            path: The MCP endpoint path.
            middleware: Additional Starlette middleware.
            json_response: Whether to force JSON responses.
            stateless_http: Whether the server should be stateless.
            transport: The HTTP transport to use.
            **kwargs: Extra arguments forwarded to FastMCP.

        Returns:
            The configured Starlette application.
        """
        user_token_mw = Middleware(UserTokenMiddleware, mcp_server_ref=self)
        final_middleware_list = [user_token_mw]
        if middleware:
            final_middleware_list.extend(middleware)
        return super().http_app(
            path=path,
            middleware=final_middleware_list,
            json_response=json_response,
            stateless_http=stateless_http,
            transport=transport,
            **kwargs,
        )


class UserTokenMiddleware:
    """ASGI middleware extracting a per-request AIO Tests token.

    Lets one server instance serve several tenants: each client sends its own
    token as ``X-Aio-Api-Token`` (or ``Authorization: Token <token>``) and the
    request is scoped to that tenant.
    """

    def __init__(
        self, app: ASGIApp, mcp_server_ref: Optional["AIOTestsMCP"] = None
    ) -> None:
        """Initialize the middleware.

        Args:
            app: The next ASGI application.
            mcp_server_ref: The server, used to match the MCP endpoint path.
        """
        self.app = app
        self.mcp_server_ref = mcp_server_ref
        if not self.mcp_server_ref:
            logger.warning(
                "UserTokenMiddleware initialized without mcp_server_ref. "
                "Path matching for the MCP endpoint may fail."
            )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Handle one ASGI event.

        Args:
            scope: The ASGI scope.
            receive: The ASGI receive callable.
            send: The ASGI send callable.
        """
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        scope_copy: Scope = dict(scope)
        if "state" not in scope_copy:
            scope_copy["state"] = {}
        scope_copy["state"]["auth_validation_error"] = None

        if self.mcp_server_ref and self._should_process_auth(scope_copy):
            self._process_authentication_headers(scope_copy)

        async def safe_send(message: Message) -> None:
            try:
                await send(message)
            except (ConnectionResetError, BrokenPipeError, OSError) as e:
                # Client disconnected: swallow so we don't violate the ASGI spec.
                logger.debug(
                    f"Client disconnected during response: {type(e).__name__}: {e}"
                )
                return

        auth_error = scope_copy["state"].get("auth_validation_error")
        if auth_error:
            logger.warning(f"Authentication failed: {auth_error}")
            await self._send_json_error_response(safe_send, 401, auth_error)
            return

        await self.app(scope_copy, receive, safe_send)

    async def _send_json_error_response(
        self, send: Send, status_code: int, error_message: str
    ) -> None:
        """Send a JSON error response over the ASGI protocol.

        Args:
            send: The ASGI send callable.
            status_code: The HTTP status code.
            error_message: The message to include in the JSON body.
        """
        body = json.dumps({"error": error_message}).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": status_code,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    def _should_process_auth(self, scope: Scope) -> bool:
        """Check whether this request targets the MCP endpoint.

        Args:
            scope: The ASGI scope.

        Returns:
            True when authentication headers should be parsed.
        """
        if not self.mcp_server_ref or scope.get("method") != "POST":
            return False
        try:
            mcp_path = self.mcp_server_ref.settings.streamable_http_path.rstrip("/")
            return scope.get("path", "").rstrip("/") == mcp_path
        except (AttributeError, ValueError) as e:
            logger.warning(f"Error checking auth path: {e}")
            return False

    def _process_authentication_headers(self, scope: Scope) -> None:
        """Extract the AIO Tests token from the request headers into scope state.

        Args:
            scope: The ASGI scope to annotate.
        """
        try:
            headers = dict(scope.get("headers", []))
            token_header = headers.get(b"x-aio-api-token")
            token = token_header.decode("latin-1").strip() if token_header else None

            if not token:
                auth_header = headers.get(b"authorization")
                auth_value = auth_header.decode("latin-1") if auth_header else ""
                if auth_value.startswith("Token "):
                    token = auth_value[6:].strip()
                    if not token:
                        scope["state"]["auth_validation_error"] = (
                            "Unauthorized: Empty Token (AIO Tests access token)"
                        )
                        return
                elif auth_value.strip():
                    scope["state"]["auth_validation_error"] = (
                        "Unauthorized: send the AIO Tests access token as "
                        "'X-Aio-Api-Token: <token>' or 'Authorization: Token <token>'."
                    )
                    return

            service_headers = {}
            if token:
                service_headers["X-Aio-Api-Token"] = token
                logger.debug(
                    "UserTokenMiddleware: AIO Tests token extracted (masked): "
                    f"{mask_sensitive(token, 4)}"
                )
            scope["state"]["aio_service_headers"] = service_headers
        except Exception as e:
            logger.error(f"Error processing authentication headers: {e}", exc_info=True)
            scope["state"]["auth_validation_error"] = "Authentication processing error"


main_mcp = AIOTestsMCP(name="AIO Tests MCP", lifespan=main_lifespan)
main_mcp.mount(aio_mcp, "aio")


@main_mcp.custom_route("/healthz", methods=["GET"], include_in_schema=False)
async def _health_check_route(request: Request) -> JSONResponse:
    """Expose the health check for Kubernetes probes.

    Args:
        request: The incoming HTTP request.

    Returns:
        A JSON response with an ``ok`` status.
    """
    return await health_check(request)
