"""Tests that keep server.json publishable.

``server.json`` is what the MCP registry serves to clients looking for this
server, and its version is hard-coded rather than derived from the git tag.
Nothing in the build reads it, so a stale or inconsistent manifest stays
invisible until a release advertises a package version that does not exist.
"""

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _manifest() -> dict:
    """Load the MCP registry manifest.

    Returns:
        The parsed ``server.json``.
    """
    return json.loads((REPO_ROOT / "server.json").read_text(encoding="utf-8"))


def _distribution_name() -> str:
    """Read the distribution name out of pyproject.toml.

    Parsed with a regex rather than ``tomllib``, which only exists on Python
    3.11 and up while this project still supports 3.10.

    Returns:
        The value of ``[project] name``.
    """
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^name\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert match, "could not find the distribution name in pyproject.toml"
    return match.group(1)


class TestReleaseManifest:
    """Tests for the consistency of server.json."""

    def test_server_version_matches_package_version(self):
        """The manifest advertises one version, so both fields must agree.

        The top-level version names the server release and the package entry
        names the PyPI version clients install. If they drift, the registry
        points at a version that was never published.
        """
        manifest = _manifest()
        package_versions = [package["version"] for package in manifest["packages"]]

        assert package_versions, "server.json lists no packages to install"
        for version in package_versions:
            assert version == manifest["version"]

    def test_package_identifier_matches_the_distribution_name(self):
        """The manifest must name the distribution this repo actually builds.

        The distribution was renamed once already; a manifest left on the old
        name sends every client to a package that no longer receives fixes.
        """
        manifest = _manifest()
        distribution = _distribution_name()

        pypi_packages = [
            package
            for package in manifest["packages"]
            if package["registryType"] == "pypi"
        ]

        assert pypi_packages, "server.json declares no PyPI package"
        for package in pypi_packages:
            assert package["identifier"] == distribution
