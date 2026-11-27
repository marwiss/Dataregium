# This file was generated with the assistance of an AI coding tool.
"""A document's history in its project's repository, a version's commit, and the diff on any commit of it (ADR 0030,
UL-48, fas 5.5): versions, an upload as it came, a change set's trial and an upload that did not merge, kept a day
on its conflict's branch (F96 b). PostgreSQL alone, as the repositories are (F99 a)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from opencde.main import create_app
from opencde.settings import Settings
from tests.conftest import FIXTURES_DIR
from tests.integration.conftest_accounts import World, build_world, login_as
from tests.integration.helpers import Session
from tests.integration.test_documents_api import save_metadata, start_upload
from tests.integration.test_git_repos import _upload
from tests.integration.test_ifc_proposal import change_set
from tests.integration.test_ifc_validation import WALLS
from tests.integration.test_proposal_merges import _metadata, _propose
from tests.integration.test_web_signin import web_sign_in

pytestmark = [pytest.mark.integration, pytest.mark.parametrize("store_kind", ["postgres"], indirect=True)]
pytest.importorskip("ifcopenshell", reason="the ifc-db extra is not installed")
DATA = (FIXTURES_DIR / "three_walls_gtin.ifc").read_bytes()


def _refused(client: TestClient, world: World, editor: Session, file: dict[str, Any], data: bytes) -> dict[str, Any]:
    """An upload that does not merge: its 412's body."""
    started = start_upload(client, editor, [{"session_file_id": "f", **file}])
    instructions = save_metadata(
        client, started["upload_ui_url"], world.project_id, {"title_0": "Plan", "version_0": "x"}
    )
    plan = client.post(
        instructions, json={"files": [{"session_file_id": "f", "size_in_bytes": len(data)}]}, headers=editor.headers
    ).json()["documents_to_upload"][0]
    for part in plan["upload_file_parts"]:
        chunk = data[part["content_range_start"] : part["content_range_end"] + 1]
        assert client.put(part["url"], content=chunk, headers=editor.headers).status_code == 200
    refused = client.post(plan["upload_completion"]["url"], headers=editor.headers)
    assert refused.status_code == 412, refused.text
    return refused.json()


