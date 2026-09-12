"""Dependency provider that resolves an AIOFetcher for the current request."""

from __future__ import annotations

import dataclasses
import logging

from fastmcp import Context
from fastmcp.server.dependencies import get_http_request
from starlette.requests import Request

from .config import AIOConfig
from .context import AppContext
from .fetcher import AIOFetcher

logger = logging.getLogger("aio-tests-mcp.dependencies")


async def get_aio_fetcher(ctx: Context) -> AIOFetcher:
    """Return an AIOFetcher appropriate for the current request context.

    AIO Tests authenticates with its own access token, so a per-request token is
    taken from the dedicated ``X-Aio-Api-Token`` header. Otherwise the global
    configuration loaded at startup is used.

    Args:
        ctx: The FastMCP context.

    Returns:
        AIOFetcher instance for the current user or global config.

    Raises:
        ValueError: If configuration or credentials are invalid.
    """
    logger.debug(f"get_aio_fetcher: ENTERED. Context ID: {id(ctx)}")
    global_config: AIOConfig | None = None
    lifespan_ctx_dict = ctx.request_context.lifespan_context  # type: ignore[union-attr]
    app_lifespan_ctx: AppContext | None = (
        lifespan_ctx_dict.get("app_lifespan_context")
        if isinstance(lifespan_ctx_dict, dict)
        else None
    )
    if app_lifespan_ctx:
        global_config = app_lifespan_ctx.full_aio_config

    try:
        request: Request = get_http_request()
        if hasattr(request.state, "aio_fetcher") and request.state.aio_fetcher:
            logger.debug("get_aio_fetcher: Returning AIOFetcher from request.state.")
            return request.state.aio_fetcher

        service_headers = getattr(request.state, "aio_service_headers", {})
        user_token = service_headers.get("X-Aio-Api-Token")
        if user_token:
            if not global_config:
                raise ValueError(
                    "AIO Tests global configuration (URL, SSL) is not available "
                    "from lifespan context."
                )
            logger.info("Creating user-specific AIOFetcher from X-Aio-Api-Token header")
            # Cloud authenticates with an AIO Tests access token; Server/Data
            # Center serves the API from Jira and authenticates with a Jira PAT.
            # Sending the wrong scheme is rejected with a 401 either way.
            user_config = (
                dataclasses.replace(
                    global_config,
                    auth_type="token",
                    api_token=user_token,
                    personal_token=None,
                    username=None,
                    password=None,
                )
                if global_config.is_cloud
                else dataclasses.replace(
                    global_config,
                    auth_type="pat",
                    api_token=None,
                    personal_token=user_token,
                    username=None,
                    password=None,
                )
            )
            try:
                user_aio_fetcher = AIOFetcher(config=user_config)
            except Exception as e:
                logger.error(
                    f"get_aio_fetcher: Failed to create user-specific AIOFetcher: {e}",
                    exc_info=True,
                )
                raise ValueError(
                    f"Invalid user AIO Tests token or configuration: {e}"
                ) from e
            request.state.aio_fetcher = user_aio_fetcher
            return user_aio_fetcher
    except RuntimeError:
        logger.debug(
            "Not in an HTTP request context. Attempting global AIOFetcher for non-HTTP."
        )

    if global_config:
        logger.debug("get_aio_fetcher: Using global AIOFetcher from lifespan_context.")
        return AIOFetcher(config=global_config)
    logger.error("AIO Tests configuration could not be resolved.")
    raise ValueError(
        "AIO Tests client (fetcher) not available. Set AIO_API_TOKEN (Cloud) or "
        "AIO_ENABLED=true with Jira Server/Data Center credentials."
    )
