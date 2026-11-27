# This file was generated with the assistance of an AI coding tool.
"""No value a client sent reaches git's arguments as a path, a ref or an option (ADR 0030, UL-48): a version's commit
is made from the server's own values, and the client's file name, title and message go on stdin as data."""

from __future__ import annotations

import asyncio
import shutil
import uuid
from pathlib import Path
from typing import Any

import pytest

from opencde.repos import git as git_module
from opencde.repos.git import Git
from opencde.repos.writer import prepare

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
HOSTILE = "--upload-pack=touch /tmp/x ../../etc/passwd\nrefs/heads/main HEAD~1 @{-1} -c core.pager=sh"


def test_the_clients_values_never_reach_gits_arguments(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []
    real = asyncio.create_subprocess_exec

    async def recording(*command: str, **kwargs: Any) -> Any:
        seen.append(list(command))
        return await real(*command, **kwargs)

    monkeypatch.setattr(git_module.asyncio, "create_subprocess_exec", recording)
    file = tmp_path / "upload"
    file.write_bytes(b"text\n")

    async def run() -> str:
        git = Git(tmp_path / "git")
        commit = await prepare(
            git,
            project_id=str(uuid.uuid4()),
            path=file,
            file_name=f"{HOSTILE}.txt",
            title=HOSTILE,
            author=HOSTILE,
            author_id=str(uuid.uuid4()),
            message=HOSTILE,
        )
        assert commit is not None
        made = await commit.make(
            head=None,
            document_id=str(uuid.uuid4()),
            version_index=1,
            version_number=HOSTILE,
            created="2026-11-27T05:00:00Z",
        )
        await commit.move(new=made, old=None)
        return (await git.run(commit.repository, "log", "-1", "--format=%an%n%B", made)).decode()

    log = asyncio.run(run())
    pieces = ("--upload-pack", "../../etc", "core.pager", "refs/heads/main HEAD", "@{-1}", "HOSTILE")
    for command in seen:
        assert not any(piece in argument for argument in command for piece in pieces), command
    assert "--upload-pack=touch" in log and "-c core.pager=sh" in log, "the text is in the commit, as data"


def test_a_proposals_branch_is_named_by_the_servers_id_alone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fas 5.4: the branch of a trial is named by the proposal's UUID, never by a topic's guid; the proposer, the
    title and the approver go into the commits as data."""
    from opencde.repos.git import proposal_ref
    from opencde.repos.writer import propose

    for hostile in ("../main", "main", "x\ny", HOSTILE, ""):
        with pytest.raises(ValueError):
            proposal_ref(hostile)
    seen: list[list[str]] = []
    real = asyncio.create_subprocess_exec

    async def recording(*command: str, **kwargs: Any) -> Any:
        seen.append(list(command))
        return await real(*command, **kwargs)

    monkeypatch.setattr(git_module.asyncio, "create_subprocess_exec", recording)
    file = tmp_path / "model.ifc"
    file.write_bytes(b"ISO-10303-21;\nDATA;\n#1=IFCWALL($);\nENDSEC;\nEND-ISO-10303-21;\n")
    project_id, document_id = str(uuid.uuid4()), str(uuid.uuid4())

    async def run() -> str:
        git = Git(tmp_path / "git")
        base = await prepare(
            git, project_id=project_id, path=file, file_name="m.ifc", title="t", author="a", author_id=None
        )
        assert base is not None
        made = await base.make(
            head=None, document_id=document_id, version_index=1, version_number="1", created="2026-11-27T05:00:00Z"
        )
        await base.move(new=made, old=None)
        branch = await propose(
            git,
            project_id=project_id,
            proposal_id=str(uuid.uuid4()),
            path=file,
            document_id=document_id,
            file_name="m.ifc",
            title=HOSTILE,
            base=1,
            base_commit=made,
            author=HOSTILE,
            author_id=None,
            created="2026-11-27T05:00:00Z",
        )
        assert branch is not None
        approved = await prepare(
            git, project_id=project_id, path=file, file_name="m.ifc", title=HOSTILE, author=HOSTILE, author_id=None
        )
        assert approved is not None
        approved.proposal_commit, approved.approver = branch, HOSTILE
        merged = await approved.make(
            head=made, document_id=document_id, version_index=2, version_number="2", created="2026-11-27T05:00:00Z"
        )
        return (await git.run(approved.repository, "log", "-1", "--format=%cn%n%B", merged)).decode()

    log = asyncio.run(run())
    pieces = ("--upload-pack", "../../etc", "core.pager", "refs/heads/main HEAD", "@{-1}")
    for command in seen:
        assert not any(piece in argument for argument in command for piece in pieces), command
    assert "Dataregium-Approved-By: --upload-pack=touch" in log, "the approver is in the commit, as data"
