# This file was generated with the assistance of an AI coding tool.
"""A project's repository mirrored to a forge (ADR 0032, F206 c-F209 c): only https to a host the server names, never
to a private address; the token sealed and given to git through its environment; main and the proposals' branches
pushed one way, a large file left out of a commit of the mirror's own, and a change made at the forge left alone."""

from __future__ import annotations

import shutil
import socket
import subprocess
import uuid
from pathlib import Path
from typing import Any

import pytest

from opencde.repos import remote
from opencde.repos.git import MAIN, Git, proposal_ref
from opencde.repos.remote import RemoteRefused, check_url, held, mirror, push, seal, sources, unseal
from opencde.repos.writer import prepare, trailers
from opencde.settings import Settings

GIT = shutil.which("git")
needs_git = pytest.mark.skipif(GIT is None, reason="git is not installed")
SMALL = b"ISO-10303-21;\r\nDATA;\r\n#1= IFCWALL($);\r\nENDSEC;\r\nEND-ISO-10303-21;\r\n"
LARGE = b"ISO-10303-21;\r\nDATA;\r\n" + b"#2= IFCSLAB($);\r\n" * 200 + b"ENDSEC;\r\nEND-ISO-10303-21;\r\n"
LIMIT = 1000
TOKEN = "github_pat_0123456789abcdefghij"  # nosec B105: a test's own


def _addresses(*found: str) -> Any:
    def lookup(host: str, port: int, *args: Any, **kwargs: Any) -> list[Any]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port)) for address in found]

    return lookup


def test_only_an_https_address_at_a_named_host_on_a_public_network_is_taken(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _addresses("140.82.121.4"))
    hosts = ["github.com"]
    assert check_url("https://GitHub.com/owner/repo.git/", hosts) == "https://github.com/owner/repo.git"
    for refused in (
        "http://github.com/owner/repo.git",
        "ssh://git@github.com/owner/repo.git",
        "https://owner:secret@github.com/owner/repo.git",
        "https://github.com/owner/repo.git?token=x",
        "https://github.com/",
        "https://github.com/owner/../other.git",
        "https://github.com:8443/owner/repo.git",
        "https://gitlab.com/owner/repo.git",
        "https://github.com.example.org/owner/repo.git",
    ):
        with pytest.raises(RemoteRefused):
            check_url(refused, hosts)
    for private in ("10.0.0.5", "127.0.0.1", "169.254.169.254", "192.168.1.2", "::1", "fd00::1"):
        monkeypatch.setattr(socket, "getaddrinfo", _addresses("140.82.121.4", private))
        with pytest.raises(RemoteRefused, match="private network"):
            check_url("https://github.com/owner/repo.git", hosts)


def test_a_token_is_sealed_with_the_servers_secret_and_never_kept_as_it_came() -> None:
    secret = "a" * 48
    first, second = seal(secret, TOKEN), seal(secret, TOKEN)
    assert first != second and TOKEN not in first, "a nonce of its own each time"
    assert unseal(secret, first) == TOKEN
    with pytest.raises(RemoteRefused, match="another secret"):
        unseal("b" * 48, first)


def test_only_the_last_paragraph_of_a_message_holds_trailers() -> None:
    message = (
        "Plan: version 2\n\nDataregium-Mirror-Of: " + "0" * 40 + "\n\nDataregium-Document: d\nDataregium-Version: 2\n"
    )
    assert trailers(message) == {"Document": "d", "Version": "2"}, "a client's line above is not the server's"


def _settings(tmp_path: Path, **values: Any) -> Settings:
    values = {
        "data_dir": tmp_path / "data",
        "environment": "development",
        "store_url": "postgresql://u@db:5432/d",
        **values,
    }
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def test_the_mirror_is_off_without_hosts_and_the_hosts_are_names(tmp_path: Path) -> None:
    on = _settings(tmp_path, git_enabled=True, git_remotes_enabled=True)
    assert not on.git_remotes_on, "no forge until the administrator names one"
    assert on.model_copy(update={"git_remote_hosts": ["github.com"]}).git_remotes_on
    assert not _settings(tmp_path, git_remotes_enabled=True, git_remote_hosts=["github.com"]).git_remotes_on
    assert _settings(tmp_path, git_remote_hosts=[" GitHub.com "]).git_remote_hosts == ["github.com"]
    for wrong in ("https://github.com", "github.com:443", "localhost", "a b.se"):
        with pytest.raises(ValueError, match="names hosts"):
            _settings(tmp_path, git_remote_hosts=[wrong])


# -- the mirror and the push, to a forge in a folder ----------------------------------------------------------------


class Server:
    """A project's repository with versions committed as the server commits them, and a forge as a bare folder."""

    def __init__(self, tmp_path: Path) -> None:
        self.git = Git(tmp_path / "git")
        self.project_id = str(uuid.uuid4())
        self.tmp_path = tmp_path
        self.head: str | None = None
        self.index = 0
        self.forge = tmp_path / "forge.git"
        subprocess.run([str(GIT), "init", "--bare", "--quiet", str(self.forge)], check=True)  # noqa: S603
        self.url = self.forge.as_posix()

    @property
    def repository(self) -> Path:
        return self.git.repository(self.project_id)

    async def version(self, data: bytes, document: str = "d1", file_name: str = "m.ifc") -> str:
        self.index += 1
        path = self.tmp_path / f"v{self.index}.ifc"
        path.write_bytes(data)
        made = await prepare(
            self.git, project_id=self.project_id, path=path, file_name=file_name, title="t", author="a", author_id=None
        )
        assert made is not None
        document_id = str(uuid.UUID(int=hash(document) % 2**64))
        sha = await made.make(
            head=self.head,
            document_id=document_id,
            version_index=self.index,
            version_number=str(self.index),
            created=f"2026-11-27T05:00:{self.index:02d}Z",
        )
        await made.move(new=sha, old=self.head)
        self.head = sha
        return sha

    def at_forge(self, *args: str) -> str:
        run = subprocess.run([str(GIT), "--git-dir", str(self.forge), *args], capture_output=True, check=True)  # noqa: S603
        return run.stdout.decode("utf-8").strip()

    async def round(self, pushed: dict[str, str]) -> tuple[remote.Mirrored, dict[str, str], list[str]]:
        mirrored = await mirror(self.git, self.repository, await sources(self.git, self.repository), LIMIT)
        after, changed = await push(self.git, self.repository, self.url, TOKEN, mirrored, pushed)
        return mirrored, after, changed


