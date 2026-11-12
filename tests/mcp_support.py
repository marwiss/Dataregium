# This file was generated with the assistance of an AI coding tool.
"""What the tests of the MCP endpoint share: the application with a token store and a graph that stand in for the
database, and requests as the two eras of the protocol write them.

The token store knows one token, bound to the endpoint; the graph answers the memberships it is given and keeps
every statement with its parameters, so that a test sees what reached the database, and it holds the stand-ins of
the documents' and the jobs' stores that ``list_models`` reads the models it is given from, which keep what they
were asked. The database's own side is in ``tests/integration/test_mcp_list_models.py``.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from opencde import audit
from opencde.auth.dependencies import get_tokens
from opencde.deps import get_graph
from opencde.main import create_app
from opencde.mcp import ifc_models
from opencde.store.documents.versions import VersionRecord
from opencde.store.oauth2.tokens import Principal
from tests.conftest import test_settings as make_settings

MCP = "http://testserver/mcp"
MODERN = "2026-07-28"
BEARER = {"Authorization": "Bearer a-token-the-store-knows"}
META = {
    "io.modelcontextprotocol/protocolVersion": MODERN,
    "io.modelcontextprotocol/clientCapabilities": {},
    "io.modelcontextprotocol/clientInfo": {"name": "test-client", "version": "1.0"},
}
CLOUD = "11111111-1111-4111-8111-111111111111"
LOCAL = "22222222-2222-4222-8222-222222222222"
DOCUMENT = "33333333-3333-4333-8333-333333333333"


def principal(
    scopes: tuple[str, ...] = ("mcp",), resource: str | None = MCP, hashed: str = "hash-of-the-token"
) -> Principal:
    return Principal(
        user_id="u-anna",
        username="anna",
        display_name="Anna",
        is_admin=False,
        client_id="ai-client",
        scopes=scopes,
        token_hash=hashed,
        auth_time="2999-01-01T00:00:00Z",
        resource=resource,
    )


class OneToken:
    """A token store that knows one principal, whatever token is presented, or none."""

    def __init__(self, found: Principal | None) -> None:
        self.found = found

    async def principal(self, access_token: str) -> Principal | None:
        return self.found


def project(project_id: str, name: str, ai_policy: str | None) -> dict[str, Any]:
    found = {"project_id": project_id, "name": name, "created_at": "2026-11-01T00:00:00.000Z"}
    return found | ({"ai_policy": ai_policy} if ai_policy else {})


def model_row(project_id: str = CLOUD, index: int = 1, **values: Any) -> dict[str, Any]:
    """A row of ``list_models``: the latest version of an IFC document, and the state and schema of its model."""
    return {
        "project_id": project_id,
        "project_name": "Cloud project",
        "role": "editor",
        "ai_policy": "cloud",
        "document_id": DOCUMENT,
        "title": f"Model {index}",
        "file_name": f"model-{index}.ifc",
        "version_index": 3,
        "model_state": "done",
        "schema": "IFC4",
    } | values


class ModelHeads:
    """The documents' store as ``list_models`` asks it: the latest version of each IFC document of the projects asked
    for, from the graph's ``models``, as the statement would find them."""

    def __init__(self, graph: FakeGraph) -> None:
        self.graph = graph

    async def member_heads(
        self, *, user_id: str, project_ids: list[str], suffixes: list[str], limit: int
    ) -> list[VersionRecord]:
        self.graph.asked_models.append(
            {"user_id": user_id, "project_ids": list(project_ids), "suffixes": list(suffixes), "limit": limit}
        )
        rows = [row for row in self.graph.models if row["project_id"] in project_ids]
        return [_head(index, row) for index, row in enumerate(rows[:limit])]


def _head(index: int, row: dict[str, Any]) -> VersionRecord:
    return VersionRecord(
        document_id=row["document_id"],
        project_id=row["project_id"],
        project_name=row["project_name"],
        version_id=f"version-{index}",
        version_index=int(row["version_index"]),
        version_number=str(row["version_index"]),
        creation_date="2026-11-01T00:00:00.000Z",
        title=row["title"],
        file_name=row["file_name"],
        size_in_bytes=1,
        sha256="0" * 64,
        storage_key=f"documents/version-{index}",
        author="anna",
        metadata=[],
    )


class ModelConversions:
    """The jobs' store as ``list_models`` asks it: the newest conversion of each version, from the state and schema of
    the graph's ``models``; a model without a state has none."""

    def __init__(self, graph: FakeGraph) -> None:
        self.graph = graph

    async def for_member_versions(self, *, user_id: str, versions: list[tuple[str, str, int]]) -> list[dict[str, Any]]:
        found: dict[tuple[str, str, int], dict[str, Any]] = {}
        for row in self.graph.models:
            key = (row["project_id"], row["document_id"], int(row["version_index"]))
            if key in versions and row["model_state"] is not None and key not in found:
                where = dict(zip(("project_id", "document_id", "version_index"), key, strict=True))
                found[key] = where | {"state": row["model_state"], "schema": row["schema"]}
        return list(found.values())


