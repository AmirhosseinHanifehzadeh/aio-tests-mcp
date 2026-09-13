"""A stateful in-process fake of the AIO Tests REST API.

Serves the subset of ``/rest/aio-tcms-api/1.0`` (Server/Data Center) and
``/aio-tcms/api/v1`` (Cloud) that this MCP server calls, over real HTTP, so
integration tests exercise the whole stack: MCP protocol, tool dispatch,
payload building, ``requests`` transport, response parsing and serialization.

The fake keeps state across calls, so a test can create a folder, create a case
in it, and read the case back the way a real deployment would behave. Every
request is recorded in :attr:`FakeAIO.requests` for contract assertions.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

# The project the fake knows about. Any other key is reported as not enabled,
# which is how a real deployment behaves for a project without AIO Tests.
KNOWN_PROJECT = "AT"
KNOWN_PROJECT_ID = 10500

PROJECT_CONFIG: dict[str, Any] = {
    "adhocTestCycle": {"key": "AT-CY-1", "jiraProjectID": KNOWN_PROJECT_ID},
    "caseTypes": [
        {"ID": 1, "name": "Functional", "isDefault": True},
        {"ID": 2, "name": "Performance"},
        {"ID": 3, "name": "Security"},
    ],
    "casePriorities": [
        {"ID": 10, "name": "Critical"},
        {"ID": 11, "name": "Medium", "isDefault": True},
        {"ID": 12, "name": "Low"},
    ],
    "caseStatuses": [
        {"ID": 20, "name": "Draft", "description": "Work in progress"},
        {"ID": 21, "name": "Published", "description": "Ready for execution"},
        {"ID": 22, "name": "Deprecated"},
    ],
    "caseAutomationStatuses": [
        {"ID": 30, "name": "Manual", "isDefault": True},
        {"ID": 31, "name": "Automated"},
    ],
    "caseScriptTypes": [
        {"ID": 40, "name": "Classic", "isEnabled": True},
        {"ID": 41, "name": "BDD", "isEnabled": True},
    ],
    "customFields": [
        {
            "ID": 10113,
            "name": "Environment",
            "description": "Setup used for the case",
            "type": "SINGLE_SELECT_LIST",
            "caseAssociation": {"isAssociated": True, "isRequired": False},
            "allowedListValues": [
                {"ID": 501, "value": "Staging"},
                {"ID": 502, "value": "Production"},
            ],
        },
        {
            "ID": 10114,
            "name": "Test Notes",
            "type": "MULTI_LINE_TEXT",
            "caseAssociation": {"isAssociated": True, "isRequired": True},
        },
        {
            "ID": 10115,
            "name": "Cycle Only",
            "type": "SINGLE_LINE_TEXT",
            "caseAssociation": {"isAssociated": False, "isRequired": False},
        },
    ],
}


@dataclass
class _State:
    """Mutable state of the fake deployment."""

    folders: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    tags: list[dict[str, Any]] = field(default_factory=list)
    cases: dict[int, dict[str, Any]] = field(default_factory=dict)
    next_folder_id: int = 100
    next_tag_id: int = 200
    next_case_id: int = 900
    next_case_number: int = 1


def _fresh_state() -> _State:
    """Build the initial state of the fake deployment.

    Returns:
        A state seeded with one folder tree, two tags and two test cases.
    """
    state = _State()
    state.folders = {
        "testcase": [
            {
                "ID": 1,
                "name": "Regression",
                "children": [
                    {"ID": 2, "name": "Checkout", "children": []},
                    {"ID": 3, "name": "Login", "children": []},
                ],
            },
            {"ID": 4, "name": "Smoke", "children": []},
        ],
        "testcycle": [{"ID": 50, "name": "Release 1.0", "children": []}],
        "testset": [],
    }
    state.next_folder_id = 100
    state.tags = [{"ID": 201, "name": "smoke"}, {"ID": 202, "name": "critical"}]
    state.next_tag_id = 203

    state.cases = {}
    state.next_case_id = 900
    state.next_case_number = 1
    _seed_case(
        state,
        title="Login with valid credentials",
        folder={"ID": 3, "name": "Login"},
        status={"ID": 21, "name": "Published"},
        priority={"ID": 10, "name": "Critical"},
        type={"ID": 1, "name": "Functional"},
        scriptType={"ID": 40, "name": "Classic"},
        automationStatus={"ID": 30, "name": "Manual"},
        description="<p>Happy path login</p>",
        tags=[{"tag": {"ID": 201, "name": "smoke"}}],
        jiraRequirementIDs=["AT-42"],
        steps=[
            {
                "ID": 1,
                "stepType": "TEXT",
                "step": "<p>Open the login page</p>",
                "data": "<p>https://example.test/login</p>",
                "expectedResult": "<p>The form is shown</p>",
            },
            {
                "ID": 2,
                "stepType": "TEXT",
                "step": "<p>Submit valid credentials</p>",
                "expectedResult": "<p>The dashboard opens</p>",
            },
        ],
    )
    _seed_case(
        state,
        title="Checkout with an expired card",
        folder={"ID": 2, "name": "Checkout"},
        status={"ID": 20, "name": "Draft"},
        priority={"ID": 11, "name": "Medium"},
        type={"ID": 1, "name": "Functional"},
        scriptType={"ID": 41, "name": "BDD"},
        automationStatus={"ID": 31, "name": "Automated"},
        automationKey="checkout.expired_card",
        tags=[{"tag": {"ID": 202, "name": "critical"}}],
        steps=[
            {"ID": 3, "stepType": "BDD_GIVEN", "bddStep": "<p>a card that expired</p>"},
            {"ID": 4, "stepType": "BDD_WHEN", "bddStep": "<p>the order is placed</p>"},
            {"ID": 5, "stepType": "BDD_THEN", "bddStep": "<p>payment is refused</p>"},
        ],
    )
    # A second version of the first case, so version history is non-trivial.
    first = state.cases[900]
    first["version"] = 2
    first["versions"] = [{"version": 1, "ID": 899}, {"version": 2, "ID": 900}]
    return state


def _seed_case(state: _State, **fields: Any) -> dict[str, Any]:
    """Insert a case into the fake deployment.

    Args:
        state: The state to insert into.
        **fields: Raw API-shaped case attributes.

    Returns:
        The stored case document.
    """
    case_id = state.next_case_id
    number = state.next_case_number
    state.next_case_id += 1
    state.next_case_number += 1
    case: dict[str, Any] = {
        "ID": case_id,
        "key": f"{KNOWN_PROJECT}-TC-{number}",
        "version": 1,
        "versions": [{"version": 1, "ID": case_id}],
        "jiraProjectID": KNOWN_PROJECT_ID,
        "createdDate": "2026-01-05T09:00:00.000+0000",
        "updatedDate": "2026-02-11T14:30:00.000+0000",
        "isArchived": False,
        "steps": [],
        "tags": [],
        "customFields": [],
        "jiraRequirementIDs": [],
        "jiraComponentIDs": [],
        "jiraReleaseIDs": [],
    }
    case.update(fields)
    state.cases[case_id] = case
    return case


def _strip_html(value: Any) -> Any:
    """Remove HTML markup the way AIO Tests does when ``needDataInRTF`` is off.

    Args:
        value: The value to strip; non-strings are returned unchanged.

    Returns:
        The value without tags, or unchanged when it is not a string.
    """
    if not isinstance(value, str):
        return value
    text = re.sub(r"<br\s*/?>", "\n", value)
    text = re.sub(r"<[^>]+>", "", text)
    return (
        text.replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#x27;", "'")
        .replace("&amp;", "&")
    )


_RICH_CASE_KEYS = ("description", "precondition")
_RICH_STEP_KEYS = ("step", "data", "expectedResult", "bddStep")


def _render_case(case: dict[str, Any], *, rtf: bool) -> dict[str, Any]:
    """Return a case document with rich text rendered for the request.

    Args:
        case: The stored case document.
        rtf: Whether the caller asked to keep the HTML markup.

    Returns:
        A copy of the case with rich-text fields stripped when ``rtf`` is False.
    """
    rendered = json.loads(json.dumps(case))
    if rtf:
        return rendered
    for key in _RICH_CASE_KEYS:
        if key in rendered:
            rendered[key] = _strip_html(rendered[key])
    for step in rendered.get("steps") or []:
        for key in _RICH_STEP_KEYS:
            if key in step:
                step[key] = _strip_html(step[key])
    for custom in rendered.get("customFields") or []:
        custom["value"] = _strip_html(custom.get("value"))
    return rendered


_LOOKUP_CONFIG_KEYS = {
    "status": "caseStatuses",
    "priority": "casePriorities",
    "type": "caseTypes",
    "scriptType": "caseScriptTypes",
    "automationStatus": "caseAutomationStatuses",
}


def _resolve_lookups(document: dict[str, Any]) -> dict[str, Any]:
    """Fill in the names of lookup references, as the real API does.

    Clients send ``{"ID": 12}``; the stored document carries the resolved name
    alongside it, so a read-back shows ``{"ID": 12, "name": "Low"}``.

    Args:
        document: A case document about to be stored.

    Returns:
        The same document with lookup references expanded in place.
    """
    for attribute, config_key in _LOOKUP_CONFIG_KEYS.items():
        reference = document.get(attribute)
        if not isinstance(reference, dict) or reference.get("ID") is None:
            continue
        match = next(
            (
                option
                for option in PROJECT_CONFIG[config_key]
                if option["ID"] == reference["ID"]
            ),
            None,
        )
        if match is None:
            raise FakeAIOError(
                400, f"'{attribute}' {reference['ID']} is not valid for this project"
            )
        document[attribute] = {"ID": match["ID"], "name": match["name"]}

    folder = document.get("folder")
    if isinstance(folder, dict) and folder.get("ID") is not None:
        document["folder"] = {"ID": folder["ID"], "name": folder.get("name")}
    return document


def _flatten(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flatten a folder tree.

    Args:
        nodes: The root folders.

    Returns:
        Every folder in the tree, parents before children.
    """
    out: list[dict[str, Any]] = []
    for node in nodes:
        out.append(node)
        out.extend(_flatten(node.get("children") or []))
    return out


