# This file was generated with the assistance of an AI coding tool.
"""A change set's trial as a branch and its approval as a merge commit, merged by ifcmerge when the document has moved
on since its base (ADR 0030, UL-48, fas 5.4). PostgreSQL alone, as the repositories are (F99 a). ifcmerge is GPL and
never here: the tests run the stand-in in the fixtures, and the real one where ``OPENCDE_TEST_IFCMERGE`` names it."""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from opencde.main import create_app
from opencde.settings import Settings
from tests.conftest import FIXTURES_DIR
from tests.integration.conftest import bim_settings
from tests.integration.conftest_accounts import World, build_world, login_as
from tests.integration.helpers import Session
from tests.integration.test_bcf_links_documents import make_topic
from tests.integration.test_git_repos import _git
from tests.integration.test_ifc_proposal import (
    LOCATION,
    NEW_WALL,
    SNIPPET_FIELDS,
    change_set,
    opened,
    tried,
    with_ops,
)
from tests.integration.test_ifc_validation import MODEL, WALLS, checked, upload
from tests.integration.test_web_signin import web_sign_in

pytestmark = [pytest.mark.integration, pytest.mark.parametrize("store_kind", ["postgres"], indirect=True)]
ifcopenshell = pytest.importorskip("ifcopenshell", reason="the ifc-db extra is not installed")
REAL = os.environ.get("OPENCDE_TEST_IFCMERGE")


@pytest.fixture
def merging(settings: Settings, clean_database: None) -> Iterator[tuple[TestClient, World, Settings]]:
    """Tier 2 with the repositories and ifcmerge: the real one where CI fetches it, else the stand-in."""
    merge = {"ifcmerge_path": REAL, "perl_path": "perl"} if REAL else {}
    on = bim_settings(settings).model_copy(
        update={
            "git_enabled": True,
            "ifcmerge_path": str(FIXTURES_DIR / "ifcmerge_stand_in.py"),
            "perl_path": sys.executable,
            **merge,
        }
    )
    world = build_world(on)
    with TestClient(create_app(on, startup_wait_seconds=30)) as client:
        yield client, world, on


def _propose(client: TestClient, world: World, editor: Session, body: bytes) -> str:
    topic = make_topic(client, world, editor, bim_snippet=SNIPPET_FIELDS)
    topics = f"/bcf/3.0/projects/{world.project_id}/topics"
    put = client.put(f"{topics}/{topic['guid']}/snippet", content=body, headers=editor.headers)
    assert put.status_code == 200, put.text
    proposal = f"{topics}/{topic['guid']}/ifc-proposal"
    report = tried(client, editor, proposal)
    assert report["state"] == "clean", report
    return proposal


def _metadata(client: TestClient, session: Session, version: dict[str, Any]) -> dict[str, list[str]]:
    response = client.get(version["links"]["document_version_metadata"]["url"], headers=session.headers)
    return {entry["name"]: entry["value"] for entry in response.json()["metadata"]}


def _branches(settings: Settings, project_id: str) -> dict[str, str]:
    listed = _git(settings, project_id, "for-each-ref", "--format=%(refname) %(objectname)", "refs/heads/proposals/")
    return dict(line.split(" ") for line in listed.splitlines())


def _parents(settings: Settings, project_id: str, commit: str) -> list[str]:
    return _git(settings, project_id, "rev-list", "--parents", "-n", "1", commit).split()[1:]


def test_a_trial_is_a_branch_and_its_approval_merges_it(merging: tuple[TestClient, World, Settings]) -> None:
    client, world, settings = merging
    editor = login_as(client, world, "editor")
    web_sign_in(client, world, "editor")
    manager = login_as(client, world, "manager")
    first = upload(client, world, editor, "three_walls_gtin.ifc", MODEL.read_bytes())
    assert checked(client, editor, first)["ifc_validation_status"] == ["passed"]
    on_first = _metadata(client, editor, first)["git_commit"][0]
    rename = {"guid": WALLS[0], "attribute": "Name", "op": "set", "new": "Renamed"}
    proposal = _propose(client, world, editor, change_set(first["document_id"], rename))

    ((ref, branch),) = _branches(settings, world.project_id).items()
    assert _parents(settings, world.project_id, branch) == [on_first], "the trial's file on the base's commit"
    message = _git(settings, world.project_id, "log", "-1", "--format=%an|%cn|%B", branch)
    assert message.startswith("editor|Dataregium|") and f"Dataregium-Proposal: {ref.rsplit('/', 1)[1]}" in message
    path = f"documents/{first['document_id']}/model.ifc"
    assert "'Renamed'" in _git(settings, world.project_id, "cat-file", "blob", f"{branch}:{path}")

    approved = client.post(proposal + "/approve", headers=manager.headers)
    assert approved.status_code == 201, approved.text
    commit = _metadata(client, editor, approved.json())["git_commit"][0]
    assert _parents(settings, world.project_id, commit) == [on_first, branch], "main, and the proposal's branch"
    message = _git(settings, world.project_id, "log", "-1", "--format=%an|%cn|%B", commit)
    assert message.startswith("editor|manager|") and "Dataregium-Approved-By: manager" in message
    assert _branches(settings, world.project_id) == {}, "the branch goes once it is merged"


