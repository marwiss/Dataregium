# This file was generated with the assistance of an AI coding tool.
"""A change set tried on its base version when it is uploaded as a topic's snippet (ADR 0021)."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from opencde.documents.changes import SCHEMA_PATH, SNIPPET_TYPE
from opencde.main import create_app
from opencde.settings import Settings
from tests.integration.conftest import bim_settings
from tests.integration.conftest_accounts import World, build_world, login_as
from tests.integration.helpers import Session
from tests.integration.test_bcf_links_documents import make_topic
from tests.integration.test_ifc_validation import MODEL, WALLS, checked, upload
from tests.integration.test_web_signin import web_sign_in

pytestmark = [pytest.mark.integration, pytest.mark.both_stores]
pytest.importorskip("ifcopenshell", reason="the ifc-db extra is not installed")


@pytest.fixture
def ifc_client(settings: Settings, clean_database: None, tmp_path: Any) -> Iterator[tuple[TestClient, World]]:
    settings = bim_settings(settings)
    world = build_world(settings)
    with TestClient(create_app(settings, startup_wait_seconds=30)) as client:
        yield client, world


def tried(client: TestClient, session: Session, url: str) -> dict[str, Any]:
    deadline = time.monotonic() + 300
    while True:
        response = client.get(url, headers=session.headers)
        assert response.status_code == 200, response.text
        report = response.json()
        if report["state"] not in ("queued", "running"):
            return report
        assert time.monotonic() < deadline, "the trial did not finish in time"
        time.sleep(0.5)


def change_set(document_id: str, *changes: dict[str, Any]) -> bytes:
    return json.dumps(
        {
            "schema": f"http://testserver{SCHEMA_PATH}",
            "base": {"document_id": document_id, "version_index": 1},
            "provenance": {"user": "editor", "ai_model": "claude-opus-5-5", "tool_call": "rename"},
            "changes": list(changes),
        }
    ).encode("utf-8")


def test_a_change_set_is_tried_on_its_base_and_judged_against_its_findings(
    ifc_client: tuple[TestClient, World],
) -> None:
    client, world = ifc_client
    editor = login_as(client, world, "editor")
    web_sign_in(client, world, "editor")
    viewer = login_as(client, world, "viewer")
    outsider = login_as(client, world, "outsider")
    assert client.get("/.well-known/dataregium").json()["extensions"]["ifc_changes"]["tried"] is True
    version = upload(client, world, editor, "three_walls_gtin.ifc", MODEL.read_bytes())
    assert checked(client, editor, version)["ifc_validation_status"] == ["passed"]
    document_id = version["document_id"]
    topics = f"/bcf/3.0/projects/{world.project_id}/topics"
    topic = make_topic(
        client,
        world,
        editor,
        bim_snippet={
            "snippet_type": SNIPPET_TYPE,
            "is_external": False,
            "reference": "changes.json",
            "reference_schema": f"http://testserver{SCHEMA_PATH}",
        },
    )
    snippet = f"{topics}/{topic['guid']}/snippet"
    proposal = f"{topics}/{topic['guid']}/ifc-proposal"
    assert client.get(proposal, headers=viewer.headers).status_code == 404, "nothing uploaded yet"

    # A clean change: one wall renamed, nothing the base lacks.
    rename = {"guid": WALLS[0], "attribute": "Name", "op": "set", "old": "YVT-A", "new": "YVT-A2"}
    assert client.put(snippet, content=change_set(document_id, rename), headers=editor.headers).status_code == 200
    report = tried(client, viewer, proposal)
    assert report["state"] == "clean", report
    assert report["applied"] == 1 and report["change_count"] == 1 and report["issue_count"] == 0
    assert report["base"] == {"document_id": document_id, "version_index": 1}
    assert report["ruleset"].startswith("IfcOpenShell ") and report["checks"][0]["name"] == "schema"
    assert client.get(proposal, headers=outsider.headers).status_code == 404
    assert client.get(proposal).status_code == 401
    storage = client.app.state.storage  # type: ignore[attr-defined]
    kept = [key for key in storage.iter_keys("proposals")]
    assert len(kept) == 1, "the applied file is kept for the approval"

    # A change set that breaks the schema, and one change that cannot be applied: both are issues.
    duplicate = {"guid": WALLS[0], "attribute": "GlobalId", "op": "set", "new": WALLS[1]}
    stale = {"guid": WALLS[1], "attribute": "Name", "op": "set", "old": "not its name", "new": "x"}
    assert (
        client.put(snippet, content=change_set(document_id, duplicate, stale), headers=editor.headers).status_code
        == 200
    )
    report = tried(client, viewer, proposal)
    assert report["state"] == "issues", report
    assert report["applied"] == 1 and report["change_count"] == 2 and report["error_count"] >= 2
    kinds = {issue["kind"] for issue in report["issues"]}
    assert "proposal" in kinds and kinds - {"proposal"}, "a change that did not apply, and a new finding"
    stale_issue = next(issue for issue in report["issues"] if issue["kind"] == "proposal")
    assert stale_issue["message"] == "change 1: Name no longer holds the old value"
    assert any(issue.get("global_id") == WALLS[1] and "IfcRoot.UR1" in issue["message"] for issue in report["issues"])
    assert [key for key in storage.iter_keys("proposals")] != kept, "a new snippet replaces the trial and its file"
    assert len(list(storage.iter_keys("proposals"))) == 1

    # A base that is not in the project cannot be tried.
    elsewhere = change_set("11111111-2222-4333-8444-555555555555", rename)
    assert client.put(snippet, content=elsewhere, headers=editor.headers).status_code == 200
    report = tried(client, viewer, proposal)
    assert report["state"] == "failed" and "not in this project" in report["error"]

    # The trial goes with the topic, file and all.
    assert client.delete(f"{topics}/{topic['guid']}", headers=editor.headers).status_code == 200
    assert list(storage.iter_keys("proposals")) == []


def test_a_manager_approves_a_clean_trial_and_it_becomes_the_next_version(ifc_client: tuple[TestClient, World]) -> None:
    client, world = ifc_client
    editor = login_as(client, world, "editor")
    web_sign_in(client, world, "editor")
    manager = login_as(client, world, "manager")
    version = upload(client, world, editor, "three_walls_gtin.ifc", MODEL.read_bytes())
    assert checked(client, editor, version)["ifc_validation_status"] == ["passed"]
    document_id = version["document_id"]
    topics = f"/bcf/3.0/projects/{world.project_id}/topics"
    snippet_fields = {
        "snippet_type": SNIPPET_TYPE,
        "is_external": False,
        "reference": "changes.json",
        "reference_schema": f"http://testserver{SCHEMA_PATH}",
    }
    topic = make_topic(client, world, editor, bim_snippet=snippet_fields)
    snippet = f"{topics}/{topic['guid']}/snippet"
    proposal = f"{topics}/{topic['guid']}/ifc-proposal"
    approve = proposal + "/approve"
    assert client.post(approve, headers=manager.headers).status_code == 404, "nothing to approve yet"
    rename = {"guid": WALLS[0], "attribute": "Name", "op": "set", "old": "YVT-A", "new": "YVT-A2"}
    assert client.put(snippet, content=change_set(document_id, rename), headers=editor.headers).status_code == 200
    assert tried(client, editor, proposal)["state"] == "clean"

    assert client.post(approve, headers=editor.headers).status_code == 403, "an editor proposes, a manager approves"
    approved = client.post(approve, headers=manager.headers)
    assert approved.status_code == 201, approved.text
    new = approved.json()
    assert new["version_index"] == 2 and new["version_number"] == "2" and new["title"] == version["title"]
    metadata = client.get(new["links"]["document_version_metadata"]["url"], headers=editor.headers).json()
    entries = {entry["name"]: entry["value"] for entry in metadata["metadata"]}
    assert entries["base_version_index"] == ["1"] and entries["change_set_id"] == [topic["guid"]]
    assert entries["proposed_by"] == ["editor"] and entries["approved_by"] == ["manager"]
    assert entries["ai_model"] == ["claude-opus-5-5"] and entries["tool_call"] == ["rename"]
    assert entries["uploaded_by"] == ["editor"] and entries["file_name"] == ["three_walls_gtin.ifc"]
    assert checked(client, editor, new)["ifc_validation_status"] == ["passed"], "the new version is checked as any"
    changes = client.get(new["links"]["document_version"]["url"] + "/ifc-changes", headers=editor.headers)
    assert changes.status_code == 200, changes.text
    assert changes.json()["changes"] == [rename] and changes.json()["base"]["version_index"] == 1
    assert changes.json()["change_set_id"] == topic["guid"] and changes.json()["approved_by"] == "manager"
    whole_file = client.get(version["links"]["document_version"]["url"] + "/ifc-changes", headers=editor.headers)
    assert whole_file.status_code == 404, "version 1 was uploaded as a whole file"
    download = client.get(new["links"]["document_version_download"]["url"], headers=editor.headers)
    assert download.status_code == 200 and b"YVT-A2" in download.content and b"'YVT-A'" not in download.content
    report = client.get(proposal, headers=editor.headers).json()
    assert report["state"] == "approved" and report["approved_by"] == "manager" and report["version_index"] == 2
    assert client.post(approve, headers=manager.headers).status_code == 409, "approved once"
    since = client.get(
        new["links"]["document_version"]["url"] + "/ifc-queries/changed-since?since=1", headers=editor.headers
    )
    assert since.status_code == 200, since.text
    assert since.json()["rows"] == [[2, topic["guid"], "editor", "manager", False, WALLS[0], None, None, "Name", "set"]]
    from_the_start = client.get(
        new["links"]["document_version"]["url"] + "/ifc-queries/changed-since?since=0", headers=editor.headers
    ).json()
    assert [row[0] for row in from_the_start["rows"]] == [1, 2] and from_the_start["rows"][0][4] is True
    assert (
        client.get(
            new["links"]["document_version"]["url"] + "/ifc-queries/changed-since?since=2", headers=editor.headers
        ).status_code
        == 400
    )
    comments = client.get(f"{topics}/{topic['guid']}/comments", headers=editor.headers).json()
    assert any("version 2" in comment["comment"] and comment["author"] == "manager" for comment in comments)
    assert client.get(f"{topics}/{topic['guid']}", headers=editor.headers).json()["topic_status"] == "Closed"
    (viewpoint,) = client.get(f"{topics}/{topic['guid']}/viewpoints", headers=editor.headers).json()
    selection = client.get(
        f"{topics}/{topic['guid']}/viewpoints/{viewpoint['guid']}/selection", headers=editor.headers
    ).json()["selection"]
    assert [component["ifc_guid"] for component in selection] == [WALLS[0]], "the viewpoint selects what changed"

    # A change set on a base the document has moved past is not approved on a server without ifcmerge: 412, for
    # the client to propose it again on the latest (merged where there is ifcmerge: test_proposal_merges.py).
    stale = make_topic(client, world, editor, bim_snippet=snippet_fields)
    assert (
        client.put(
            f"{topics}/{stale['guid']}/snippet", content=change_set(document_id, rename), headers=editor.headers
        ).status_code
        == 200
    )
    assert tried(client, editor, f"{topics}/{stale['guid']}/ifc-proposal")["state"] == "clean"
    refused = client.post(f"{topics}/{stale['guid']}/ifc-proposal/approve", headers=manager.headers)
    assert refused.status_code == 412 and refused.json()["code"] == "version_conflict"
    assert refused.json()["conflicts"] == ["*"] and "does not merge" in refused.json()["message"]
    assert refused.json()["merge"] == {"base_version_index": 1, "latest_version_index": 2}

    # A trial with issues is not approved either.
    broken = make_topic(client, world, editor, bim_snippet=snippet_fields)
    duplicate = {"guid": WALLS[0], "attribute": "GlobalId", "op": "set", "new": WALLS[1]}
    two = json.loads(change_set(document_id, duplicate))
    two["base"]["version_index"] = 2
    assert (
        client.put(
            f"{topics}/{broken['guid']}/snippet", content=json.dumps(two).encode(), headers=editor.headers
        ).status_code
        == 200
    )
    assert tried(client, editor, f"{topics}/{broken['guid']}/ifc-proposal")["state"] == "issues"
    assert client.post(f"{topics}/{broken['guid']}/ifc-proposal/approve", headers=manager.headers).status_code == 409


def test_the_proposals_against_a_document_are_listed_for_its_members(ifc_client: tuple[TestClient, World]) -> None:
    """The Documents API lists the change sets tried against a document, newest first, with the addresses of the
    trial and its approval; a member sees them, an outsider sees no document."""
    client, world = ifc_client
    editor = login_as(client, world, "editor")
    web_sign_in(client, world, "editor")
    manager = login_as(client, world, "manager")
    viewer = login_as(client, world, "viewer")
    outsider = login_as(client, world, "outsider")
    version = upload(client, world, editor, "three_walls_gtin.ifc", MODEL.read_bytes())
    assert checked(client, editor, version)["ifc_validation_status"] == ["passed"]
    document_id = version["document_id"]
    listing = f"/documents/1.0/documents/{document_id}/ifc-proposals"
    assert client.get(listing, headers=viewer.headers).json() == [], "nothing proposed yet"
    assert client.get(listing).status_code == 401
    assert client.get(listing, headers=outsider.headers).status_code == 404, "not a member: no such document"
    elsewhere = "/documents/1.0/documents/11111111-2222-4333-8444-555555555555/ifc-proposals"
    assert client.get(elsewhere, headers=viewer.headers).status_code == 404
    not_an_id = "/documents/1.0/documents/not-a-document/ifc-proposals"
    assert client.get(not_an_id, headers=viewer.headers).status_code == 404

    topics = f"/bcf/3.0/projects/{world.project_id}/topics"
    snippet_fields = {
        "snippet_type": SNIPPET_TYPE,
        "is_external": False,
        "reference": "changes.json",
        "reference_schema": f"http://testserver{SCHEMA_PATH}",
    }
    rename = {"guid": WALLS[0], "attribute": "Name", "op": "set", "old": "YVT-A", "new": "YVT-A2"}
    duplicate = {"guid": WALLS[0], "attribute": "GlobalId", "op": "set", "new": WALLS[1]}
    first = make_topic(client, world, editor, title="Rename the wall", bim_snippet=snippet_fields)
    second = make_topic(
        client, world, editor, title="Duplicate a GlobalId", description="d" * 300, bim_snippet=snippet_fields
    )
    for topic, change in ((first, rename), (second, duplicate)):
        put = client.put(
            f"{topics}/{topic['guid']}/snippet", content=change_set(document_id, change), headers=editor.headers
        )
        assert put.status_code == 200, put.text
        tried(client, editor, f"{topics}/{topic['guid']}/ifc-proposal")

    response = client.get(listing, headers=viewer.headers)
    assert response.status_code == 200, response.text
    assert "x-total-count" not in response.headers and "link" not in response.headers, "a whole list says nothing"
    newest, oldest = response.json()
    assert [entry["topic_guid"] for entry in (newest, oldest)] == [second["guid"], first["guid"]], "newest first"
    assert oldest == {
        "topic_guid": first["guid"],
        "title": "Rename the wall",
        "description": None,
        "state": "clean",
        "base_version_index": 1,
        "change_count": 1,
        "author": "editor",
        "checked_at": oldest["checked_at"],
        "version_index": None,
        "proposal_url": f"http://testserver{topics}/{first['guid']}/ifc-proposal",
        "approve_url": f"http://testserver{topics}/{first['guid']}/ifc-proposal/approve",
    }
    assert oldest["checked_at"].endswith("Z")
    assert newest["state"] == "issues" and newest["change_count"] == 1 and newest["description"] == "d" * 200
    assert client.get(listing + "?$skip=1", headers=viewer.headers).json() == [oldest]
    assert client.get(listing + "?$top=1", headers=viewer.headers).json() == [newest]
    refused = client.get(listing + "?$filter=title eq 'x'", headers=viewer.headers)
    assert refused.status_code == 400, "nothing to filter on"

    # Approved through the row's own address, the row names the version it became.
    approved = client.post(oldest["approve_url"], headers=manager.headers)
    assert approved.status_code == 201, approved.text
    rows = {entry["topic_guid"]: entry for entry in client.get(listing, headers=viewer.headers).json()}
    assert rows[first["guid"]]["state"] == "approved" and rows[first["guid"]]["version_index"] == 2
    assert rows[first["guid"]]["base_version_index"] == 1 and rows[second["guid"]]["version_index"] is None
    assert client.get(rows[first["guid"]]["proposal_url"], headers=viewer.headers).json()["state"] == "approved"

    # A trial that failed is listed too: a proposal names its base from the moment it is queued.
    notes = upload(client, world, editor, "notes.txt", b"not a model")
    third = make_topic(client, world, editor, title="Not a model", bim_snippet=snippet_fields)
    put = client.put(
        f"{topics}/{third['guid']}/snippet", content=change_set(notes["document_id"], rename), headers=editor.headers
    )
    assert put.status_code == 200, put.text
    assert tried(client, editor, f"{topics}/{third['guid']}/ifc-proposal")["state"] == "failed"
    against_notes = f"/documents/1.0/documents/{notes['document_id']}/ifc-proposals"
    (failed,) = client.get(against_notes, headers=viewer.headers).json()
    assert failed["topic_guid"] == third["guid"] and failed["state"] == "failed" and failed["checked_at"]
    assert failed["base_version_index"] == 1 and failed["change_count"] is None and failed["version_index"] is None
    assert len(client.get(listing, headers=viewer.headers).json()) == 2, "the other document's is not here"


# -- Dataspatium's write tools in a change set (``ops``), applied with the app's own code ---------------------------

SNIPPET_FIELDS = {
    "snippet_type": SNIPPET_TYPE,
    "is_external": False,
    "reference": "changes.json",
    "reference_schema": f"http://testserver{SCHEMA_PATH}",
}
NEW_WALL = {"tool": "create_wall", "storey": None, "start": [0, 0], "end": [4, 0], "thickness": 0.2, "height": 3,
            "name": "New wall"}  # fmt: skip
LOCATION = "ObjectPlacement.RelativePlacement.Location"  # the first wall's own point, in millimetres


def with_ops(document_id: str, version_index: int, ops: list[dict[str, Any]], *changes: Any, **more: Any) -> bytes:
    """A change set as Dataspatium sends one from its write tools: the operations, and the changes they make to the
    instances the base has (none, where they only create)."""
    body = json.loads(change_set(document_id, *changes))
    body["base"]["version_index"] = version_index
    return json.dumps({**body, "ops_form": "dataspatium-tools/1", "ops": ops, **more}).encode("utf-8")


def opened(client: TestClient, session: Session, version: dict[str, Any], tmp_path: Any) -> Any:
    import ifcopenshell

    download = client.get(version["links"]["document_version_download"]["url"], headers=session.headers)
    assert download.status_code == 200, download.text
    path = tmp_path / f"version-{version['version_index']}.ifc"
    path.write_bytes(download.content)
    return ifcopenshell.open(str(path))


def test_a_change_set_with_operations_builds_a_wall_that_the_approval_keeps(
    ifc_client: tuple[TestClient, World], tmp_path: Any
) -> None:
    client, world = ifc_client
    editor = login_as(client, world, "editor")
    web_sign_in(client, world, "editor")
    manager = login_as(client, world, "manager")
    tools = client.get("/.well-known/dataregium").json()["extensions"]["ifc_changes"]
    assert tools["ops_form"] == "dataspatium-tools/1" and "create_wall" in tools["ops_tools"]
    version = upload(client, world, editor, "three_walls_gtin.ifc", MODEL.read_bytes())
    assert checked(client, editor, version)["ifc_validation_status"] == ["passed"]
    document_id = version["document_id"]
    topics = f"/bcf/3.0/projects/{world.project_id}/topics"
    topic = make_topic(client, world, editor, title="A wall with a door", bim_snippet=SNIPPET_FIELDS)
    snippet = f"{topics}/{topic['guid']}/snippet"
    proposal = f"{topics}/{topic['guid']}/ifc-proposal"

    unknown = with_ops(document_id, 1, [NEW_WALL, {"tool": "drop_everything"}])
    refused = client.put(snippet, content=unknown, headers=editor.headers)
    assert refused.status_code == 400 and "ops.1.tool" in refused.text and "drop_everything" not in refused.text
    no_form = json.loads(with_ops(document_id, 1, [NEW_WALL]))
    del no_form["ops_form"]
    assert client.put(snippet, content=json.dumps(no_form).encode(), headers=editor.headers).status_code == 400

    door = {"tool": "create_opening", "wall_id": "new-1", "offset": 1, "width": 0.9, "height": 2.1, "fill": "door"}
    fire = {"tool": "set_properties", "ids": ["new-1"], "pset": "Pset_WallCommon", "properties": {"FireRating": "EI60"}}
    put = client.put(snippet, content=with_ops(document_id, 1, [NEW_WALL, door, fire]), headers=editor.headers)
    assert put.status_code == 200, put.text
    report = tried(client, editor, proposal)
    assert report["state"] == "clean", report
    assert report["change_count"] == 3 and report["applied"] == 3
    assert report["notes"] == [
        "IsExternal was left out on the walls of storey 1. etasje that the change set made or changed, and on their "
        "doors and windows: 8 wall ends stand open, so the app cannot tell outside from inside"
    ], "the new wall stands alone, so the topology tells no outside"
    assert list(report["created"]) == ["new-1", "new-2"], "the wall and the door, by their refs"

    approved = client.post(proposal + "/approve", headers=manager.headers)
    assert approved.status_code == 201, approved.text
    new = approved.json()
    assert new["version_index"] == 2
    model = opened(client, editor, new, tmp_path)
    wall = model.by_guid(report["created"]["new-1"])
    assert wall.is_a("IfcWall") and wall.Name == "New wall", "the version holds the wall the operations built"
    assert model.by_guid(report["created"]["new-2"]).is_a("IfcDoor")
    assert len(model.by_type("IfcWall")) == 4
    after = client.get(proposal, headers=editor.headers).json()
    assert after["state"] == "approved" and after["created"] == report["created"]
    (viewpoint,) = client.get(f"{topics}/{topic['guid']}/viewpoints", headers=editor.headers).json()
    selection = client.get(
        f"{topics}/{topic['guid']}/viewpoints/{viewpoint['guid']}/selection", headers=editor.headers
    ).json()["selection"]
    assert report["created"]["new-1"] in {component["ifc_guid"] for component in selection}, "what was built"

    # An operation that cannot be applied is an issue that names it, and the trial is not approved.
    failing = make_topic(client, world, editor, bim_snippet=SNIPPET_FIELDS)
    nowhere = {**NEW_WALL, "storey": "Level 9"}
    put = client.put(
        f"{topics}/{failing['guid']}/snippet",
        content=with_ops(document_id, 2, [NEW_WALL, nowhere]),
        headers=editor.headers,
    )
    assert put.status_code == 200, put.text
    report = tried(client, editor, f"{topics}/{failing['guid']}/ifc-proposal")
    assert report["state"] == "issues" and report["applied"] == 1 and report["change_count"] == 2
    (issue,) = [issue for issue in report["issues"] if issue["kind"] == "op"]
    assert (issue["op"], issue["tool"]) == (1, "create_wall") and issue["message"].startswith("op 1 (create_wall): ")
    assert client.post(f"{topics}/{failing['guid']}/ifc-proposal/approve", headers=manager.headers).status_code == 409


FILE_SNIPPET = {
    "snippet_type": "DataregiumIfcFile",
    "is_external": False,
    "reference": "proposal.ifc",
    "reference_schema": "https://standards.buildingsmart.org/IFC/RELEASE/IFC4/ADD2_TC1/HTML/",
}


def renamed_file(tmp_path: Any, guid: str, name: str, source: Any = MODEL) -> bytes:
    """The model with one wall renamed, written by IfcOpenShell as an app would."""
    import ifcopenshell

    model = ifcopenshell.open(str(source))
    model.by_guid(guid).Name = name
    target = tmp_path / f"{name}.ifc"
    model.write(str(target))
    return target.read_bytes()


def test_a_whole_file_proposed_on_its_base_is_tried_and_approved(
    ifc_client: tuple[TestClient, World], tmp_path: Any
) -> None:
    """F204 a and F124 a: a topic's snippet of the type DataregiumIfcFile is a whole IFC file on a base version, named
    in the upload's query with what made it; it is checked as it is, given back, and approved as a change set is."""
    client, world = ifc_client
    editor = login_as(client, world, "editor")
    web_sign_in(client, world, "editor")
    manager = login_as(client, world, "manager")
    viewer = login_as(client, world, "viewer")
    version = upload(client, world, editor, "three_walls_gtin.ifc", MODEL.read_bytes())
    assert checked(client, editor, version)["ifc_validation_status"] == ["passed"]
    document_id = version["document_id"]
    topics = f"/bcf/3.0/projects/{world.project_id}/topics"
    topic = make_topic(client, world, editor, bim_snippet=FILE_SNIPPET)
    snippet, proposal = f"{topics}/{topic['guid']}/snippet", f"{topics}/{topic['guid']}/ifc-proposal"
    data = renamed_file(tmp_path, WALLS[0], "Whole")
    base = {"base_document_id": document_id, "base_version_index": 1}

    assert client.put(snippet, content=data, headers=editor.headers).status_code == 400, "a whole file names its base"
    assert client.put(snippet, content=b"not a model", headers=editor.headers, params=base).status_code == 400
    assert client.put(snippet, content=data, headers=viewer.headers, params=base).status_code == 403
    put = client.put(snippet, content=data, headers=editor.headers, params={**base, "ai_model": "claude-opus-5-5"})
    assert put.status_code == 200, put.text
    report = tried(client, editor, proposal)
    assert report["state"] == "clean" and report["kind"] == "file", report
    assert report["base"] == {"document_id": document_id, "version_index": 1} and "change_count" not in report
    given = client.get(proposal + "/file", headers=viewer.headers)
    assert given.status_code == 200 and b"'Whole'" in given.content, "the trial's file, for every member"
    assert client.get("/.well-known/dataregium").json()["extensions"]["ifc_changes"]["file_snippet_type"] == (
        "DataregiumIfcFile"
    )

    approved = client.post(proposal + "/approve", headers=manager.headers)
    assert approved.status_code == 201, approved.text
    new = approved.json()
    entries = {
        entry["name"]: entry["value"]
        for entry in client.get(new["links"]["document_version_metadata"]["url"], headers=editor.headers).json()[
            "metadata"
        ]
    }
    assert entries["proposed_by"] == ["editor"] and entries["ai_model"] == ["claude-opus-5-5"]
    download = client.get(new["links"]["document_version_download"]["url"], headers=editor.headers)
    assert b"'Whole'" in download.content and b"'YVT-A'" not in download.content
    (viewpoint,) = client.get(f"{topics}/{topic['guid']}/viewpoints", headers=editor.headers).json()
    selection = client.get(
        f"{topics}/{topic['guid']}/viewpoints/{viewpoint['guid']}/selection", headers=editor.headers
    ).json()["selection"]
    assert [component["ifc_guid"] for component in selection] == [WALLS[0]], "what differs from the base"
