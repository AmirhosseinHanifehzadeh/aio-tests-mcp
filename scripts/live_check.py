#!/usr/bin/env python3
"""Exercise every MCP tool against a real AIO Tests deployment.

Starts the server exactly as an MCP client would, speaks the MCP protocol over
stdio, and calls each tool in turn against a live Jira. Credentials come from the
environment or a ``--env-file``, so nothing is hard-coded to one deployment.

Read-only by default. Pass ``--write`` to also create a folder and a test case,
and to update the case that was just created; the run never touches pre-existing
data.

Usage::

    python scripts/live_check.py --project PROJ
    python scripts/live_check.py --project PROJ --env-file .env --write

Exits non-zero if any check fails.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402
from fastmcp import Client  # noqa: E402
from fastmcp.client.transports import StdioTransport  # noqa: E402

GREEN, RED, YELLOW, DIM, RESET = (
    "\033[32m",
    "\033[31m",
    "\033[33m",
    "\033[2m",
    "\033[0m",
)


@dataclass
class Outcome:
    """The result of one check."""

    name: str
    status: str  # "pass", "fail" or "skip"
    detail: str = ""
    seconds: float = 0.0


@dataclass
class Report:
    """Every outcome of a run."""

    outcomes: list[Outcome] = field(default_factory=list)

    def record(self, outcome: Outcome) -> None:
        """Print an outcome as it happens and keep it for the summary.

        Args:
            outcome: The outcome to record.
        """
        mark = {"pass": f"{GREEN}PASS{RESET}", "fail": f"{RED}FAIL{RESET}"}.get(
            outcome.status, f"{YELLOW}SKIP{RESET}"
        )
        timing = f"{DIM}{outcome.seconds * 1000:6.0f}ms{RESET}"
        print(f"  {mark} {timing}  {outcome.name}")
        if outcome.detail:
            for line in outcome.detail.splitlines():
                print(f"          {DIM}{line}{RESET}")
        self.outcomes.append(outcome)

    @property
    def failed(self) -> list[Outcome]:
        """The checks that failed.

        Returns:
            Every failing outcome.
        """
        return [item for item in self.outcomes if item.status == "fail"]


async def run_check(
    report: Report, name: str, action: Callable[[], Awaitable[str]]
) -> Any:
    """Run one check, recording its outcome.

    Args:
        report: The report to record into.
        name: Human-readable check name.
        action: Coroutine returning a one-line summary of what it found.

    Returns:
        True when the check passed, False otherwise.
    """
    started = time.monotonic()
    try:
        detail = await action()
        report.record(Outcome(name, "pass", detail or "", time.monotonic() - started))
        return True
    except Exception as exc:  # noqa: BLE001 - the point is to report anything
        report.record(
            Outcome(
                name, "fail", f"{type(exc).__name__}: {exc}", time.monotonic() - started
            )
        )
        return False


def decode(result: Any) -> Any:
    """Decode a tool result into Python data.

    Args:
        result: The MCP tool result.

    Returns:
        The decoded JSON payload.
    """
    return json.loads(result.content[0].text)


async def main(argv: list[str] | None = None) -> int:
    """Run the live check.

    Args:
        argv: Command-line arguments, defaulting to ``sys.argv``.

    Returns:
        Process exit status.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project", required=True, help="Jira project key to run the checks against"
    )
    parser.add_argument("--env-file", help="Path to a .env file with the credentials")
    parser.add_argument(
        "--write",
        action="store_true",
        help="Also exercise the write tools (creates a folder and a test case)",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="Keep the data the write checks create instead of noting it for cleanup",
    )
    args = parser.parse_args(argv)

    if args.env_file:
        load_dotenv(args.env_file, override=True)

    project = args.project
    report = Report()

    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    transport = StdioTransport(
        command=sys.executable,
        args=["-m", "aio_tests_mcp_server", "--transport", "stdio"],
        env=env,
        cwd=str(REPO_ROOT),
    )

    target = os.getenv("AIO_URL") or os.getenv("JIRA_URL") or "AIO Tests Cloud"
    print("\nAIO Tests MCP live check")
    print(f"  target   {target}")
    print(f"  project  {project}")
    print(f"  mode     {'read + write' if args.write else 'read only'}\n")

    created_case_key: str | None = None
    created_folder: str | None = None

    async with Client(transport) as client:
        tools = await client.list_tools()
        names = sorted(tool.name for tool in tools)
        print(f"{DIM}  {len(names)} tools advertised: {', '.join(names)}{RESET}\n")
        if not names:
            print(
                f"{RED}  No tools were advertised. Credentials are missing or "
                f"incomplete.{RESET}\n"
            )
            return 1

        async def check_project() -> str:
            data = decode(
                await client.call_tool("aio_get_project", {"project_key": project})
            )
            if not data.get("aio_enabled"):
                raise RuntimeError(
                    f"AIO Tests is not enabled for '{project}': "
                    f"{data.get('error', 'no detail')}"
                )
            return (
                f"project_id={data.get('project_id')} "
                f"adhoc={data.get('adhoc_cycle_key')}"
            )

        if not await run_check(report, "aio_get_project", check_project):
            print(f"\n{RED}  Cannot reach the project; stopping.{RESET}\n")
            return 1

        async def check_schema() -> str:
            data = decode(
                await client.call_tool(
                    "aio_get_test_case_schema", {"project_key": project}
                )
            )
            allowed = data.get("allowed_values", {})
            return (
                f"{len(data.get('fields', []))} fields, "
                f"{len(data.get('custom_fields', []))} custom fields, "
                f"statuses={[s.get('name') for s in allowed.get('statuses', [])]}, "
                f"priorities={[p.get('name') for p in allowed.get('priorities', [])]}"
            )

        await run_check(report, "aio_get_test_case_schema", check_schema)

        async def check_tags() -> str:
            data = decode(
                await client.call_tool("aio_get_tags", {"project_key": project})
            )
            return f"{len(data.get('tags', []))} tags"

        await run_check(report, "aio_get_tags", check_tags)

        for folder_type in ("testcase", "testcycle", "testset"):

            async def check_folders(folder_type: str = folder_type) -> str:
                data = decode(
                    await client.call_tool(
                        "aio_get_folder_hierarchy",
                        {
                            "project_key": project,
                            "folder_type": folder_type,
                            "flat": True,
                        },
                    )
                )
                folders = data.get("folders", [])
                sample = ", ".join(str(f.get("path")) for f in folders[:3])
                return f"{len(folders)} folders{f' ({sample}...)' if sample else ''}"

            await run_check(
                report, f"aio_get_folder_hierarchy [{folder_type}]", check_folders
            )

        sample_key: str | None = None

        async def check_search() -> str:
            nonlocal sample_key
            data = decode(
                await client.call_tool(
                    "aio_search_test_cases",
                    {"project_key": project, "max_results": 5},
                )
            )
            cases = data.get("test_cases", [])
            if cases:
                sample_key = cases[0].get("key")
            return (
                f"{data.get('count')} returned of {data.get('total', '?')} total, "
                f"is_last={data.get('is_last')}"
            )

        await run_check(report, "aio_search_test_cases [no filter]", check_search)

        async def check_search_paging() -> str:
            first = decode(
                await client.call_tool(
                    "aio_search_test_cases",
                    {"project_key": project, "max_results": 1, "start_at": 0},
                )
            )
            second = decode(
                await client.call_tool(
                    "aio_search_test_cases",
                    {"project_key": project, "max_results": 1, "start_at": 1},
                )
            )
            keys = [
                c.get("key")
                for page in (first, second)
                for c in page.get("test_cases", [])
            ]
            if len(keys) == 2 and keys[0] == keys[1]:
                raise RuntimeError(f"start_at did not advance the page: {keys}")
            return f"page0={keys[:1]} page1={keys[1:]}"

        await run_check(
            report, "aio_search_test_cases [pagination]", check_search_paging
        )

        async def check_search_by_title() -> str:
            data = decode(
                await client.call_tool(
                    "aio_search_test_cases",
                    {"project_key": project, "title": "a", "max_results": 3},
                )
            )
            return f"{data.get('count')} cases whose title contains 'a'"

        await run_check(report, "aio_search_test_cases [title]", check_search_by_title)

        async def check_search_by_status() -> str:
            schema = decode(
                await client.call_tool(
                    "aio_get_test_case_schema", {"project_key": project}
                )
            )
            statuses = schema.get("allowed_values", {}).get("statuses", [])
            if not statuses:
                return "skipped: the project defines no case statuses"
            name = statuses[0].get("name")
            data = decode(
                await client.call_tool(
                    "aio_search_test_cases",
                    {"project_key": project, "statuses": [name], "max_results": 3},
                )
            )
            return (
                f"{data.get('count')} cases with status '{name}' "
                "(the name was resolved to an ID)"
            )

        await run_check(
            report, "aio_search_test_cases [status name]", check_search_by_status
        )

        if sample_key:

            async def check_get_case() -> str:
                data = decode(
                    await client.call_tool(
                        "aio_get_test_case",
                        {"project_key": project, "test_case_id": sample_key},
                    )
                )
                return (
                    f"{data.get('key')} v{data.get('version')}, "
                    f"{len(data.get('steps', []))} steps, "
                    f"folder={(data.get('folder') or {}).get('name')}"
                )

            await run_check(report, "aio_get_test_case", check_get_case)

            async def check_get_case_rtf() -> str:
                data = decode(
                    await client.call_tool(
                        "aio_get_test_case",
                        {
                            "project_key": project,
                            "test_case_id": sample_key,
                            "include_rtf": True,
                            "include_attachments": True,
                        },
                    )
                )
                return f"{data.get('key')} read with rich text and attachments"

            await run_check(report, "aio_get_test_case [rtf]", check_get_case_rtf)

            async def check_versions() -> str:
                data = decode(
                    await client.call_tool(
                        "aio_get_test_case_versions",
                        {"project_key": project, "test_case_id": sample_key},
                    )
                )
                return (
                    f"current v{data.get('current_version')}, "
                    f"{len(data.get('versions', []))} saved versions"
                )

            await run_check(report, "aio_get_test_case_versions", check_versions)
        else:
            for name in (
                "aio_get_test_case",
                "aio_get_test_case [rtf]",
                "aio_get_test_case_versions",
            ):
                report.record(
                    Outcome(name, "skip", "the project has no test cases to read")
                )

        write_tools = {
            "aio_create_folder",
            "aio_create_test_case",
            "aio_update_test_case",
        }
        missing_write_tools = write_tools - set(names)
        if args.write and missing_write_tools:
            reason = (
                "the server hides its write tools: READ_ONLY_MODE is on, or "
                "ENABLED_TOOLS excludes them"
            )
            for name in sorted(write_tools):
                report.record(Outcome(name, "skip", reason))
        elif not args.write:
            for name in sorted(write_tools):
                report.record(Outcome(name, "skip", "read-only run; pass --write"))
        else:
            stamp = time.strftime("%Y%m%d-%H%M%S")
            folder_path = f"/MCP Live Check/{stamp}"

            async def check_create_folder() -> str:
                nonlocal created_folder
                data = decode(
                    await client.call_tool(
                        "aio_create_folder",
                        {"project_key": project, "folder_path": folder_path},
                    )
                )
                created_folder = data["folder"].get("path") or folder_path
                return f"created {created_folder} (id={data['folder'].get('id')})"

            folder_ok = await run_check(
                report, "aio_create_folder", check_create_folder
            )

            async def check_create_case() -> str:
                nonlocal created_case_key
                data = decode(
                    await client.call_tool(
                        "aio_create_test_case",
                        {
                            "project_key": project,
                            "title": f"[MCP live check] {stamp}",
                            "steps": [
                                {
                                    "step": "Run the live check",
                                    "data": "scripts/live_check.py",
                                    "expected_result": "Every tool reports PASS",
                                }
                            ],
                            "fields": {
                                "description": (
                                    "Created by the AIO Tests MCP live "
                                    "check. Safe to delete."
                                ),
                                **({"folder": folder_path} if folder_ok else {}),
                            },
                        },
                    )
                )
                created_case_key = data["test_case"].get("key")
                return f"created {created_case_key}"

            case_ok = await run_check(report, "aio_create_test_case", check_create_case)

            if case_ok and created_case_key:

                async def check_update_case() -> str:
                    data = decode(
                        await client.call_tool(
                            "aio_update_test_case",
                            {
                                "project_key": project,
                                "test_case_id": created_case_key,
                                "fields": {"description": "Updated by the live check."},
                                "steps": [
                                    {
                                        "step": "Re-run the live check",
                                        "expected_result": "The update is visible",
                                    }
                                ],
                            },
                        )
                    )
                    case = data["test_case"]
                    if len(case.get("steps", [])) != 1:
                        raise RuntimeError(
                            "expected the steps to be replaced, got "
                            f"{case.get('steps')}"
                        )
                    return f"updated {case.get('key')}, steps replaced"

                await run_check(report, "aio_update_test_case", check_update_case)
            else:
                report.record(
                    Outcome(
                        "aio_update_test_case", "skip", "no case was created to update"
                    )
                )

    passed = sum(1 for item in report.outcomes if item.status == "pass")
    skipped = sum(1 for item in report.outcomes if item.status == "skip")
    failed = len(report.failed)
    colour = RED if failed else GREEN
    print(f"\n{colour}  {passed} passed, {failed} failed, {skipped} skipped{RESET}\n")
    if created_case_key and not args.keep:
        print(f"{YELLOW}  Left behind in {project}: case {created_case_key}{RESET}")
    if created_folder and not args.keep:
        print(f"{YELLOW}  Left behind in {project}: folder {created_folder}{RESET}")
    if created_case_key or created_folder:
        print(f"{DIM}  AIO Tests has no delete API, so remove these in Jira.{RESET}\n")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
