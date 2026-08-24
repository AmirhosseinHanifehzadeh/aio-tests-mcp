"""Shared fixtures for AIO Tests unit tests."""

from unittest.mock import MagicMock

import pytest

from aio_tests_mcp_server.config import AIOConfig
from aio_tests_mcp_server.fetcher import AIOFetcher


@pytest.fixture
def aio_config() -> AIOConfig:
    """Provide a Cloud AIO Tests configuration."""
    return AIOConfig(
        url="https://tcms.aiojiraapps.com/aio-tcms/api/v1",
        auth_type="token",
        api_token="test-token",
    )


@pytest.fixture
def aio_fetcher(aio_config: AIOConfig) -> AIOFetcher:
    """Provide an AIOFetcher whose HTTP session is mocked out."""
    fetcher = AIOFetcher(config=aio_config)
    fetcher.session = MagicMock()
    return fetcher
