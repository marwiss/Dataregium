# This file was generated with the assistance of an AI coding tool.
"""A git repository per project (ADR 0030, UL-48, fas 5.2): each version of a text file is a commit on its project's
main, made in the version's transaction, and a file that is not text gets none. PostgreSQL alone (F99 a)."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from opencde.main import create_app
from opencde.settings import Settings
from tests.integration.conftest_accounts import World, build_world, login_as, login_with_scope
from tests.integration.test_documents_api import save_metadata, start_upload, upload_file
from tests.integration.test_web_signin import web_sign_in

pytestmark = [pytest.mark.integration, pytest.mark.parametrize("store_kind", ["postgres"], indirect=True)]
GIT = shutil.which("git")
if GIT is None:
    pytest.skip("git is not installed", allow_module_level=True)


@pytest.fixture
def git_client(settings: Settings, clean_database: None) -> Iterator[tuple[TestClient, World, Settings]]:
    settings = settings.model_copy(update={"git_enabled": True})
    world = build_world(settings)
    with TestClient(create_app(settings, startup_wait_seconds=30)) as client:
        yield client, world, settings


def _git(settings: Settings, project_id: str, *args: str) -> str:
    repository = settings.data_dir / "git" / f"{project_id}.git"
    run = subprocess.run(  # noqa: S603
        [str(GIT), "--git-dir", str(repository), *args], capture_output=True, check=True
    )
    return run.stdout.decode("utf-8")  # as the bytes are: a text mode would read CRLF as LF


def _upload(client: TestClient, world: World, session: Any, file: dict[str, Any], data: bytes, version: str) -> dict:
    started = start_upload(client, session, [{"session_file_id": "f", **file}])
    instructions = save_metadata(
        client, started["upload_ui_url"], world.project_id, {"title_0": "Plan", "version_0": version}
    )
    plan = client.post(
        instructions, json={"files": [{"session_file_id": "f", "size_in_bytes": len(data)}]}, headers=session.headers
    ).json()
    return upload_file(client, session, plan["documents_to_upload"][0], data)


def _metadata(client: TestClient, session: Any, version: dict[str, Any]) -> dict[str, list[str]]:
    response = client.get(version["links"]["document_version_metadata"]["url"], headers=session.headers)
    return {entry["name"]: entry["value"] for entry in response.json()["metadata"]}


def test_every_version_of_a_text_file_is_a_commit_on_the_projects_main(
    git_client: tuple[TestClient, World, Settings],
) -> None:
    client, world, settings = git_client
    editor = login_as(client, world, "editor")
    web_sign_in(client, world, "editor")
    first = _upload(client, world, editor, {"file_name": "notes.txt"}, b"first\r\n", "1")
    document_id = first["document_id"]
    commit = _metadata(client, editor, first)["git_commit"][0]
    assert len(commit) == 40
    path = f"documents/{document_id}/notes.txt"
    assert _git(settings, world.project_id, "cat-file", "blob", f"{commit}:{path}") == "first\r\n", "the bytes as sent"

    second = _upload(
        client,
        world,
        editor,
        {"file_name": "notes.txt", "document_id": document_id, "base_version_index": 1, "message": "Second\x07 line\n"},
        b"second\n",
        "2",
    )
    again = _metadata(client, editor, second)["git_commit"][0]
    log = _git(settings, world.project_id, "log", "--format=%H %P|%an|%ae|%B", "main")
    assert log.startswith(
        f"{again} {commit}|editor|{world.users['editor']}@users.invalid|Plan: version 2\n\nSecond line\n"
    )
    assert (
        "Dataregium-Document: " + document_id in log and "Dataregium-Version: 2" in log and "Dataregium-Base: 1" in log
    )

    # Another document of the project goes on the same main; a file that is not text gets no commit.
    model = _upload(client, world, editor, {"file_name": "other.json"}, b'{"a": 1}', "1")
    other = _metadata(client, editor, model)["git_commit"][0]
    assert _git(settings, world.project_id, "rev-parse", "main").strip() == other
    assert _git(settings, world.project_id, "rev-parse", f"{other}^").strip() == again
    tree = _git(settings, world.project_id, "ls-tree", "-r", "--name-only", "main").split()
    assert tree == sorted([path, f"documents/{model['document_id']}/data.json"])
    binary = _upload(client, world, editor, {"file_name": "plan.pdf"}, b"%PDF-1.7\n\0\0", "1")
    assert "git_commit" not in _metadata(client, editor, binary)
    assert _git(settings, world.project_id, "rev-parse", "main").strip() == other

    # The repository goes with its project.
    repository = settings.data_dir / "git" / f"{world.project_id}.git"
    admin = login_with_scope(client, world, "admin", "profile admin")
    assert repository.is_dir()
    assert client.delete(f"/admin/api/projects/{world.project_id}", headers=admin.headers).status_code == 204
    assert not repository.exists() and not list((settings.data_dir / "git").glob(".trash-*"))


def test_a_server_whose_data_is_in_the_graph_keeps_no_repositories(settings: Settings) -> None:
    graph = settings.model_copy(update={"git_enabled": True, "store_url": None})
    assert graph.git_on is False and settings.model_copy(update={"git_enabled": True}).git_on is True


def test_a_backup_holds_the_repositories_and_restores_them(
    git_client: tuple[TestClient, World, Settings], tmp_path: Path
) -> None:
    from opencde import backup as backups
    from opencde.bootstrap import make_postgres, make_storage
    from opencde.postgres import tables
    from opencde.repos.git import Git
    from opencde.storage import LocalStorage

    client, world, settings = git_client
    editor = login_as(client, world, "editor")
    web_sign_in(client, world, "editor")
    commit = _metadata(client, editor, _upload(client, world, editor, {"file_name": "notes.txt"}, b"one\n", "1"))
    head = commit["git_commit"][0]

    async def round_trip() -> None:
        postgres = make_postgres(settings)
        assert postgres is not None
        try:
            git = Git(settings.data_dir / "git")
            manifest = await backups.export(None, make_storage(settings), tmp_path / "b", postgres=postgres, git=git)
            assert manifest.repos == 1 and f"git/{world.project_id}.bundle" in manifest.checksums
            await tables.wipe(postgres)
            elsewhere = Git(tmp_path / "again" / "git")
            again = LocalStorage(tmp_path / "again")
            await backups.restore(None, again, tmp_path / "b", postgres=postgres, git=elsewhere)
            assert await elsewhere.head(elsewhere.repository(world.project_id)) == head
            await tables.wipe(postgres)
            with pytest.raises(backups.BackupError, match="repositories"):
                await backups.restore(None, LocalStorage(tmp_path / "third"), tmp_path / "b", postgres=postgres)
        finally:
            await postgres.close()

    asyncio.run(round_trip())
