"""Application context shared across the server lifespan."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import AIOConfig


@dataclass(frozen=True)
class AppContext:
    """The AIO Tests configuration resolved once at server startup.

    Holds the global/default credentials. Per-request credentials supplied via
    the ``X-Aio-Api-Token`` header are layered on top of this in
    :func:`aio_tests_mcp_server.dependencies.get_aio_fetcher`.
    """

    full_aio_config: AIOConfig | None = None
    read_only: bool = False
    enabled_tools: list[str] | None = None
