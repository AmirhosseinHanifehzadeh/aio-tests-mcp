"""AIO Tests MCP server: test management in Jira for MCP-compatible AI clients."""

import asyncio
import logging
import os
import sys
from importlib.metadata import PackageNotFoundError, version

import click
from dotenv import find_dotenv, load_dotenv

# Fix high CPU usage on Windows: ProactorEventLoop busy-waits when combined with
# synchronous libraries (like requests) that use select() for socket operations.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from .utils.env import is_env_truthy  # noqa: E402
from .utils.lifecycle import ensure_clean_exit, setup_signal_handlers  # noqa: E402
from .utils.logging import setup_logging  # noqa: E402

try:
    __version__ = version("aio-tests-mcp-server")
except PackageNotFoundError:
    __version__ = "0.0.0"

logging_level = logging.DEBUG if is_env_truthy("MCP_VERBOSE") else logging.WARNING
logging_stream = sys.stdout if is_env_truthy("MCP_LOGGING_STDOUT") else sys.stderr
logger = setup_logging(logging_level, logging_stream)


@click.version_option(__version__, prog_name="aio-tests-mcp-server")
@click.command()
@click.option("-v", "--verbose", count=True, help="Increase verbosity (repeatable).")
@click.option(
    "--env-file",
    type=click.Path(exists=True, dir_okay=False),
    help="Path to a .env file to load.",
)
@click.option(
    "--transport",
    type=click.Choice(["stdio", "sse", "streamable-http"]),
    default="stdio",
    help="Transport type.",
)
@click.option(
    "--stateless",
    is_flag=True,
    help="Run the server statelessly (streamable-http only).",
)
@click.option("--port", default=8000, help="Port for SSE or Streamable HTTP.")
@click.option(
    "--host",
    default="0.0.0.0",  # noqa: S104
    help="Host to bind for SSE or Streamable HTTP.",
)
@click.option("--path", default="/mcp", help="Path for Streamable HTTP (e.g. /mcp).")
@click.option(
    "--aio-url",
    help=(
        "AIO Tests API base URL (defaults to the Cloud API, or JIRA_URL for Server/DC)."
    ),
)
@click.option("--aio-token", help="AIO Tests access token (Cloud).")
@click.option("--jira-url", help="Jira base URL (Server/Data Center).")
@click.option("--jira-personal-token", help="Jira Personal Access Token (Server/DC).")
@click.option("--jira-username", help="Jira username/email (Server/DC basic auth).")
@click.option("--jira-password", help="Jira password/API token (Server/DC basic auth).")
@click.option(
    "--aio-ssl-verify/--no-aio-ssl-verify",
    default=True,
    help="Verify SSL certificates (default: verify).",
)
@click.option("--read-only", is_flag=True, help="Disable every write tool.")
@click.option(
    "--enabled-tools",
    help="Comma-separated list of tool names to expose (default: all).",
)
def main(
    verbose: int,
    env_file: str | None,
    transport: str,
    stateless: bool,
    port: int,
    host: str,
    path: str,
    aio_url: str | None,
    aio_token: str | None,
    jira_url: str | None,
    jira_personal_token: str | None,
    jira_username: str | None,
    jira_password: str | None,
    aio_ssl_verify: bool,
    read_only: bool,
    enabled_tools: str | None,
) -> None:
    """Run the AIO Tests MCP server.

    Credentials are read from the environment (or a .env file); the options
    below override them.
    """
    if verbose == 1:
        current_logging_level = logging.INFO
    elif verbose >= 2:
        current_logging_level = logging.DEBUG
    else:
        current_logging_level = (
            logging.DEBUG if is_env_truthy("MCP_VERBOSE") else logging.WARNING
        )

    global logger
    logger = setup_logging(current_logging_level, logging_stream)
    logger.debug(f"Logging level set to: {logging.getLevelName(current_logging_level)}")

    if env_file:
        load_dotenv(env_file)
    else:
        # usecwd=True searches from the directory the server was started in.
        # Without it python-dotenv searches upwards from this source file, which
        # picks up an unrelated .env next to the installed package.
        load_dotenv(find_dotenv(usecwd=True))

    if read_only:
        os.environ["READ_ONLY_MODE"] = "true"
    if enabled_tools:
        os.environ["ENABLED_TOOLS"] = enabled_tools

    def _set(env_var: str, value: str | None) -> None:
        if value:
            os.environ[env_var] = value

    _set("AIO_URL", aio_url)
    _set("AIO_API_TOKEN", aio_token)
    _set("JIRA_URL", jira_url)
    _set("JIRA_PERSONAL_TOKEN", jira_personal_token)
    _set("JIRA_USERNAME", jira_username)
    _set("JIRA_API_TOKEN", jira_password)
    if not aio_ssl_verify:
        os.environ["AIO_SSL_VERIFY"] = "false"
    # Server/DC reuses the Jira credentials, so it needs the explicit opt-in.
    if jira_url and not os.getenv("AIO_API_TOKEN"):
        os.environ.setdefault("AIO_ENABLED", "true")

    from .app import main_mcp

    run_kwargs: dict[str, object] = {"transport": transport, "show_banner": False}
    if transport in ("sse", "streamable-http"):
        run_kwargs.update(
            {
                "host": host,
                "port": port,
                "log_level": "DEBUG" if verbose >= 2 else "INFO",
            }
        )
        if transport == "streamable-http":
            run_kwargs["path"] = path
            if stateless:
                run_kwargs["stateless_http"] = True

    setup_signal_handlers()
    try:
        logger.info(f"Starting AIO Tests MCP server with {transport} transport")
        main_mcp.run(**run_kwargs)  # type: ignore[arg-type]
    except KeyboardInterrupt:
        logger.info("Server shutdown requested")
    except Exception as e:
        logger.error(f"Server encountered an error: {e}", exc_info=True)
        sys.exit(1)
    finally:
        ensure_clean_exit()


__all__ = ["__version__", "main"]