def test_the_approval_merges_a_change_set_on_an_older_base(merging: tuple[TestClient, World, Settings]) -> None:
    """A, B and C rename walls on version 1; A becomes version 2, B is merged with it as version 3, and C, which
    renames A's wall again, collides."""
    client, world, settings = merging
    editor = login_as(client, world, "editor")
    web_sign_in(client, world, "editor")
    manager = login_as(client, world, "manager")
    first = upload(client, world, editor, "three_walls_gtin.ifc", MODEL.read_bytes())
    assert checked(client, editor, first)["ifc_validation_status"] == ["passed"]
    document_id = first["document_id"]
    a = _propose(
        client, world, editor, change_set(document_id, {"guid": WALLS[0], "attribute": "Name", "op": "set", "new": "A"})
    )
    b = _propose(
        client, world, editor, change_set(document_id, {"guid": WALLS[1], "attribute": "Name", "op": "set", "new": "B"})
    )
    c = _propose(
        client, world, editor, change_set(document_id, {"guid": WALLS[0], "attribute": "Name", "op": "set", "new": "C"})
    )
    second = client.post(a + "/approve", headers=manager.headers).json()
    assert second["version_index"] == 2

    merged = client.post(b + "/approve", headers=manager.headers)
    assert merged.status_code == 201, merged.text
    third = merged.json()
    assert third["version_index"] == 3
    meta = _metadata(client, editor, third)
    assert meta["base_version_index"] == ["1"] and meta["merged_with_version_index"] == ["2"]
    trial = client.get(b, headers=editor.headers).json()
    assert (trial["state"], trial["base"]["version_index"], trial["merged_with_version_index"]) == ("approved", 1, 2)
    assert trial["version_index"] == 3 and "renumbered" not in trial, "a rename moves no id"
    download = client.get(third["links"]["document_version_download"]["url"], headers=editor.headers).content
    assert b"'A'" in download and b"'B'" in download, "both changes are in version 3"
    parents = _parents(settings, world.project_id, meta["git_commit"][0])
    assert parents[0] == _metadata(client, editor, second)["git_commit"][0] and len(parents) == 2

    refused = client.post(c + "/approve", headers=manager.headers)
    assert refused.status_code == 412, refused.text
    body = refused.json()
    step = ifcopenshell.open(str(MODEL)).by_guid(WALLS[0]).id()
    assert (
        body["code"] == "version_conflict" and body["conflicts"] == [f"#{step}.Name"] and "collide" in body["message"]
    )
    (detail,) = body["conflict_details"]
    assert {key: detail[key] for key in ("key", "guid", "step_id", "attribute", "op", "base", "theirs", "ours")} == {
        "key": f"#{step}.Name",
        "guid": WALLS[0],
        "step_id": step,
        "attribute": "Name",
        "op": "set",
        "base": "YVT-A",
        "theirs": "A",
        "ours": "C",
    }, "what the base held, what the latest holds and what the proposal sets"
    assert detail["version_index"] == 3 and body["merge"] == {"base_version_index": 1, "latest_version_index": 3}
    assert detail["change_index"] == 0, "the change in the change set, for the client to pair them"
    assert client.get(c, headers=editor.headers).json()["state"] == "clean", "a collision leaves the trial as it was"


def test_new_elements_merged_past_mains_are_renumbered(
    merging: tuple[TestClient, World, Settings], tmp_path: Any
) -> None:
    """Two change sets on version 1 each build a wall; the second, merged with the first, has its new instances moved
    past version 2's highest id, and the trial and the version say where each went."""
    client, world, _ = merging
    editor = login_as(client, world, "editor")
    web_sign_in(client, world, "editor")
    manager = login_as(client, world, "manager")
    first = upload(client, world, editor, "three_walls_gtin.ifc", MODEL.read_bytes())
    assert checked(client, editor, first)["ifc_validation_status"] == ["passed"]
    document_id = first["document_id"]
    one = _propose(client, world, editor, with_ops(document_id, 1, [NEW_WALL]))
    other = {**NEW_WALL, "start": [0, 5], "end": [4, 5], "name": "Other wall"}
    two = _propose(client, world, editor, with_ops(document_id, 1, [other]))
    assert client.post(one + "/approve", headers=manager.headers).status_code == 201

    merged = client.post(two + "/approve", headers=manager.headers)
    assert merged.status_code == 201, client.get(two, headers=editor.headers).json()["issues"]
    trial = client.get(two, headers=editor.headers).json()
    renumbered = {int(old): new for old, new in trial["renumbered"].items()}
    assert renumbered and all(new > old for old, new in renumbered.items())
    model = opened(client, editor, merged.json(), tmp_path)
    wall = model.by_guid(trial["created"]["new-1"])
    assert wall.Name == "Other wall" and wall.id() in renumbered.values(), "followed by its GlobalId and its id"
    assert {w.Name for w in model.by_type("IfcWall")} >= {"New wall", "Other wall"}
    meta = _metadata(client, editor, merged.json())
    assert meta["ifc_renumbered"][: len(renumbered)] == [f"{old}>{new}" for old, new in sorted(renumbered.items())]