class FakeGraph:
    """The server's own graph as far as the endpoint reads and writes it, with the stand-ins of the stores that
    ``list_models`` reads."""

    def __init__(self, memberships: list[tuple[dict[str, Any], str]] | None = None, models: list | None = None):
        self.memberships = (
            memberships if memberships is not None else [(project(CLOUD, "Cloud project", "cloud"), "editor")]
        )
        self.models = models if models is not None else [model_row()]
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.asked_models: list[dict[str, Any]] = []
        self.heads = ModelHeads(self)
        self.model_conversions = ModelConversions(self)

    async def run(self, query: str, **params: Any) -> list[dict[str, Any]]:
        self.calls.append((query, params))
        if "RETURN properties(p) AS project, m.role AS role" in query:
            return [{"project": found, "role": role} for found, role in self.memberships]
        return []

    def events(self) -> list[dict[str, Any]]:
        """The audit events written, with their details read back."""
        return [
            {**params, "details": json.loads(params["details"])}
            for query, params in self.calls
            if query == audit.CREATE
        ]

    def model_queries(self) -> list[dict[str, Any]]:
        """What the documents' store was asked for the models: the user, the projects, the suffixes and the limit."""
        return list(self.asked_models)


BOUND = principal()


def stand_in_stores(patch: pytest.MonkeyPatch) -> None:
    """``list_models`` reads a ``FakeGraph``'s models from its stand-ins of the documents' and the jobs' stores; any
    other graph gets the stores it would get."""
    documents, conversions = ifc_models.document_store, ifc_models.conversion_store
    patch.setattr(
        ifc_models,
        "document_store",
        lambda store, postgres: store.heads if isinstance(store, FakeGraph) else documents(store, postgres),
    )
    patch.setattr(
        ifc_models,
        "conversion_store",
        lambda store, postgres: (
            store.model_conversions if isinstance(store, FakeGraph) else conversions(store, postgres)
        ),
    )


@contextmanager
def endpoint(
    tmp_path: Path,
    graph: FakeGraph | None = None,
    found: Principal | None = BOUND,
    *,
    base_url: str = "http://testserver",
    **overrides: Any,
) -> Iterator[TestClient]:
    """The application with the endpoint on, the token store and the graph standing in for the database, and the
    graph's stand-ins for the stores ``list_models`` reads; ``found`` None is a token the store does not know."""
    settings = make_settings(tmp_path, **{"mcp_enabled": True, **overrides})
    app = create_app(settings, startup_wait_seconds=0)
    app.dependency_overrides[get_tokens] = lambda: OneToken(found)
    app.dependency_overrides[get_graph] = lambda: graph if graph is not None else FakeGraph()
    with pytest.MonkeyPatch.context() as patch:
        stand_in_stores(patch)
        with TestClient(app, base_url=base_url) as client:
            yield client


def headers(method: str, name: str | None = None, version: str = MODERN, **extra: str) -> dict[str, str]:
    """The headers a client of 2026-07-28 sends with a request."""
    sent = {**BEARER, "MCP-Protocol-Version": version, "Mcp-Method": method}
    if name is not None:
        sent["Mcp-Name"] = name
    return sent | extra


def rpc(
    client: TestClient,
    method: str,
    params: dict[str, Any] | None = None,
    *,
    request_id: int | str = 1,
    meta: dict[str, Any] | None = None,
    sent: dict[str, str] | None = None,
) -> Any:
    """A request of 2026-07-28, with its ``_meta`` and its headers; either can be given to make it wrong."""
    body_params = {**(params or {}), "_meta": META if meta is None else meta}
    name = body_params.get("name") if method == "tools/call" else None
    message = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": body_params}
    return client.post("/mcp", json=message, headers=headers(method, name) if sent is None else sent)


def call(client: TestClient, name: str, arguments: dict[str, Any] | None, **kwargs: Any) -> Any:
    return rpc(client, "tools/call", {"name": name, "arguments": arguments}, **kwargs)


def legacy(
    client: TestClient, method: str, params: dict[str, Any] | None = None, *, version: str | None = "2025-11-25"
) -> Any:
    """A request of a handshake revision: its revision in the header after ``initialize``, no ``_meta``."""
    sent = {**BEARER} | ({"MCP-Protocol-Version": version} if version else {})
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": 7, "method": method}
    if params is not None:
        message["params"] = params
    return client.post("/mcp", json=message, headers=sent)
