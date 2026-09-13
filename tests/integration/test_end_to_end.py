"""End-to-end tests: real MCP protocol, real HTTP, fake AIO Tests deployment.

Every test here starts the published console script as a subprocess, speaks MCP
over stdio to it, and lets it call a fake AIO Tests API over the loopback
interface. Nothing is monkeypatched, so a passing test means the whole path
works: tool registration and filtering, argument validation, payload building,
the ``requests`` transport, response parsing and JSON serialization.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client
from mcp.shared.exceptions import McpError

from .conftest import PROJECT_KEY, make_client
from .fake_aio_server import FakeAIO

ALL_TOOLS = {
    "aio_get_project",
    "aio_get_test_case_schema",
    "aio_search_test_cases",
    "aio_get_test_case",
    "aio_get_test_case_versions",
    "aio_create_test_case",
    "aio_update_test_case",
    "aio_get_folder_hierarchy",
    "aio_create_folder",
    "aio_get_tags",
}
READ_TOOLS = {
    "aio_get_project",
    "aio_get_test_case_schema",
    "aio_search_test_cases",
    "aio_get_test_case",
    "aio_get_test_case_versions",
    "aio_get_folder_hierarchy",
    "aio_get_tags",
}
WRITE_TOOLS = ALL_TOOLS - READ_TOOLS


async def call(client: Client, tool: str, **arguments: Any) -> Any:
    """Invoke a tool and decode its JSON result.

    Args:
        client: The connected MCP client.
        tool: Tool name.
        **arguments: Tool arguments.

    Returns:
        The decoded tool result.
    """
    result = await client.call_tool(tool, arguments)
    return json.loads(result.content[0].text)


def last_request(fake_aio: FakeAIO, method: str, needle: str) -> dict[str, Any]:
    """Return the most recent recorded request matching a method and path.

    Args:
        fake_aio: The running fake deployment.
        method: HTTP method to match.
        needle: Substring the path must contain.

    Returns:
        The matching request record.
    """
    for record in reversed(fake_aio.requests):
        if record["method"] == method and needle in record["path"]:
            return record
    raise AssertionError(f"No {method} request with '{needle}' in {fake_aio.requests}")


# --------------------------------------------------------------------------
# Tool surface
# --------------------------------------------------------------------------


async def test_every_tool_is_exposed(client: Client) -> None:
    """The server advertises exactly the documented tool set."""
    names = {tool.name for tool in await client.list_tools()}
    assert names == ALL_TOOLS


async def test_read_only_mode_hides_write_tools(
    fake_aio: FakeAIO, server_cwd: Path
) -> None:
    """READ_ONLY_MODE removes every write tool from the listing."""
    async with make_client(fake_aio, server_cwd, READ_ONLY_MODE="true") as client:
        names = {tool.name for tool in await client.list_tools()}
    assert names == READ_TOOLS
    assert not names & WRITE_TOOLS


async def test_enabled_tools_filter(fake_aio: FakeAIO, server_cwd: Path) -> None:
    """ENABLED_TOOLS narrows the listing to the named tools."""
    async with make_client(
        fake_aio,
        server_cwd,
        ENABLED_TOOLS="aio_get_project,aio_get_tags",
    ) as client:
        names = {tool.name for tool in await client.list_tools()}
    assert names == {"aio_get_project", "aio_get_tags"}


async def test_no_credentials_hides_every_tool(
    fake_aio: FakeAIO, server_cwd: Path
) -> None:
    """Without credentials the tools are withheld rather than failing at call time."""
    async with make_client(
        fake_aio, server_cwd, AIO_ENABLED="false", AIO_PERSONAL_TOKEN=""
    ) as client:
        assert await client.list_tools() == []


# --------------------------------------------------------------------------
# Read tools
# --------------------------------------------------------------------------


async def test_get_project(client: Client) -> None:
    """A configured project reports itself as AIO-enabled with its Jira ID."""
    result = await call(client, "aio_get_project", project_key=PROJECT_KEY)
    assert result == {
        "aio_enabled": True,
        "project_key": PROJECT_KEY,
        "project_id": 10500,
        "adhoc_cycle_key": "AT-CY-1",
    }


async def test_get_project_reports_a_project_without_aio(client: Client) -> None:
    """An unknown project is reported as disabled instead of raising."""
    result = await call(client, "aio_get_project", project_key="NOPE")
    assert result["aio_enabled"] is False
    assert "error" in result


async def test_get_test_case_schema(client: Client) -> None:
    """The schema lists built-in fields, case custom fields and allowed values."""
    schema = await call(client, "aio_get_test_case_schema", project_key=PROJECT_KEY)

    assert schema["project_key"] == PROJECT_KEY
    assert schema["project_id"] == 10500
    assert "title" in schema["required_fields"]
    # Required custom fields are surfaced alongside the built-in ones.
    assert "Test Notes" in schema["required_fields"]

    custom_names = {field["name"] for field in schema["custom_fields"]}
    assert custom_names == {"Environment", "Test Notes"}, (
        "custom fields not associated with cases must be filtered out"
    )

    allowed = schema["allowed_values"]
    assert {entity["name"] for entity in allowed["statuses"]} == {
        "Draft",
        "Published",
        "Deprecated",
    }
    assert {entity["name"] for entity in allowed["script_types"]} == {"Classic", "BDD"}

    read_only = {field["name"] for field in schema["fields"] if field.get("read_only")}
    assert {"key", "version", "createdDate"} <= read_only


async def test_get_tags(client: Client) -> None:
    """Tags are returned with their IDs and names."""
    result = await call(client, "aio_get_tags", project_key=PROJECT_KEY)
    assert result == {
        "tags": [{"id": 201, "name": "smoke"}, {"id": 202, "name": "critical"}]
    }


async def test_get_folder_hierarchy_tree(client: Client) -> None:
    """The nested tree carries children and derived paths."""
    result = await call(client, "aio_get_folder_hierarchy", project_key=PROJECT_KEY)
    assert result["folder_type"] == "testcase"
    regression = result["folders"][0]
    assert regression["name"] == "Regression"
    assert regression["path"] == "/Regression"
    child_paths = {child["path"] for child in regression["children"]}
    assert child_paths == {"/Regression/Checkout", "/Regression/Login"}
    # Nested folders have no parentID in the API payload; it must be derived.
    assert all(child["parent_id"] == 1 for child in regression["children"])


async def test_get_folder_hierarchy_flat(client: Client) -> None:
    """The flat listing returns every folder, parents before children."""
    result = await call(
        client, "aio_get_folder_hierarchy", project_key=PROJECT_KEY, flat=True
    )
    paths = [folder["path"] for folder in result["folders"]]
    assert paths == [
        "/Regression",
        "/Regression/Checkout",
        "/Regression/Login",
        "/Smoke",
    ]


@pytest.mark.parametrize("folder_type", ["testcase", "testcycle", "testset"])
async def test_get_folder_hierarchy_every_type(
    client: Client, fake_aio: FakeAIO, folder_type: str
) -> None:
    """Each folder tree is addressed by its own API path segment."""
    result = await call(
        client,
        "aio_get_folder_hierarchy",
        project_key=PROJECT_KEY,
        folder_type=folder_type,
    )
    assert result["folder_type"] == folder_type
    assert last_request(fake_aio, "GET", "/folder")["path"].endswith(
        f"/{folder_type}/folder"
    )


async def test_folder_tree_missing_on_this_deployment(
    client: Client, fake_aio: FakeAIO
) -> None:
    """A 404 on a folder tree explains itself instead of surfacing a bare 404.

    Jira Server/Data Center has no ``/testset/folder`` endpoint, so a caller
    following the tool description gets a 404 that looks like a missing project.
    """
    fake_aio.state.folders.pop("testset", None)
    with pytest.raises(Exception) as excinfo:
        await call(
            client,
            "aio_get_folder_hierarchy",
            project_key=PROJECT_KEY,
            folder_type="testset",
        )
    message = str(excinfo.value)
    assert "testset" in message
    assert "does not provide" in message
    assert "testcase" in message and "testcycle" in message


async def test_get_test_case_by_key(client: Client) -> None:
    """A case is returned in full, with HTML stripped by default."""
    case = await call(
        client, "aio_get_test_case", project_key=PROJECT_KEY, test_case_id="AT-TC-1"
    )
    assert case["key"] == "AT-TC-1"
    assert case["title"] == "Login with valid credentials"
    assert case["description"] == "Happy path login"
    assert case["priority"] == {"id": 10, "name": "Critical"}
    assert case["folder"] == {"id": 3, "name": "Login"}
    assert case["tags"] == [{"id": 201, "name": "smoke"}]
    assert case["jira_requirement_ids"] == ["AT-42"]
    assert [step["step"] for step in case["steps"]] == [
        "Open the login page",
        "Submit valid credentials",
    ]


async def test_get_test_case_by_numeric_id(client: Client) -> None:
    """A numeric case ID addresses the same case as its key."""
    case = await call(
        client, "aio_get_test_case", project_key=PROJECT_KEY, test_case_id="901"
    )
    assert case["key"] == "AT-TC-2"


async def test_get_test_case_with_rtf(client: Client, fake_aio: FakeAIO) -> None:
    """include_rtf keeps the HTML markup and sets the API flag."""
    case = await call(
        client,
        "aio_get_test_case",
        project_key=PROJECT_KEY,
        test_case_id="AT-TC-1",
        include_rtf=True,
    )
    assert case["description"] == "<p>Happy path login</p>"
    assert last_request(fake_aio, "GET", "/detail")["params"]["needDataInRTF"] == "True"


async def test_get_test_case_bdd_steps(client: Client) -> None:
    """BDD steps round-trip with their step types."""
    case = await call(
        client, "aio_get_test_case", project_key=PROJECT_KEY, test_case_id="AT-TC-2"
    )
    assert [(step["step_type"], step["bdd_step"]) for step in case["steps"]] == [
        ("BDD_GIVEN", "a card that expired"),
        ("BDD_WHEN", "the order is placed"),
        ("BDD_THEN", "payment is refused"),
    ]


async def test_get_test_case_not_found(client: Client) -> None:
    """A missing case surfaces as a tool error, not a silent empty result."""
    with pytest.raises((McpError, Exception)) as excinfo:
        await call(
            client,
            "aio_get_test_case",
            project_key=PROJECT_KEY,
            test_case_id="AT-TC-999",
        )
    assert "404" in str(excinfo.value) or "not found" in str(excinfo.value).lower()


async def test_get_test_case_versions(client: Client) -> None:
    """Version history reports every saved version and flags the current one."""
    result = await call(
        client,
        "aio_get_test_case_versions",
        project_key=PROJECT_KEY,
        test_case_id="AT-TC-1",
    )
    assert result["key"] == "AT-TC-1"
    assert result["current_version"] == 2
    assert result["versions"] == [
        {"is_current": False, "version": 1, "id": 899},
        {"is_current": True, "version": 2, "id": 900},
    ]


# --------------------------------------------------------------------------
# Search
# --------------------------------------------------------------------------


async def test_search_without_filters_lists_cases(
    client: Client, fake_aio: FakeAIO
) -> None:
    """An unfiltered search uses the plain listing endpoint, not search."""
    result = await call(client, "aio_search_test_cases", project_key=PROJECT_KEY)
    assert result["count"] == 2
    assert {case["key"] for case in result["test_cases"]} == {"AT-TC-1", "AT-TC-2"}
    assert not any(record["path"].endswith("/search") for record in fake_aio.requests)


async def test_search_by_title(client: Client, fake_aio: FakeAIO) -> None:
    """A title filter posts a CONTAINS criterion and narrows the result."""
    result = await call(
        client, "aio_search_test_cases", project_key=PROJECT_KEY, title="Checkout"
    )
    assert [case["key"] for case in result["test_cases"]] == ["AT-TC-2"]
    body = last_request(fake_aio, "POST", "/search")["body"]
    assert body["title"] == {"comparisonType": "CONTAINS", "value": "Checkout"}


async def test_search_by_exact_title(client: Client) -> None:
    """EXACT_MATCH only returns a case whose title matches in full."""
    partial = await call(
        client,
        "aio_search_test_cases",
        project_key=PROJECT_KEY,
        title="Checkout",
        title_match="EXACT_MATCH",
    )
    assert partial["test_cases"] == []
    full = await call(
        client,
        "aio_search_test_cases",
        project_key=PROJECT_KEY,
        title="Checkout with an expired card",
        title_match="EXACT_MATCH",
    )
    assert [case["key"] for case in full["test_cases"]] == ["AT-TC-2"]


async def test_search_resolves_lookup_names_to_ids(
    client: Client, fake_aio: FakeAIO
) -> None:
    """Status, priority and type names are resolved against the project config."""
    result = await call(
        client,
        "aio_search_test_cases",
        project_key=PROJECT_KEY,
        statuses=["Published"],
        priorities=["Critical"],
        types=["Functional"],
        automation_statuses=["Manual"],
    )
    assert [case["key"] for case in result["test_cases"]] == ["AT-TC-1"]
    body = last_request(fake_aio, "POST", "/search")["body"]
    assert body["statusID"] == {"comparisonType": "IN", "list": [21]}
    assert body["priorityID"] == {"comparisonType": "IN", "list": [10]}
    assert body["typeID"] == {"comparisonType": "IN", "list": [1]}
    assert body["automationStatusID"] == {"comparisonType": "IN", "list": [30]}


async def test_search_rejects_an_unknown_lookup_name(client: Client) -> None:
    """An unknown status name fails with the list of valid values."""
    with pytest.raises(Exception) as excinfo:
        await call(
            client,
            "aio_search_test_cases",
            project_key=PROJECT_KEY,
            statuses=["Nonexistent"],
        )
    message = str(excinfo.value)
    assert "Nonexistent" in message
    assert "Published" in message, "the error should list the accepted values"


async def test_search_resolves_folder_paths(client: Client, fake_aio: FakeAIO) -> None:
    """A folder path is resolved to its numeric ID before searching."""
    result = await call(
        client,
        "aio_search_test_cases",
        project_key=PROJECT_KEY,
        folders=["/Regression/Login"],
    )
    assert [case["key"] for case in result["test_cases"]] == ["AT-TC-1"]
    body = last_request(fake_aio, "POST", "/search")["body"]
    assert body["folderID"] == {"comparisonType": "IN", "list": [3]}


async def test_search_by_folder_name(client: Client) -> None:
    """A unique bare folder name resolves without a full path."""
    result = await call(
        client, "aio_search_test_cases", project_key=PROJECT_KEY, folders=["Checkout"]
    )
    assert [case["key"] for case in result["test_cases"]] == ["AT-TC-2"]


async def test_search_by_keys_tags_and_requirements(
    client: Client, fake_aio: FakeAIO
) -> None:
    """Key, tag and requirement filters are sent as IN criteria."""
    by_key = await call(
        client, "aio_search_test_cases", project_key=PROJECT_KEY, keys=["AT-TC-2"]
    )
    assert [case["key"] for case in by_key["test_cases"]] == ["AT-TC-2"]

    by_tag = await call(
        client, "aio_search_test_cases", project_key=PROJECT_KEY, tags=["smoke"]
    )
    assert [case["key"] for case in by_tag["test_cases"]] == ["AT-TC-1"]

    by_requirement = await call(
        client,
        "aio_search_test_cases",
        project_key=PROJECT_KEY,
        requirement_ids=["AT-42"],
    )
    assert [case["key"] for case in by_requirement["test_cases"]] == ["AT-TC-1"]
    assert last_request(fake_aio, "POST", "/search")["body"]["requirementID"] == {
        "comparisonType": "IN",
        "list": ["AT-42"],
    }


async def test_search_by_automation_key(client: Client) -> None:
    """The automation key is matched as a substring."""
    result = await call(
        client,
        "aio_search_test_cases",
        project_key=PROJECT_KEY,
        automation_key="expired_card",
    )
    assert [case["key"] for case in result["test_cases"]] == ["AT-TC-2"]


async def test_search_date_criteria(client: Client, fake_aio: FakeAIO) -> None:
    """Open-ended and closed date ranges map to the right comparison types."""
    await call(
        client,
        "aio_search_test_cases",
        project_key=PROJECT_KEY,
        created_after="2026-01-01T00:00:00Z",
    )
    assert last_request(fake_aio, "POST", "/search")["body"]["createdDate"] == {
        "comparisonType": "AFTER",
        "value1": "2026-01-01T00:00:00Z",
    }

    await call(
        client,
        "aio_search_test_cases",
        project_key=PROJECT_KEY,
        updated_after="2026-01-01T00:00:00Z",
        updated_before="2026-12-31T00:00:00Z",
    )
    assert last_request(fake_aio, "POST", "/search")["body"]["updatedDate"] == {
        "comparisonType": "BETWEEN",
        "value1": "2026-01-01T00:00:00Z",
        "value2": "2026-12-31T00:00:00Z",
    }


async def test_search_include_archived_flag(client: Client, fake_aio: FakeAIO) -> None:
    """include_archived is only sent when it is explicitly set."""
    await call(client, "aio_search_test_cases", project_key=PROJECT_KEY, title="Login")
    assert "isArchived" not in last_request(fake_aio, "POST", "/search")["body"]

    result = await call(
        client,
        "aio_search_test_cases",
        project_key=PROJECT_KEY,
        title="Login",
        include_archived=False,
    )
    assert last_request(fake_aio, "POST", "/search")["body"]["isArchived"] == {
        "value": False
    }
    assert [case["key"] for case in result["test_cases"]] == ["AT-TC-1"]


async def test_search_pagination_below_the_api_minimum(
    client: Client, fake_aio: FakeAIO
) -> None:
    """A max_results below the API minimum is requested at the floor and trimmed."""
    result = await call(
        client, "aio_search_test_cases", project_key=PROJECT_KEY, max_results=1
    )
    assert len(result["test_cases"]) == 1
    assert result["max_results"] == 1
    # The API silently resets anything under 10, so the request must use the floor.
    assert last_request(fake_aio, "GET", "/testcase")["params"]["maxResults"] == "10"


async def test_search_reports_the_total_match_count(client: Client) -> None:
    """The total across every page is reported, so a caller can decide to paginate."""
    page = await call(
        client, "aio_search_test_cases", project_key=PROJECT_KEY, max_results=1
    )
    assert page["count"] == 1, "one case on this page"
    assert page["total"] == 2, "two cases match in total"
    assert page["is_last"] is False


async def test_search_start_at(client: Client, fake_aio: FakeAIO) -> None:
    """start_at is forwarded and pages through the result set."""
    result = await call(
        client, "aio_search_test_cases", project_key=PROJECT_KEY, start_at=1
    )
    assert [case["key"] for case in result["test_cases"]] == ["AT-TC-2"]
    assert last_request(fake_aio, "GET", "/testcase")["params"]["startAt"] == "1"


async def test_search_rejects_out_of_range_max_results(client: Client) -> None:
    """The tool schema rejects a page size above the documented cap."""
    with pytest.raises(Exception):
        await call(
            client, "aio_search_test_cases", project_key=PROJECT_KEY, max_results=500
        )


# --------------------------------------------------------------------------
# Write tools
# --------------------------------------------------------------------------


async def test_create_minimal_test_case(client: Client, fake_aio: FakeAIO) -> None:
    """A case can be created from a title alone."""
    result = await call(
        client,
        "aio_create_test_case",
        project_key=PROJECT_KEY,
        title="Password reset sends an email",
    )
    assert result["success"] is True
    assert result["test_case"]["title"] == "Password reset sends an email"
    assert result["test_case"]["key"] == "AT-TC-3"

    body = last_request(fake_aio, "POST", "/testcase")["body"]
    assert body == {"title": "Password reset sends an email"}


async def test_create_test_case_with_classic_steps_and_fields(
    client: Client, fake_aio: FakeAIO
) -> None:
    """Lookup names, folders and steps are resolved into the API payload."""
    result = await call(
        client,
        "aio_create_test_case",
        project_key=PROJECT_KEY,
        title="Guest checkout",
        steps=[
            {
                "step": "Add an item to the basket",
                "data": "SKU-1",
                "expected_result": "The basket shows 1 item",
            },
            {"step": "Check out as a guest", "expected_result": "The order is placed"},
        ],
        fields={
            "description": "Covers the guest flow",
            "precondition": "The catalogue is populated",
            "folder": "/Regression/Checkout",
            "status": "Draft",
            "priority": "Critical",
            "type": "Functional",
            "script_type": "Classic",
            "automation_status": "Manual",
            "automation_key": "checkout.guest",
            "estimated_effort": 300,
            "requirement_ids": ["AT-7"],
            "component_ids": [11],
            "release_ids": [22],
        },
    )
    assert result["success"] is True

    body = last_request(fake_aio, "POST", "/testcase")["body"]
    assert body["title"] == "Guest checkout"
    assert body["folder"] == {"ID": 2}
    assert body["status"] == {"ID": 20}
    assert body["priority"] == {"ID": 10}
    assert body["type"] == {"ID": 1}
    assert body["scriptType"] == {"ID": 40}
    assert body["automationStatus"] == {"ID": 30}
    assert body["automationKey"] == "checkout.guest"
    assert body["estimatedEffort"] == 300
    assert body["jiraRequirementIDs"] == ["AT-7"]
    assert body["jiraComponentIDs"] == [11]
    assert body["jiraReleaseIDs"] == [22]
    assert body["steps"] == [
        {
            "step": "Add an item to the basket",
            "data": "SKU-1",
            "expectedResult": "The basket shows 1 item",
            "stepType": "TEXT",
        },
        {
            "step": "Check out as a guest",
            "expectedResult": "The order is placed",
            "stepType": "TEXT",
        },
    ]


async def test_create_test_case_with_bdd_steps(
    client: Client, fake_aio: FakeAIO
) -> None:
    """BDD steps keep their step types and are sent under bddStep."""
    await call(
        client,
        "aio_create_test_case",
        project_key=PROJECT_KEY,
        title="Refund a paid order",
        steps=[
            {"step_type": "BDD_GIVEN", "bdd_step": "a paid order"},
            {"step_type": "BDD_WHEN", "bdd_step": "a refund is requested"},
            {"step_type": "BDD_THEN", "bdd_step": "the balance is restored"},
        ],
        fields={"script_type": "BDD"},
    )
    body = last_request(fake_aio, "POST", "/testcase")["body"]
    assert body["scriptType"] == {"ID": 41}
    assert body["steps"] == [
        {"stepType": "BDD_GIVEN", "bddStep": "a paid order"},
        {"stepType": "BDD_WHEN", "bddStep": "a refund is requested"},
        {"stepType": "BDD_THEN", "bddStep": "the balance is restored"},
    ]


async def test_create_test_case_with_a_reference_step(
    client: Client, fake_aio: FakeAIO
) -> None:
    """A REFERENCE step points at another case by key."""
    await call(
        client,
        "aio_create_test_case",
        project_key=PROJECT_KEY,
        title="Full purchase journey",
        steps=[
            {"step_type": "REFERENCE", "referenced_case_key": "AT-TC-1"},
            {"step": "Verify the confirmation email"},
        ],
    )
    body = last_request(fake_aio, "POST", "/testcase")["body"]
    assert body["steps"][0] == {
        "stepType": "REFERENCE",
        "referencedCase": {"key": "AT-TC-1"},
    }


async def test_create_test_case_creates_missing_tags(
    client: Client, fake_aio: FakeAIO
) -> None:
    """Unknown tags are created first, then referenced by ID."""
    await call(
        client,
        "aio_create_test_case",
        project_key=PROJECT_KEY,
        title="Tagged case",
        fields={"tags": ["smoke", "brand-new-tag"]},
    )
    created = last_request(fake_aio, "POST", "/tag")["body"]
    assert created == [{"name": "brand-new-tag"}]

    body = last_request(fake_aio, "POST", "/testcase")["body"]
    assert body["tags"] == [
        {"tag": {"ID": 201, "name": "smoke"}},
        {"tag": {"name": "brand-new-tag", "ID": 203}},
    ]


async def test_create_test_case_creates_a_missing_folder(
    client: Client, fake_aio: FakeAIO
) -> None:
    """A folder path that does not exist yet is created on demand."""
    await call(
        client,
        "aio_create_test_case",
        project_key=PROJECT_KEY,
        title="Case in a new folder",
        fields={"folder": "/Release 2.0/API"},
    )
    hierarchy = last_request(fake_aio, "PUT", "/folder/hierarchy")["body"]
    assert hierarchy == {"folderHierarchy": ["Release 2.0", "API"]}

    folders = await call(
        client, "aio_get_folder_hierarchy", project_key=PROJECT_KEY, flat=True
    )
    assert "/Release 2.0/API" in {folder["path"] for folder in folders["folders"]}


async def test_create_test_case_refuses_a_missing_folder_when_asked(
    client: Client,
) -> None:
    """With create_folder_if_missing disabled, an unknown folder is an error."""
    with pytest.raises(Exception) as excinfo:
        await call(
            client,
            "aio_create_test_case",
            project_key=PROJECT_KEY,
            title="Should not be created",
            fields={"folder": "/Does/Not/Exist"},
            create_folder_if_missing=False,
        )
    assert "not found" in str(excinfo.value).lower()


async def test_create_test_case_with_custom_fields(
    client: Client, fake_aio: FakeAIO
) -> None:
    """Custom fields are resolved from names to IDs."""
    await call(
        client,
        "aio_create_test_case",
        project_key=PROJECT_KEY,
        title="Case with custom fields",
        fields={
            "custom_fields": {"Environment": "Staging", "Test Notes": "Run nightly"}
        },
    )
    body = last_request(fake_aio, "POST", "/testcase")["body"]
    assert body["customFields"] == [
        {"ID": 10113, "name": "Environment", "value": "Staging"},
        {"ID": 10114, "name": "Test Notes", "value": "Run nightly"},
    ]


async def test_create_test_case_rejects_an_unknown_custom_field(
    client: Client,
) -> None:
    """An undefined custom field fails with the list of defined ones."""
    with pytest.raises(Exception) as excinfo:
        await call(
            client,
            "aio_create_test_case",
            project_key=PROJECT_KEY,
            title="Bad custom field",
            fields={"custom_fields": {"Nope": "x"}},
        )
    assert "Nope" in str(excinfo.value)
    assert "Environment" in str(excinfo.value)


async def test_create_test_case_rejects_unknown_fields(client: Client) -> None:
    """A misspelled field name fails with the supported field list."""
    with pytest.raises(Exception) as excinfo:
        await call(
            client,
            "aio_create_test_case",
            project_key=PROJECT_KEY,
            title="Bad field",
            fields={"priorityy": "Critical"},
        )
    assert "priorityy" in str(excinfo.value)


async def test_create_test_case_rejects_a_malformed_step(client: Client) -> None:
    """A BDD step type without bdd_step text is rejected before the API call."""
    with pytest.raises(Exception) as excinfo:
        await call(
            client,
            "aio_create_test_case",
            project_key=PROJECT_KEY,
            title="Bad step",
            steps=[{"step_type": "BDD_GIVEN", "step": "wrong key for BDD"}],
        )
    assert "bdd_step" in str(excinfo.value)


async def test_create_test_case_rejects_an_empty_title(client: Client) -> None:
    """An all-whitespace title is rejected."""
    with pytest.raises(Exception):
        await call(client, "aio_create_test_case", project_key=PROJECT_KEY, title="   ")


async def test_create_test_case_escapes_plain_text_by_default(
    client: Client, fake_aio: FakeAIO
) -> None:
    """Without include_rtf, plain text is sent verbatim rather than as markup."""
    await call(
        client,
        "aio_create_test_case",
        project_key=PROJECT_KEY,
        title="Plain text case",
        fields={"description": "a < b & c"},
    )
    body = last_request(fake_aio, "POST", "/testcase")["body"]
    assert body["description"] == "a < b & c"


# --------------------------------------------------------------------------
# Update
# --------------------------------------------------------------------------


async def test_update_test_case_changes_only_the_given_fields(
    client: Client, fake_aio: FakeAIO
) -> None:
    """An update carries the whole document forward, changing only what was given."""
    result = await call(
        client,
        "aio_update_test_case",
        project_key=PROJECT_KEY,
        test_case_id="AT-TC-1",
        fields={"priority": "Low", "title": "Login with valid credentials (v2)"},
    )
    assert result["success"] is True
    assert result["test_case"]["title"] == "Login with valid credentials (v2)"
    assert result["test_case"]["priority"] == {"id": 12, "name": "Low"}

    body = last_request(fake_aio, "PUT", "/detail")["body"]
    assert body["priority"] == {"ID": 12}
    assert body["title"] == "Login with valid credentials (v2)"
    # Untouched fields are carried over so the replace-style API keeps them.
    assert body["status"] == {"ID": 21, "name": "Published"}
    assert body["folder"] == {"ID": 3, "name": "Login"}
    assert len(body["steps"]) == 2


async def test_update_test_case_strips_read_only_fields(
    client: Client, fake_aio: FakeAIO
) -> None:
    """Read-only attributes are removed before the document is written back."""
    await call(
        client,
        "aio_update_test_case",
        project_key=PROJECT_KEY,
        test_case_id="AT-TC-1",
        fields={"priority": "Low"},
    )
    body = last_request(fake_aio, "PUT", "/detail")["body"]
    for read_only in ("ID", "key", "version", "versions", "createdDate", "updatedDate"):
        assert read_only not in body, f"{read_only} must not be sent back"
    # Nested identifiers are how the API keeps existing rows attached, so they
    # must survive the strip.
    assert body["folder"]["ID"] == 3
    assert body["steps"][0]["ID"] == 1


async def test_update_test_case_preserves_untouched_formatting(
    client: Client, fake_aio: FakeAIO
) -> None:
    """Rich text left alone keeps its markup instead of being flattened."""
    await call(
        client,
        "aio_update_test_case",
        project_key=PROJECT_KEY,
        test_case_id="AT-TC-1",
        fields={"priority": "Low"},
    )
    body = last_request(fake_aio, "PUT", "/detail")["body"]
    assert body["description"] == "<p>Happy path login</p>"
    assert body["steps"][0]["step"] == "<p>Open the login page</p>"
    # The read-back must ask for RTF, otherwise the markup is lost on write-back.
    assert last_request(fake_aio, "GET", "/detail")["params"]["needDataInRTF"] == "True"


async def test_update_test_case_escapes_plain_text(
    client: Client, fake_aio: FakeAIO
) -> None:
    """Plain-text input is escaped so it survives the rich-text round trip."""
    await call(
        client,
        "aio_update_test_case",
        project_key=PROJECT_KEY,
        test_case_id="AT-TC-1",
        fields={"description": "a < b & c\nsecond line"},
    )
    body = last_request(fake_aio, "PUT", "/detail")["body"]
    assert body["description"] == "a &lt; b &amp; c<br/>second line"


async def test_update_test_case_with_html(client: Client, fake_aio: FakeAIO) -> None:
    """include_rtf sends the supplied HTML through untouched."""
    await call(
        client,
        "aio_update_test_case",
        project_key=PROJECT_KEY,
        test_case_id="AT-TC-1",
        fields={"description": "<p><b>Bold</b> intro</p>"},
        include_rtf=True,
    )
    body = last_request(fake_aio, "PUT", "/detail")["body"]
    assert body["description"] == "<p><b>Bold</b> intro</p>"


async def test_update_test_case_replaces_steps(
    client: Client, fake_aio: FakeAIO
) -> None:
    """Supplying steps replaces the existing list rather than appending."""
    await call(
        client,
        "aio_update_test_case",
        project_key=PROJECT_KEY,
        test_case_id="AT-TC-1",
        steps=[{"step": "Only step", "expected_result": "Only result"}],
    )
    body = last_request(fake_aio, "PUT", "/detail")["body"]
    assert body["steps"] == [
        {
            "step": "Only step",
            "expectedResult": "Only result",
            "stepType": "TEXT",
        }
    ]

    case = await call(
        client, "aio_get_test_case", project_key=PROJECT_KEY, test_case_id="AT-TC-1"
    )
    assert len(case["steps"]) == 1


async def test_update_test_case_creates_a_new_version(
    client: Client, fake_aio: FakeAIO
) -> None:
    """create_new_version bumps the version instead of editing in place."""
    await call(
        client,
        "aio_update_test_case",
        project_key=PROJECT_KEY,
        test_case_id="AT-TC-2",
        fields={"priority": "Low"},
        create_new_version=True,
    )
    assert (
        last_request(fake_aio, "PUT", "/detail")["params"]["createNewVersion"] == "True"
    )
    versions = await call(
        client,
        "aio_get_test_case_versions",
        project_key=PROJECT_KEY,
        test_case_id="AT-TC-2",
    )
    assert versions["current_version"] == 2


async def test_update_test_case_without_fields_is_rejected(client: Client) -> None:
    """An update with nothing to change fails instead of writing the case back."""
    with pytest.raises(Exception) as excinfo:
        await call(
            client,
            "aio_update_test_case",
            project_key=PROJECT_KEY,
            test_case_id="AT-TC-1",
        )
    assert "no fields" in str(excinfo.value).lower()


# --------------------------------------------------------------------------
# Folders
# --------------------------------------------------------------------------


async def test_create_folder_nested_path(client: Client, fake_aio: FakeAIO) -> None:
    """A whole path is created in one call and the leaf is returned with its path."""
    result = await call(
        client,
        "aio_create_folder",
        project_key=PROJECT_KEY,
        folder_path="/Release 3.0/API/Contracts",
    )
    assert result["success"] is True
    assert result["folder"]["name"] == "Contracts"
    assert result["folder"]["path"] == "/Release 3.0/API/Contracts"
    assert last_request(fake_aio, "PUT", "/folder/hierarchy")["body"][
        "folderHierarchy"
    ] == ["Release 3.0", "API", "Contracts"]


async def test_create_folder_is_idempotent(client: Client) -> None:
    """Creating an existing folder returns it instead of duplicating it."""
    first = await call(
        client, "aio_create_folder", project_key=PROJECT_KEY, folder_path="/Smoke"
    )
    second = await call(
        client, "aio_create_folder", project_key=PROJECT_KEY, folder_path="/Smoke"
    )
    assert first["folder"]["id"] == second["folder"]["id"] == 4


async def test_create_folder_under_a_parent(client: Client, fake_aio: FakeAIO) -> None:
    """parent_folder_id nests the new path under an existing folder."""
    result = await call(
        client,
        "aio_create_folder",
        project_key=PROJECT_KEY,
        folder_path="Payments",
        parent_folder_id=1,
    )
    assert result["folder"]["path"] == "/Regression/Payments"
    body = last_request(fake_aio, "PUT", "/folder/hierarchy")["body"]
    assert body == {"folderHierarchy": ["Payments"], "baseFolderId": 1}


async def test_create_folder_in_another_tree(client: Client, fake_aio: FakeAIO) -> None:
    """A folder can be created in the test cycle tree."""
    result = await call(
        client,
        "aio_create_folder",
        project_key=PROJECT_KEY,
        folder_path="Sprint 12",
        folder_type="testcycle",
    )
    assert result["folder_type"] == "testcycle"
    assert last_request(fake_aio, "PUT", "/folder/hierarchy")["path"].endswith(
        "/testcycle/folder/hierarchy"
    )


async def test_create_folder_rejects_an_unknown_type(client: Client) -> None:
    """An unsupported folder type is rejected with the accepted values."""
    with pytest.raises(Exception) as excinfo:
        await call(
            client,
            "aio_create_folder",
            project_key=PROJECT_KEY,
            folder_path="X",
            folder_type="testplan",
        )
    assert "testcase" in str(excinfo.value)


async def test_create_folder_rejects_an_empty_path(client: Client) -> None:
    """A path made only of separators is rejected."""
    with pytest.raises(Exception):
        await call(
            client, "aio_create_folder", project_key=PROJECT_KEY, folder_path="///"
        )


# --------------------------------------------------------------------------
# Cross-cutting behaviour
# --------------------------------------------------------------------------


async def test_project_key_is_url_escaped(client: Client, fake_aio: FakeAIO) -> None:
    """A project key with unsafe characters is escaped into the path."""
    await call(client, "aio_get_project", project_key="A/B C")
    assert any("A%2FB%20C" in record["path"] for record in fake_aio.requests)


async def test_authentication_failure_is_reported_clearly(
    fake_aio: FakeAIO, server_cwd: Path
) -> None:
    """A rejected credential produces an actionable message, not a stack trace."""
    async with make_client(
        fake_aio, server_cwd, AIO_PERSONAL_TOKEN="wrong-token"
    ) as client:
        with pytest.raises(Exception) as excinfo:
            await call(client, "aio_get_tags", project_key=PROJECT_KEY)
    message = str(excinfo.value)
    assert "Authentication failed" in message
    assert "wrong-token" not in message, "credentials must not leak into errors"


async def test_write_tools_are_refused_in_read_only_mode(
    fake_aio: FakeAIO, server_cwd: Path
) -> None:
    """A write tool called directly in read-only mode is refused, not executed."""
    async with make_client(fake_aio, server_cwd, READ_ONLY_MODE="true") as client:
        with pytest.raises(Exception) as excinfo:
            await call(
                client,
                "aio_create_test_case",
                project_key=PROJECT_KEY,
                title="Should never be created",
            )
    assert "read-only" in str(excinfo.value).lower()
    assert not any(
        record["method"] == "POST" and record["path"].endswith("/testcase")
        for record in fake_aio.requests
    )