def test_a_documents_history_lists_its_versions_uploads_trials_and_conflicts(
    merging: tuple[TestClient, World, Settings], monkeypatch: pytest.MonkeyPatch
) -> None:
    from opencde.repos import upkeep as upkeep_module

    client, world, _ = merging
    editor = login_as(client, world, "editor")
    web_sign_in(client, world, "editor")
    outsider = login_as(client, world, "outsider")
    first = _upload(client, world, editor, {"file_name": "model.ifc"}, DATA, "1")
    document_id = first["document_id"]
    on_first = {"file_name": "model.ifc", "document_id": document_id, "base_version_index": 1}
    second = _upload(client, world, editor, on_first, DATA.replace(b"'YVT-A'", b"'Main'"), "2")
    third = _upload(client, world, editor, on_first, DATA.replace(b"'YVT-B'", b"'Mine'"), "3")
    assert _metadata(client, editor, third)["merged_with_version_index"] == ["2"]
    body = _refused(client, world, editor, on_first, DATA.replace(b"'YVT-B'", b"'CONFLICT'"))
    conflict = body["conflict_commit"]
    assert len(conflict) == 40 and body["conflict_expires_at"].endswith("Z")
    rename = {"guid": WALLS[2] if len(WALLS) > 2 else WALLS[0], "attribute": "Description", "op": "set", "new": "D"}
    proposal = _propose(client, world, editor, change_set(document_id, rename))

    history = f"/documents/1.0/documents/{document_id}/history"
    response = client.get(history, headers=editor.headers)
    assert response.status_code == 200, response.text
    entries = response.json()
    kinds = sorted(entry["kind"] for entry in entries)
    assert kinds == ["conflict", "proposal", "upload", "version", "version", "version"], entries
    commits = {_metadata(client, editor, version)["git_commit"][0]: version for version in (first, second, third)}
    by_kind = {entry["kind"]: entry for entry in entries}
    for entry in entries:
        if entry["kind"] == "version":
            assert commits[entry["commit"]]["version_index"] == entry["version_index"]
    upload = by_kind["upload"]
    assert (upload["version_index"], upload["base_version_index"]) == (3, 1), "the upload as it came, in version 3"
    trial = by_kind["proposal"]
    assert trial["proposal_url"] == f"http://testserver{proposal}" and trial["base_version_index"] == 1
    kept = by_kind["conflict"]
    assert kept["commit"] == conflict and kept["expires_at"] == body["conflict_expires_at"]
    assert kept["merged_with_version_index"] == 3 and kept["base_version_index"] == 1
    assert entries[0]["date"] >= entries[-1]["date"] and all(entry["date"].endswith("Z") for entry in entries)
    page = client.get(history + "?$top=2", headers=editor.headers)
    assert len(page.json()) == 2 and page.headers["x-total-count"] == "6"
    assert page.headers["link"].endswith('%24skip=2>; rel="next"') or "skip=2" in page.headers["link"]
    assert "x-total-count" not in response.headers, "a whole list says nothing"
    assert len(client.get(history + "?$skip=5", headers=editor.headers).json()) == 1
    assert client.get(history, headers=outsider.headers).status_code == 404
    assert client.get(history + "?$top=501", headers=editor.headers).status_code == 400

    # A version's commit: the merged upload's side, its parents and its whole message.
    made = client.get(f"/documents/1.0/documents/{document_id}/versions/3/commit", headers=editor.headers)
    assert made.status_code == 200, made.text
    merge = made.json()
    assert merge["side_commit"] == upload["commit"] and "proposal_commit" not in merge
    assert merge["parents"][0] == _metadata(client, editor, second)["git_commit"][0]
    assert (merge["base_version_index"], merge["merged_with_version_index"]) == (1, 2)
    assert "Dataregium-Merged-With: 2" in merge["message"] and merge["committer"] == "Dataregium"
    root = client.get(f"/documents/1.0/documents/{document_id}/versions/1/commit", headers=editor.headers).json()
    assert root["parents"] == [] and "side_commit" not in root
    assert (
        client.get(f"/documents/1.0/documents/{document_id}/versions/9/commit", headers=editor.headers).status_code
        == 404
    )

    # The diff on any commit of the document: the upload that did not merge against version 1.
    diff = f"/documents/1.0/documents/{document_id}/ifc-diff"
    compared = client.get(f"{diff}?a=1&b={conflict}", headers=editor.headers)
    assert compared.status_code == 200, compared.text
    report = compared.json()
    assert report["a"] == {"document_id": document_id, "version_index": 1, "commit": first_commit(commits)}
    assert report["b"] == {"document_id": document_id, "commit": conflict}, "a commit that is no version"
    assert [element["guid"] for element in report["changed"]] == [WALLS[1]]
    assert client.get(f"{diff}?a=1&b=1", headers=editor.headers).status_code == 400
    assert client.get(f"{diff}?a=1&b={'0' * 40}", headers=editor.headers).status_code == 404
    assert client.get(f"{diff}?a=1&b=--output=x", headers=editor.headers).status_code == 400
    assert client.get(f"{diff}?a=1&b={conflict}", headers=outsider.headers).status_code == 404

    # The conflict's branch goes once it has expired.
    state = client.app.state  # type: ignore[attr-defined]
    monkeypatch.setattr(upkeep_module, "now_iso", lambda: "2999-01-01T00:00:00Z")
    done = client.portal.call(upkeep_module.upkeep, state.git, None)  # type: ignore[union-attr]
    assert done.conflicts == 1
    kinds = sorted(entry["kind"] for entry in client.get(history, headers=editor.headers).json())
    assert kinds == ["proposal", "upload", "version", "version", "version"]
    gone = client.get(f"{diff}?a={conflict}&b=3", headers=editor.headers)
    assert gone.status_code == 404, "an expired conflict is no commit of the document any more"


def first_commit(commits: dict[str, dict[str, Any]]) -> str:
    return next(commit for commit, version in commits.items() if version["version_index"] == 1)


def test_a_server_without_repositories_has_no_history(settings: Settings, clean_database: None) -> None:
    world = build_world(settings)
    with TestClient(create_app(settings, startup_wait_seconds=30)) as client:
        editor = login_as(client, world, "editor")
        web_sign_in(client, world, "editor")
        version = _upload(client, world, editor, {"file_name": "notes.txt"}, b"one\n", "1")
        history = f"/documents/1.0/documents/{version['document_id']}/history"
        assert client.get(history, headers=editor.headers).status_code == 404
        assert client.get("/.well-known/dataregium").json()["extensions"]["git"] == {"enabled": False}
