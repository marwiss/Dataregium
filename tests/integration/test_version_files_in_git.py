# This file was generated with the assistance of an AI coding tool.
"""An old version's file in git alone (ADR 0030, F94 c, fas 5.6): the cleaner's round takes it out of the storage, and
the download, the diff and a merge on it as a base read it back out of the repository, byte for byte. PostgreSQL
alone, as the repositories are (F99 a)."""

from __future__ import annotations

import os
import time

import pytest
from fastapi.testclient import TestClient

from opencde.repos.reading import prune
from opencde.settings import Settings
from opencde.store.documents.postgres import prunable_versions
from tests.conftest import FIXTURES_DIR
from tests.integration.conftest_accounts import World, login_as
from tests.integration.test_git_repos import _upload
from tests.integration.test_proposal_merges import _metadata
from tests.integration.test_web_signin import web_sign_in

pytestmark = [pytest.mark.integration, pytest.mark.parametrize("store_kind", ["postgres"], indirect=True)]
pytest.importorskip("ifcopenshell", reason="the ifc-db extra is not installed")
DATA = (FIXTURES_DIR / "three_walls_gtin.ifc").read_bytes()


def test_an_old_versions_file_is_read_back_out_of_git(merging: tuple[TestClient, World, Settings]) -> None:
    client, world, _ = merging
    editor = login_as(client, world, "editor")
    web_sign_in(client, world, "editor")
    first = _upload(client, world, editor, {"file_name": "model.ifc"}, DATA, "1")
    on_first = {"file_name": "model.ifc", "document_id": first["document_id"], "base_version_index": 1}
    second = _upload(client, world, editor, on_first, DATA.replace(b"'YVT-A'", b"'Main'"), "2")
    download = first["links"]["document_version_download"]["url"]
    before = client.get(download, headers=editor.headers).content
    state = client.app.state  # type: ignore[attr-defined]
    path = state.storage.path(_key(client, first))
    old = time.time() - 3 * 86_400
    for version in (first, second):
        aged = state.storage.path(_key(client, version))
        os.utime(aged, (old, old))

    def candidates(offset: int, limit: int):
        return prunable_versions(state.postgres, offset, limit)

    done = client.portal.call(prune, state.git, state.storage, 1, candidates)  # type: ignore[union-attr]
    assert done.removed == 1 and not path.exists(), "the old version's file, not the latest's"
    assert state.storage.path(_key(client, second)).exists()

    again = client.get(download, headers=editor.headers)
    assert again.status_code == 200 and again.content == before, "byte for byte out of the version's commit"
    assert path.exists(), "and the storage is its cache again"

    os.utime(path, (old, old))
    assert client.portal.call(prune, state.git, state.storage, 1, candidates).removed == 1  # type: ignore[union-attr]
    compared = client.get(
        f"/documents/1.0/documents/{first['document_id']}/versions/1/ifc-diff/2", headers=editor.headers
    )
    assert compared.status_code == 200, compared.text
    assert len(compared.json()["changed"]) == 1, "the wall renamed in version 2"

    os.utime(path, (old, old))
    assert client.portal.call(prune, state.git, state.storage, 1, candidates).removed == 1  # type: ignore[union-attr]
    third = _upload(client, world, editor, on_first, DATA.replace(b"'YVT-B'", b"'Mine'"), "3")
    assert _metadata(client, editor, third)["merged_with_version_index"] == ["2"], "merged on a base out of git"


def _key(client: TestClient, version: dict) -> str:
    """A version's storage key, as the store holds it."""
    state = client.app.state  # type: ignore[attr-defined]
    row = client.portal.call(  # type: ignore[union-attr]
        state.postgres.one,
        "SELECT storage_key FROM document_versions WHERE document_id = $1 AND version_index = $2",
        version["document_id"],
        version["version_index"],
    )
    return str(row["storage_key"])
