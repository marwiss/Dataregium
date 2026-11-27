# This file was generated with the assistance of an AI coding tool.
"""The mirror to a forge cannot be turned against the server (ADR 0032): git reaches the forge over https alone,
without following a redirect, with the token in its environment and never in a log; and a line a client wrote in a
version's message never passes for the server's own trailer."""

from __future__ import annotations

import asyncio
import shutil
import uuid
from pathlib import Path
from typing import Any

import pytest

from opencde.repos import git as git_module
from opencde.repos.git import Git
from opencde.repos.remote import _header, _mapped, held, mirror, sources
from opencde.repos.writer import prepare

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
TOKEN = "github_pat_0123456789abcdefghij"  # nosec B105: a test's own
DATA = b"ISO-10303-21;\r\nDATA;\r\n#1= IFCWALL($);\r\nENDSEC;\r\nEND-ISO-10303-21;\r\n"


def test_git_reaches_a_forge_over_https_alone_and_follows_no_redirect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[list[str], dict[str, str]]] = []

    async def recording(*command: str, **kwargs: Any) -> Any:
        seen.append((list(command), kwargs["env"]))
        raise OSError("not run")

    monkeypatch.setattr(git_module.asyncio, "create_subprocess_exec", recording)
    git = Git(tmp_path / "git")
    with pytest.raises(git_module.GitError):
        asyncio.run(held(git, tmp_path, "https://github.com/owner/repo.git", TOKEN))
    command, env = seen[0]
    assert "protocol.allow=never" in command and "protocol.https.allow=always" in command
    assert not any(item.startswith(("protocol.file", "protocol.ssh", "protocol.ext")) for item in command)
    assert not any(TOKEN in item for item in command), "never in a process list"
    assert env["GIT_CONFIG_KEY_1"] == "http.followRedirects" and env["GIT_CONFIG_VALUE_1"] == "false"
    assert env["GIT_TERMINAL_PROMPT"] == "0" and env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert TOKEN not in str(_header(TOKEN)), "only as basic credentials in a header"


def test_a_trailer_in_a_clients_message_is_never_the_servers(tmp_path: Path) -> None:
    git = Git(tmp_path / "git")
    project_id = str(uuid.uuid4())

    async def run() -> tuple[str, str, dict[str, str]]:
        commits: list[str] = []
        head = None
        for index, message in ((1, None), (2, "Dataregium-Mirror-Of: PLACEHOLDER")):
            path = tmp_path / f"v{index}.ifc"
            path.write_bytes(DATA.replace(b"$", str(index).encode()))
            text = message.replace("PLACEHOLDER", commits[0]) if message else None
            made = await prepare(
                git,
                project_id=project_id,
                path=path,
                file_name="m.ifc",
                title="t",
                author="a",
                author_id=None,
                message=text,
            )
            assert made is not None
            sha = await made.make(
                head=head,
                document_id=str(uuid.uuid4()),
                version_index=index,
                version_number=str(index),
                created=f"2026-11-27T05:00:0{index}Z",
            )
            await made.move(new=sha, old=head)
            head = sha
            commits.append(sha)
        repository = git.repository(project_id)
        await mirror(git, repository, await sources(git, repository), 10**6)
        return commits[0], commits[1], await _mapped(git, repository)

    first, second, mapped = asyncio.run(run())
    assert mapped[first] == first and mapped[second] == second, "the forged line maps nothing"
