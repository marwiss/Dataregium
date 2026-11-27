# This file was generated with the assistance of an AI coding tool.
"""IfcMCP's tools behind the MCP endpoint on PostgreSQL (ADR 0031, F60, F204 a, F205 d): an editor's AI reads a
version, opens a working copy, renames a wall with an allowed API function, is refused one that would read a file,
and proposes the copy as a whole file, which the server tries on the version; a viewer reads but never opens a
copy, and a closed copy is gone."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from opencde.main import create_app
from opencde.settings import Settings
from tests.integration.conftest import bim_settings
from tests.integration.conftest_accounts import World, build_world, login_as
from tests.integration.test_ifc_proposal import tried
from tests.integration.test_ifc_validation import MODEL, WALLS, checked, upload
from tests.integration.test_mcp_change_sets import _bound, _ok, _tool
from tests.integration.test_web_signin import web_sign_in
from tests.mcp_support import META, MODERN

pytestmark = [
    pytest.mark.integration,
    pytest.mark.security,
    pytest.mark.parametrize("store_kind", ["postgres"], indirect=True),
]
ifcopenshell = pytest.importorskip("ifcopenshell", reason="the ifc-db extra is not installed")


@pytest.fixture
def ifcmcp_client(settings: Settings, clean_database: None) -> Iterator[tuple[TestClient, World]]:
    on = bim_settings(settings, mcp_enabled=True)
    world = build_world(on)
    with TestClient(create_app(on, startup_wait_seconds=30)) as client:
        state = client.app.state  # type: ignore[attr-defined]
        client.portal.call(  # type: ignore[union-attr]
            state.postgres.execute, "UPDATE projects SET ai_policy = 'cloud' WHERE project_id = $1", world.project_id
        )  # a team server serves the cloud projects to AI tools (F29 a)
        yield client, world


def _listed(client: TestClient, session: Any) -> list[str]:
    message = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"_meta": META}}
    sent = {**session.headers, "MCP-Protocol-Version": MODERN, "Mcp-Method": "tools/list"}
    answer = client.post("/mcp", json=message, headers=sent)
    assert answer.status_code == 200, answer.text
    return [tool["name"] for tool in answer.json()["result"]["tools"]]


def test_an_ai_reads_edits_a_copy_and_proposes_it_as_a_whole_file(ifcmcp_client: tuple[TestClient, World]) -> None:
    client, world = ifcmcp_client
    editor_web = login_as(client, world, "editor")
    web_sign_in(client, world, "editor")
    version = upload(client, world, editor_web, "three_walls_gtin.ifc", MODEL.read_bytes())
    assert checked(client, editor_web, version)["ifc_validation_status"] == ["passed"]
    document_id = version["document_id"]
    editor = _bound(client, world, "editor", "mcp mcp:propose")
    viewer = _bound(client, world, "viewer", "mcp mcp:propose")

    names = _listed(client, editor)
    assert {"ifc_summary", "ifc_select", "ifc_open", "ifc_edit", "ifc_propose", "ifc_close"} <= set(names)
    assert not {"ifc_load", "ifc_save", "ifc_new", "ifc_plot", "ifc_render", "ifc_clash"} & set(names)

    on_version = {"copy_id": None, "document_id": document_id, "version_index": None}
    summary = _ok(_tool(client, editor, "ifc_summary", on_version))["result"]
    assert summary["schema"] == "IFC2X3" and summary["total_entities"] > 0, summary
    walls = _ok(_tool(client, viewer, "ifc_select", {**on_version, "query": "IfcWall"}))["result"]
    assert len(walls) == 3, "a viewer reads"
    refused = _tool(client, viewer, "ifc_open", {"document_id": document_id, "version_index": None})
    assert refused["isError"] is True, "only an editor or a manager opens a copy"

    opened = _ok(_tool(client, editor, "ifc_open", {"document_id": document_id, "version_index": None}))
    copy_id = opened["copy_id"]
    assert opened["version_index"] == 1 and opened["expires_at"].endswith("Z")
    wall = ifcopenshell.open(str(MODEL)).by_guid(WALLS[0]).id()
    rename = {"product": wall, "attributes": {"Name": "AI wall"}}
    edited = _ok(
        _tool(
            client,
            editor,
            "ifc_edit",
            {"copy_id": copy_id, "function_path": "attribute.edit_attributes", "params": json.dumps(rename)},
        )
    )
    assert "error" not in edited, edited
    outside = _ok(
        _tool(
            client,
            editor,
            "ifc_edit",
            {"copy_id": copy_id, "function_path": "project.append_asset", "params": json.dumps({"library": "/etc/x"})},
        )
    )
    assert outside.get("refused") is True and "/etc" not in json.dumps(outside), "a function that reads a file"
    listed = _ok(_tool(client, editor, "ifc_list", {"module": None}))["result"]
    assert "project" not in {entry["module"] for entry in listed}, "a module with nothing allowed is not listed"
    info = _ok(_tool(client, editor, "ifc_info", {"copy_id": copy_id, "document_id": None, "version_index": None,
                                                  "element_id": wall}))["result"]  # fmt: skip
    assert "AI wall" in json.dumps(info), "the copy holds the edit"
    still = _ok(_tool(client, editor, "ifc_info", {**on_version, "element_id": wall}))["result"]
    assert "AI wall" not in json.dumps(still), "the version itself is as it was"
    assert _tool(client, viewer, "ifc_info", {"copy_id": copy_id, "document_id": None, "version_index": None,
                                              "element_id": wall})["isError"] is True, "a copy is its user's alone"  # fmt: skip

    proposed = _ok(
        _tool(
            client,
            editor,
            "ifc_propose",
            {"copy_id": copy_id, "title": "Rename the wall", "description": None, "ai_model": "claude-opus-5-5"},
        )
    )
    proposal = proposed["proposal_url"].removeprefix("http://testserver")
    report = tried(client, editor_web, proposal)
    assert report["state"] == "clean" and report["kind"] == "file", report
    given = client.get(proposal + "/file", headers=editor_web.headers)
    assert b"'AI wall'" in given.content
    topic = client.get(proposal.removesuffix("/ifc-proposal"), headers=editor_web.headers).json()
    assert topic["bim_snippet"]["snippet_type"] == "DataregiumIfcFile" and topic["bim_snippet"]["is_external"] is False
    snippet = client.get(proposal.removesuffix("/ifc-proposal") + "/snippet", headers=editor_web.headers)
    assert snippet.status_code == 200 and b"'AI wall'" in snippet.content, "the file as the AI left it"

    assert _ok(_tool(client, editor, "ifc_close", {"copy_id": copy_id})) == {"ok": True}
    gone = _tool(client, editor, "ifc_info", {"copy_id": copy_id, "document_id": None, "version_index": None,
                                              "element_id": wall})  # fmt: skip
    assert gone["isError"] is True, "a closed copy is gone"