def test_a_proposal_that_gives_up_a_conflict_keeps_theirs_once_merged(
    merging: tuple[TestClient, World, Settings], tmp_path: Any
) -> None:
    """The client cannot cut one change out of its operations, so it sends them again with the key ifcmerge gave in
    ``keep_theirs``: the server applies them to the latest version and gives the key the latest version's value."""
    client, world, _ = merging
    editor = login_as(client, world, "editor")
    web_sign_in(client, world, "editor")
    manager = login_as(client, world, "manager")
    first = upload(client, world, editor, "three_walls_gtin.ifc", MODEL.read_bytes())
    assert checked(client, editor, first)["ifc_validation_status"] == ["passed"]
    document_id = first["document_id"]
    point = ifcopenshell.open(str(MODEL)).by_guid(WALLS[0]).ObjectPlacement.RelativePlacement.Location
    x, y, z = point.Coordinates

    # Theirs: the first wall moved to the origin, approved as version 2.
    origin = {"guid": WALLS[0], "path": LOCATION, "attribute": "Coordinates", "op": "set", "new": [0.0, 0.0, 0.0]}
    theirs = _propose(client, world, editor, change_set(document_id, origin))
    assert client.post(theirs + "/approve", headers=manager.headers).status_code == 201

    # Ours, on version 1: the wall moved a metre and a new wall.
    move = {"tool": "transform_elements", "ids": [WALLS[0]], "dx": 1, "dy": 0, "dz": 0}
    moved = {**origin, "old": [x, y, z], "new": [x + 1000, y, z]}
    ours = _propose(client, world, editor, with_ops(document_id, 1, [move, NEW_WALL], moved))
    refused = client.post(ours + "/approve", headers=manager.headers)
    assert refused.status_code == 412, refused.text
    (key,) = refused.json()["conflicts"]
    assert key == f"#{point.id()}.Coordinates"
    (detail,) = refused.json()["conflict_details"]
    assert detail["change_index"] == 0, "the change named by GlobalId and path reaches the point"

    # The person keeps theirs: the same operations again, on the latest, with the key given up.
    snippet = ours.removesuffix("/ifc-proposal") + "/snippet"
    again = with_ops(document_id, 2, [move, NEW_WALL], keep_theirs=[key])
    assert client.put(snippet, content=again, headers=editor.headers).status_code == 200
    report = tried(client, editor, ours)
    assert report["state"] == "clean", report
    assert report["base"]["version_index"] == 2, "tried on the latest version"
    approved = client.post(ours + "/approve", headers=manager.headers)
    assert approved.status_code == 201, approved.text
    assert approved.json()["version_index"] == 3
    model = opened(client, editor, approved.json(), tmp_path)
    location = model.by_guid(WALLS[0]).ObjectPlacement.RelativePlacement.Location
    assert tuple(location.Coordinates) == (0.0, 0.0, 0.0), "theirs is kept"
    assert model.by_guid(report["created"]["new-1"]).Name == "New wall", "the rest of the operations stand"


def test_the_upkeep_takes_the_branches_of_trials_that_are_gone(merging: tuple[TestClient, World, Settings]) -> None:
    from opencde.repos.upkeep import upkeep

    client, world, settings = merging
    editor = login_as(client, world, "editor")
    web_sign_in(client, world, "editor")
    first = upload(client, world, editor, "three_walls_gtin.ifc", MODEL.read_bytes())
    assert checked(client, editor, first)["ifc_validation_status"] == ["passed"]
    rename = {"guid": WALLS[0], "attribute": "Name", "op": "set", "new": "Once"}
    proposal = _propose(client, world, editor, change_set(first["document_id"], rename))
    (old,) = _branches(settings, world.project_id)
    snippet = proposal.removesuffix("/ifc-proposal") + "/snippet"
    again = change_set(first["document_id"], {**rename, "new": "Twice"})
    assert client.put(snippet, content=again, headers=editor.headers).status_code == 200
    assert tried(client, editor, proposal)["state"] == "clean"
    assert len(_branches(settings, world.project_id)) == 2, "the replaced trial's branch is still there"
    state = client.app.state  # type: ignore[attr-defined]
    done = client.portal.call(upkeep, state.git, state.ifcgraph.proposals.open_among)  # type: ignore[union-attr]
    assert done.branches == 1 and old not in _branches(settings, world.project_id)
    assert len(_branches(settings, world.project_id)) == 1
