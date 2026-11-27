# This file was generated with the assistance of an AI coding tool.
"""A project's repository mirrored to a forge (ADR 0032, F206 c-F209 c): off by default, connected by a manager who
acknowledges that the files leave the server, the token never shown again, pushed in the cleaner's round and gone
when it expires. The forge here is a folder; PostgreSQL alone, as the repositories are (F99 a)."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from opencde.accounts import routes_remote
from opencde.admin.doctor import git_remotes_check
from opencde.main import create_app
from opencde.postgres.cleanup import delete_expired
from opencde.repos import remote
from opencde.settings import Settings
from opencde.store.accounts.remotes import PostgresRemotes
from opencde.store.logbook.postgres import audit_log_store
from tests.conftest import FIXTURES_DIR
from tests.integration.conftest_accounts import World, build_world, login_as, login_with_scope
from tests.integration.test_ai_policy import audited
from tests.integration.test_git_repos import _upload

pytestmark = [pytest.mark.integration, pytest.mark.parametrize("store_kind", ["postgres"], indirect=True)]
GIT = shutil.which("git")
if GIT is None:
    pytest.skip("git is not installed", allow_module_level=True)
DATA = (FIXTURES_DIR / "three_walls_gtin.ifc").read_bytes()
TOKEN = "github_pat_0123456789abcdefghij"  # nosec B105: a test's own
URL = "https://github.com/owner/plans.git"


@pytest.fixture
def mirroring(settings: Settings, clean_database: None) -> Iterator[tuple[TestClient, World, Settings]]:
    settings = settings.model_copy(
        update={
            "git_enabled": True,
            "git_remotes_enabled": True,
            "git_remote_hosts": ["github.com"],
            "cleanup_interval_seconds": 3600,
        }
    )
    world = build_world(settings)
    with TestClient(create_app(settings, startup_wait_seconds=30)) as client:
        yield client, world, settings


def _round(client: TestClient, settings: Settings) -> int:
    state: Any = client.app.state  # type: ignore[attr-defined]
    remotes = PostgresRemotes(state.postgres)
    secret = settings.secret_key.get_secret_value()
    audit_log = audit_log_store(state.store, state.postgres)
    hosts = settings.git_remote_hosts
    return client.portal.call(remote.push_round, state.git, remotes, secret, audit_log, hosts)  # type: ignore[union-attr]


def test_a_manager_mirrors_the_projects_repository_to_a_forge(
    mirroring: tuple[TestClient, World, Settings], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, world, settings = mirroring
    address = f"/manage/1.0/projects/{world.project_id}/remote"
    manager = login_with_scope(client, world, "manager", "profile bcf documents project")
    editor = login_as(client, world, "editor")
    capabilities = client.get("/.well-known/dataregium").json()["extensions"]["git"]["remote"]
    assert capabilities == {"enabled": True, "url": "/manage/1.0/projects/{project_id}/remote", "hosts": ["github.com"]}
    assert client.get(address, headers=manager.headers).status_code == 404, "not connected yet"

    body = {"url": URL, "token": TOKEN, "acknowledged": True}
    for wrong, field in (({"acknowledged": False}, None), ({"url": "http://github.com/o/r.git"}, "https")):
        refused = client.put(address, json={**body, **wrong}, headers=manager.headers)
        assert refused.status_code in (400, 422) and TOKEN not in refused.text, refused.text
        if field:
            assert field in refused.json()["message"]
    assert client.put(address, json={**body, "token": "short"}, headers=manager.headers).status_code in (400, 422)
    assert (
        client.put(address, json={**body, "expires_at": "2999-01-01T00:00:00Z"}, headers=manager.headers).status_code
        == 400
    )
    assert client.put(address, json=body, headers=editor.headers).status_code == 403, "a manager's alone"

    forge = tmp_path / "forge.git"
    subprocess.run([str(GIT), "init", "--bare", "--quiet", str(forge)], check=True)  # noqa: S603
    monkeypatch.setattr(routes_remote, "check_url", lambda url, hosts: forge.as_posix())
    monkeypatch.setattr(remote, "check_url", lambda url, hosts: url)
    monkeypatch.setattr(remote, "PROTOCOLS", ("file",))
    connected = client.put(address, json=body, headers=manager.headers)
    assert connected.status_code == 200, connected.text
    shown = connected.json()
    assert shown["status"] == "waiting" and shown["max_file_bytes"] == 100 * 1024 * 1024
    assert TOKEN not in connected.text and "token" not in shown, "never shown again"
    row = client.portal.call(  # type: ignore[union-attr]
        client.app.state.postgres.one,  # type: ignore[attr-defined]
        "SELECT token FROM project_remotes WHERE project_id = $1",
        world.project_id,
    )
    assert row is not None and TOKEN not in row["token"], "kept sealed"

    version = _upload(client, world, editor, {"file_name": "model.ifc"}, DATA, "1")
    assert _round(client, settings) == 1
    status = client.get(address, headers=manager.headers).json()
    assert status["status"] == "pushed" and status["pushed"] == ["refs/heads/main"] and status["left_out"] == []
    head = (
        subprocess.run(  # noqa: S603
            [str(GIT), "--git-dir", str(forge), "rev-parse", "refs/heads/main"], capture_output=True, check=True
        )
        .stdout.decode("ascii")
        .strip()
    )
    assert len(head) == 40 and version["version_index"] == 1
    assert _round(client, settings) == 0, "nothing new, nothing pushed"
    doctor = client.portal.call(git_remotes_check, client.app.state.postgres)  # type: ignore[union-attr,attr-defined]
    assert doctor.ok and doctor.params == {"projects": 1, "failing": 0, "left_out": 0, "files": "-"}
    events = [details for _, details in audited(client, world, "git.remote")]
    assert events and events[0]["project_id"] == world.project_id
    assert not any(TOKEN in str(details) for details in events)
    assert [details["url"] for _, details in audited(client, world, "manage.remote.set")] == [forge.as_posix()]

    # The connection goes with the token's time, and a manager can end it before.
    assert client.delete(address, headers=manager.headers).status_code == 204
    assert client.get(address, headers=manager.headers).status_code == 404
    assert client.put(address, json=body, headers=manager.headers).status_code == 200
    gone = client.portal.call(delete_expired, client.app.state.postgres, "2999-01-01T00:00:00Z")  # type: ignore[union-attr,attr-defined]
    assert gone >= 1 and client.get(address, headers=manager.headers).status_code == 404


def test_the_mirror_is_off_by_default(settings: Settings, clean_database: None) -> None:
    settings = settings.model_copy(update={"git_enabled": True})
    world = build_world(settings)
    with TestClient(create_app(settings, startup_wait_seconds=30)) as client:
        manager = login_with_scope(client, world, "manager", "profile project")
        address = f"/manage/1.0/projects/{world.project_id}/remote"
        assert client.get("/.well-known/dataregium").json()["extensions"]["git"]["remote"] == {"enabled": False}
        assert client.get(address, headers=manager.headers).status_code == 404
        put = client.put(address, json={"url": URL, "token": TOKEN, "acknowledged": True}, headers=manager.headers)
        assert put.status_code == 404