@pytest.fixture
def server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Server:
    monkeypatch.setattr(remote, "PROTOCOLS", ("file",))
    return Server(tmp_path)


@needs_git
async def test_a_history_with_no_large_file_is_mirrored_as_itself_and_the_token_is_never_an_argument(
    server: Server, monkeypatch: pytest.MonkeyPatch
) -> None:
    await server.version(SMALL)
    head = await server.version(SMALL.replace(b"$", b"'A'"))
    calls: list[tuple[tuple[str, ...], dict[str, str]]] = []
    run = server.git.run

    async def watched(repository: Path | None, *args: str, **kwargs: Any) -> bytes:
        calls.append((args, kwargs.get("extra") or {}))
        return await run(repository, *args, **kwargs)

    monkeypatch.setattr(server.git, "run", watched)
    mirrored, after, changed = await server.round({})
    assert mirrored.refs == {MAIN: head} and after == {MAIN: head} and not changed and not mirrored.left_out
    assert server.at_forge("rev-parse", "refs/heads/main") == head
    for args, extra in calls:
        assert not any(TOKEN in arg for arg in args), "never in a process list"
        if args[0] in ("push", "ls-remote"):
            assert extra["GIT_CONFIG_KEY_0"] == "http.extraHeader" and TOKEN not in extra["GIT_CONFIG_VALUE_0"]
    again = await server.round(after)
    assert again[1] == after and not again[2], "nothing new: nothing pushed"


@needs_git
async def test_a_large_file_is_left_out_of_a_commit_of_the_mirrors_own_and_the_next_goes_on_from_it(
    server: Server,
) -> None:
    large = await server.version(LARGE, document="big", file_name="big.ifc")
    await server.version(SMALL)
    mirrored, after, _ = await server.round({})
    assert [entry["commit"] for entry in mirrored.left_out] == [large]
    gone = mirrored.left_out[0]["path"]
    assert mirrored.left_out[0]["size"] == len(LARGE) and gone.startswith("documents/")
    tip = after[MAIN]
    assert tip != server.head
    files = server.at_forge("ls-tree", "-r", "--name-only", tip).splitlines()
    assert len(files) == 1 and files[0] != gone, "the large file is not at the forge"
    message = server.at_forge("log", "-1", "--format=%B", tip)
    assert trailers(message)["Mirror-Of"] == server.head and trailers(message)["Version"] == "2"
    first = server.at_forge("log", "--format=%B", "--reverse", tip).split("Dataregium-Left-Out: ")[1]
    assert first.startswith(f"{gone} ({len(LARGE)} bytes)"), "the commit that held it says so"
    assert server.at_forge("log", "-1", "--format=%an %aI", tip) == "a 2026-11-27T05:00:02Z"

    newest = await server.version(SMALL.replace(b"$", b"'B'"))
    mirrored, later, changed = await server.round(after)
    assert [entry["commit"] for entry in mirrored.left_out] == [newest] and not changed, "the old are not made again"
    assert server.at_forge("rev-parse", f"{later[MAIN]}^") == tip, "it goes on from the last"
    assert trailers(server.at_forge("log", "-1", "--format=%B", later[MAIN]))["Mirror-Of"] == newest


@needs_git
async def test_a_change_made_at_the_forge_is_left_as_it_is_and_reported(server: Server) -> None:
    await server.version(SMALL)
    _, after, _ = await server.round({})
    tree = server.at_forge("rev-parse", f"{after[MAIN]}^{{tree}}")
    made = ("-c", "user.name=x", "-c", "user.email=x@x", "commit-tree", tree, "-p", after[MAIN], "-m", "at the forge")
    theirs = server.at_forge(*made)
    server.at_forge("update-ref", "refs/heads/main", theirs)
    await server.version(SMALL.replace(b"$", b"'C'"))
    _, later, changed = await server.round(after)
    assert changed == [MAIN] and later[MAIN] == theirs
    assert server.at_forge("rev-parse", "refs/heads/main") == theirs, "never overwritten"


@needs_git
async def test_a_proposals_branch_is_pushed_and_taken_away_with_it(server: Server) -> None:
    head = await server.version(SMALL)
    ref = proposal_ref(str(uuid.uuid4()))
    await server.git.move_ref(server.repository, ref, head, None)
    _, after, _ = await server.round({})
    assert after == {MAIN: head, ref: head}
    assert (await held(server.git, server.repository, server.url, TOKEN))[ref] == head
    await server.git.delete_ref(server.repository, ref, head)
    _, later, _ = await server.round(after)
    assert later == {MAIN: head}
    assert ref not in await held(server.git, server.repository, server.url, TOKEN)