class FakeAIOError(Exception):
    """Raised inside a handler to return a specific HTTP status."""

    def __init__(self, status: int, message: str) -> None:
        """Initialize the error.

        Args:
            status: HTTP status to return.
            message: Body text to return.
        """
        super().__init__(message)
        self.status = status
        self.message = message


class FakeAIO:
    """A running fake AIO Tests deployment.

    Attributes:
        url: Base URL to point ``AIO_URL`` at.
        requests: Every request the fake received, in order.
    """

    def __init__(self, *, api_path: str = "/rest/aio-tcms-api/1.0") -> None:
        """Start the fake on an ephemeral port.

        Args:
            api_path: Path prefix the API is served under.
        """
        self.api_path = api_path.rstrip("/")
        self.state = _fresh_state()
        self.requests: list[dict[str, Any]] = []
        self.expected_token = "fake-token"
        self.expected_scheme = "Bearer"

        fake = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: Any) -> None:  # noqa: D102
                return

            def _run(self, method: str) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    body = json.loads(raw) if raw else None
                except ValueError:
                    body = None
                parsed = urlparse(self.path)
                params = {
                    key: value[0] for key, value in parse_qs(parsed.query).items()
                }
                record = {
                    "method": method,
                    "path": parsed.path,
                    "params": params,
                    "body": body,
                    "headers": dict(self.headers),
                }
                fake.requests.append(record)
                try:
                    status, payload = fake.dispatch(
                        method, parsed.path, params, body, dict(self.headers)
                    )
                except FakeAIOError as exc:
                    status, payload = exc.status, exc.message
                encoded = (
                    payload.encode()
                    if isinstance(payload, str)
                    else json.dumps(payload).encode()
                )
                content_type = (
                    "text/plain;charset=UTF-8"
                    if isinstance(payload, str)
                    else "application/json"
                )
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def do_GET(self) -> None:  # noqa: N802, D102
                self._run("GET")

            def do_POST(self) -> None:  # noqa: N802, D102
                self._run("POST")

            def do_PUT(self) -> None:  # noqa: N802, D102
                self._run("PUT")

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.httpd.server_address[:2]
        self.url = f"http://{host}:{port}{self.api_path}"

    def stop(self) -> None:
        """Shut the fake down."""
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)

    def reset(self) -> None:
        """Restore the seeded state and clear the recorded requests."""
        self.state = _fresh_state()
        self.requests.clear()

    def dispatch(
        self,
        method: str,
        path: str,
        params: dict[str, str],
        body: Any,
        headers: dict[str, str],
    ) -> tuple[int, Any]:
        """Route one request to its handler.

        Args:
            method: HTTP method.
            path: Request path.
            params: Query parameters.
            body: Decoded JSON body, if any.
            headers: Request headers.

        Returns:
            A ``(status, payload)`` pair.

        Raises:
            FakeAIOError: If authentication fails or no route matches.
        """
        auth = headers.get("Authorization", "")
        if auth != f"{self.expected_scheme} {self.expected_token}":
            raise FakeAIOError(401, "Authorization header is missing or invalid value.")
        if not path.startswith(f"{self.api_path}/project/"):
            raise FakeAIOError(404, "Not found")

        rest = path[len(f"{self.api_path}/project/") :]
        segments = [unquote(segment) for segment in rest.split("/") if segment]
        if not segments:
            raise FakeAIOError(404, "Not found")

        project, tail = segments[0], segments[1:]
        if project != KNOWN_PROJECT and project != str(KNOWN_PROJECT_ID):
            raise FakeAIOError(404, f"Project '{project}' does not have AIO Tests")

        rtf = str(params.get("needDataInRTF", "")).lower() == "true"

        if tail == ["config"]:
            return 200, PROJECT_CONFIG
        if tail == ["tag"]:
            return self._tags(method, body)
        if len(tail) == 3 and tail[1] == "folder" and tail[2] == "hierarchy":
            return self._create_folder(method, tail[0], body)
        if len(tail) == 2 and tail[1] == "folder":
            return self._folders(method, tail[0])
        if tail == ["testcase"]:
            return self._list_or_create_case(method, params, body, rtf=rtf)
        if tail == ["testcase", "search"]:
            return self._search_cases(method, params, body, rtf=rtf)
        if len(tail) == 3 and tail[0] == "testcase" and tail[2] == "detail":
            return self._case_detail(method, tail[1], params, body, rtf=rtf)
        raise FakeAIOError(404, f"No route for {method} {path}")

    def _tags(self, method: str, body: Any) -> tuple[int, Any]:
        """Handle the tag collection.

        Args:
            method: HTTP method.
            body: Decoded request body.

        Returns:
            A ``(status, payload)`` pair.

        Raises:
            FakeAIOError: If the method is not supported.
        """
        if method == "GET":
            return 200, self.state.tags
        if method == "POST":
            created = []
            for entry in body or []:
                name = entry.get("name")
                existing = next(
                    (tag for tag in self.state.tags if tag["name"] == name), None
                )
                if existing:
                    created.append(existing)
                    continue
                tag = {"ID": self.state.next_tag_id, "name": name}
                self.state.next_tag_id += 1
                self.state.tags.append(tag)
                created.append(tag)
            return 200, created
        raise FakeAIOError(405, "Method not allowed")

    def _folders(self, method: str, folder_type: str) -> tuple[int, Any]:
        """Return a folder tree.

        Args:
            method: HTTP method.
            folder_type: Folder tree to read.

        Returns:
            A ``(status, payload)`` pair.

        Raises:
            FakeAIOError: If the method or folder type is not supported.
        """
        if method != "GET":
            raise FakeAIOError(405, "Method not allowed")
        if folder_type not in self.state.folders:
            raise FakeAIOError(404, f"Unknown folder type '{folder_type}'")
        return 200, self.state.folders[folder_type]

    def _create_folder(
        self, method: str, folder_type: str, body: Any
    ) -> tuple[int, Any]:
        """Get-or-create a folder hierarchy and return the leaf folder.

        Args:
            method: HTTP method.
            folder_type: Folder tree to create in.
            body: ``{"folderHierarchy": [...], "baseFolderId": n}``.

        Returns:
            A ``(status, payload)`` pair carrying the leaf folder.

        Raises:
            FakeAIOError: If the method, folder type or body is not supported.
        """
        if method != "PUT":
            raise FakeAIOError(405, "Method not allowed")
        if folder_type not in self.state.folders:
            raise FakeAIOError(404, f"Unknown folder type '{folder_type}'")
        names = (body or {}).get("folderHierarchy") or []
        if not names:
            raise FakeAIOError(400, "folderHierarchy is required")

        base_id = (body or {}).get("baseFolderId")
        siblings = self.state.folders[folder_type]
        parent: dict[str, Any] | None = None
        if base_id is not None:
            parent = next(
                (
                    node
                    for node in _flatten(self.state.folders[folder_type])
                    if node["ID"] == base_id
                ),
                None,
            )
            if parent is None:
                raise FakeAIOError(404, f"Base folder {base_id} not found")
            siblings = parent.setdefault("children", [])

        node: dict[str, Any] = {}
        for name in names:
            match = next(
                (item for item in siblings if item["name"].lower() == name.lower()),
                None,
            )
            if match is None:
                match = {"ID": self.state.next_folder_id, "name": name, "children": []}
                self.state.next_folder_id += 1
                siblings.append(match)
            node = match
            siblings = match.setdefault("children", [])
        # The real API answers with the leaf folder only; parentID is omitted.
        return 200, {"ID": node["ID"], "name": node["name"]}

    def _page(
        self, cases: list[dict[str, Any]], params: dict[str, str], *, rtf: bool
    ) -> dict[str, Any]:
        """Build a paginated case list response.

        Args:
            cases: The matching cases, unpaginated.
            params: Query parameters carrying ``startAt`` and ``maxResults``.
            rtf: Whether to keep HTML markup.

        Returns:
            The API page payload.
        """
        start = int(params.get("startAt", 0) or 0)
        size = int(params.get("maxResults", 100) or 100)
        window = cases[start : start + size]
        return {
            "startAt": start,
            "maxResults": size,
            "totalCount": len(cases),
            "isLast": start + len(window) >= len(cases),
            "items": [_render_case(case, rtf=rtf) for case in window],
        }

    def _list_or_create_case(
        self, method: str, params: dict[str, str], body: Any, *, rtf: bool
    ) -> tuple[int, Any]:
        """List every case, or create one.

        Args:
            method: HTTP method.
            params: Query parameters.
            body: Decoded request body.
            rtf: Whether to keep HTML markup.

        Returns:
            A ``(status, payload)`` pair.

        Raises:
            FakeAIOError: If the method is not supported or the body is invalid.
        """
        if method == "GET":
            ordered = [self.state.cases[key] for key in sorted(self.state.cases)]
            return 200, self._page(ordered, params, rtf=rtf)
        if method != "POST":
            raise FakeAIOError(405, "Method not allowed")

        payload = self._name_folders(_resolve_lookups(dict(body or {})))
        if not payload.get("title"):
            raise FakeAIOError(400, "title is required")
        case = _seed_case(self.state, **payload)
        return 200, _render_case(case, rtf=rtf)

    def _search_cases(
        self, method: str, params: dict[str, str], body: Any, *, rtf: bool
    ) -> tuple[int, Any]:
        """Apply the search criteria the MCP server builds.

        Args:
            method: HTTP method.
            params: Query parameters.
            body: The search criteria document.
            rtf: Whether to keep HTML markup.

        Returns:
            A ``(status, payload)`` pair.

        Raises:
            FakeAIOError: If the method is not supported.
        """
        if method != "POST":
            raise FakeAIOError(405, "Method not allowed")
        criteria = body or {}
        matches = [self.state.cases[key] for key in sorted(self.state.cases)]

        title = criteria.get("title")
        if title:
            needle = str(title.get("value", "")).lower()
            if title.get("comparisonType") == "EXACT_MATCH":
                matches = [
                    c for c in matches if (c.get("title") or "").lower() == needle
                ]
            else:
                matches = [
                    c for c in matches if needle in (c.get("title") or "").lower()
                ]

        keys = criteria.get("key")
        if keys:
            wanted = {str(value) for value in keys.get("list") or []}
            matches = [c for c in matches if c.get("key") in wanted]

        for field_name, attribute in (
            ("statusID", "status"),
            ("priorityID", "priority"),
            ("typeID", "type"),
            ("automationStatusID", "automationStatus"),
        ):
            clause = criteria.get(field_name)
            if not clause:
                continue
            wanted_ids = {int(value) for value in clause.get("list") or []}
            matches = [
                case
                for case in matches
                if (case.get(attribute) or {}).get("ID") in wanted_ids
            ]

        folder_clause = criteria.get("folderID")
        if folder_clause:
            wanted_ids = {int(value) for value in folder_clause.get("list") or []}
            matches = [
                case
                for case in matches
                if (case.get("folder") or {}).get("ID") in wanted_ids
            ]

        tag_clause = criteria.get("tag")
        if tag_clause:
            wanted_names = {
                str(value).lower() for value in tag_clause.get("list") or []
            }
            matches = [
                case
                for case in matches
                if any(
                    str((entry.get("tag") or {}).get("name", "")).lower()
                    in wanted_names
                    for entry in case.get("tags") or []
                )
            ]

        requirement_clause = criteria.get("requirementID")
        if requirement_clause:
            wanted = {str(value) for value in requirement_clause.get("list") or []}
            matches = [
                case
                for case in matches
                if wanted
                & {str(value) for value in case.get("jiraRequirementIDs") or []}
            ]

        automation_key = criteria.get("automationKey")
        if automation_key:
            needle = str(automation_key.get("value", "")).lower()
            matches = [
                case
                for case in matches
                if needle in str(case.get("automationKey") or "").lower()
            ]

        archived = criteria.get("isArchived")
        if archived is not None:
            wanted_flag = bool(archived.get("value"))
            matches = [
                case for case in matches if bool(case.get("isArchived")) is wanted_flag
            ]

        return 200, self._page(matches, params, rtf=rtf)

    def _name_folders(self, document: dict[str, Any]) -> dict[str, Any]:
        """Attach the folder name to a folder reference given only by ID.

        Args:
            document: A case document about to be stored.

        Returns:
            The same document with the folder reference named in place.

        Raises:
            FakeAIOError: If the folder does not exist.
        """
        folder = document.get("folder")
        if not isinstance(folder, dict) or folder.get("ID") is None:
            return document
        match = next(
            (
                node
                for node in _flatten(self.state.folders["testcase"])
                if node["ID"] == folder["ID"]
            ),
            None,
        )
        if match is None:
            raise FakeAIOError(400, f"Folder {folder['ID']} does not exist")
        document["folder"] = {"ID": match["ID"], "name": match["name"]}
        return document

    def _find_case(self, identifier: str) -> dict[str, Any]:
        """Look a case up by key or numeric ID.

        Args:
            identifier: Case key or numeric case ID.

        Returns:
            The stored case document.

        Raises:
            FakeAIOError: If no case matches.
        """
        if identifier.isdigit():
            case = self.state.cases.get(int(identifier))
            if case:
                return case
        for case in self.state.cases.values():
            if case.get("key") == identifier:
                return case
        raise FakeAIOError(404, f"Case '{identifier}' not found")

    def _case_detail(
        self,
        method: str,
        identifier: str,
        params: dict[str, str],
        body: Any,
        *,
        rtf: bool,
    ) -> tuple[int, Any]:
        """Read or replace a single case.

        Args:
            method: HTTP method.
            identifier: Case key or numeric ID.
            params: Query parameters.
            body: The replacement case document on ``PUT``.
            rtf: Whether to keep HTML markup.

        Returns:
            A ``(status, payload)`` pair.

        Raises:
            FakeAIOError: If the method is not supported.
        """
        case = self._find_case(identifier)
        if method == "GET":
            return 200, _render_case(case, rtf=rtf)
        if method != "PUT":
            raise FakeAIOError(405, "Method not allowed")

        replacement = dict(body or {})
        # Identity and audit attributes are owned by the server: whatever the
        # client echoes back is ignored rather than applied.
        for server_owned in ("ID", "key", "version", "versions", "createdDate"):
            replacement.pop(server_owned, None)
        if str(params.get("createNewVersion", "")).lower() == "true":
            case["version"] = int(case.get("version") or 1) + 1
            case.setdefault("versions", []).append(
                {"version": case["version"], "ID": case["ID"]}
            )
        case.update(self._name_folders(_resolve_lookups(replacement)))
        case["updatedDate"] = "2026-09-12T10:00:00.000+0000"
        return 200, _render_case(case, rtf=rtf)
