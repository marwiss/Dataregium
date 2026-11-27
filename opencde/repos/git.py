# This file was generated with the assistance of an AI coding tool.
"""git's own command line in a subprocess, closed off from its surroundings (ADR 0030, UL-48, fas 5.2).

Each project has a bare repository, ``<data_dir>/git/<project_id>.git``, its name built from the project's UUID and
nothing else. Every call gets a list of arguments, never a shell; a closed environment, so that no system or user
configuration, hook, credential prompt or replacement object takes part (``GIT_CONFIG_NOSYSTEM``, an empty
``GIT_CONFIG_GLOBAL``, an empty hooks folder, ``protocol.allow=never``); and a time limit, after which the process is
killed. Blobs are written as the bytes are (``--no-filters``), trees through a temporary index, and refs only with
the value they are expected to have. No value a client sent reaches an argument as a path or a ref.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import shutil
import stat
import sys
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from opencde.ids import is_uuid, normalize_guid

MAIN = "refs/heads/main"
PROPOSALS = "refs/heads/proposals/"
CONFLICTS = "refs/heads/conflicts/"
ZERO = "0" * 40
SHA = re.compile(r"[0-9a-f]{40}")
#: What a repository is set to when it is made: the server keeps it up, and refuses what would rewrite history.
CONFIG = (
    ("gc.auto", "0"),
    ("pack.threads", "1"),
    ("receive.denyNonFastForwards", "true"),
    ("transfer.fsckObjects", "true"),
    ("core.autocrlf", "false"),
)
#: The variables of the server's own environment a git process keeps; Windows needs its system folders to start one.
KEPT = ("PATH", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "TEMP", "TMP")


class GitError(RuntimeError):
    """A git call that failed, ran out of time, or could not start; the message never holds a file's text."""


@dataclass(frozen=True)
class Person:
    """The author or the committer of a commit, and when."""

    name: str
    email: str
    date: str  # ISO 8601 in UTC


def proposal_ref(proposal_id: str) -> str:
    """The branch of a change set's trial (ADR 0030, fas 5.4), named by the server's id of the proposal, never by the
    topic's guid, which a client may choose."""
    if not is_uuid(proposal_id):
        raise ValueError("a proposal's branch is named by its UUID")
    return PROPOSALS + normalize_guid(proposal_id)


def conflict_ref(conflict_id: str) -> str:
    """The branch of an upload that did not merge (F96 b, fas 5.5), named by an id of the server's own."""
    if not is_uuid(conflict_id):
        raise ValueError("a conflict's branch is named by its UUID")
    return CONFLICTS + normalize_guid(conflict_id)


#: A commit's fields as ``log`` reads them, apart by unit separators, each commit ended by a record separator; the
#: server's messages hold no control character but the line break (``writer.clean``).
LOGGED = "%H%x1f%P%x1f%an%x1f%aI%x1f%cn%x1f%B%x1e"


def _logged(out: bytes) -> list[Logged]:
    found: list[Logged] = []
    for record in out.decode("utf-8", "replace").split("\x1e"):
        parts = record.strip("\n").split("\x1f")
        if len(parts) == 6 and SHA.fullmatch(parts[0]):
            commit, parents, author, date, committer, message = parts
            found.append(Logged(commit, tuple(parents.split()), author, date, committer, message))
    return found


@dataclass(frozen=True)
class Logged:
    """A commit as the history lists it."""

    commit: str
    parents: tuple[str, ...]
    author: str
    date: str  # ISO 8601, as git gives the author's date
    committer: str
    message: str


class Git:
    """The projects' repositories under ``root`` (``<data_dir>/git``), written with ``executable``."""

    def __init__(self, root: Path, executable: str = "git", timeout: float = 120.0) -> None:
        self.root = root
        self.executable = executable
        self.timeout = timeout

    # -- the place -----------------------------------------------------------------------------------------------

    def repository(self, project_id: str) -> Path:
        """The project's repository; a value that is not a UUID gives an error, never a path."""
        if not is_uuid(project_id):
            raise ValueError("a project's repository is named by its UUID")
        return self.root / f"{normalize_guid(project_id)}.git"

    def _quiet(self) -> tuple[Path, Path]:
        """The empty configuration file and hooks folder every call points at, made once."""
        home = self.root / ".server"
        hooks = home / "hooks"
        config = home / "config"
        if not hooks.is_dir():
            hooks.mkdir(parents=True, exist_ok=True)
        if not config.exists():
            config.write_bytes(b"")
        return config, hooks

    def environment(self, repository: Path | None, index: Path | None = None) -> dict[str, str]:
        config, _ = self._quiet()
        env = {name: os.environ[name] for name in KEPT if name in os.environ}
        env.update(
            GIT_CONFIG_NOSYSTEM="1",
            GIT_CONFIG_GLOBAL=str(config),
            HOME=str(config.parent),
            GIT_TERMINAL_PROMPT="0",
            GIT_NO_REPLACE_OBJECTS="1",
            GIT_ASKPASS="",
            LC_ALL="C",
        )
        if repository is not None:
            env["GIT_DIR"] = str(repository)
        if index is not None:
            env["GIT_INDEX_FILE"] = str(index)
        return env

    # -- running it ----------------------------------------------------------------------------------------------

    async def run(
        self,
        repository: Path | None,
        *args: str,
        stdin: bytes | None = None,
        index: Path | None = None,
        extra: dict[str, str] | None = None,
        answers: tuple[int, ...] = (0,),
        allow: tuple[str, ...] = (),
        out: Path | None = None,
    ) -> bytes:
        """``git <args>`` in ``repository`` (``GIT_DIR``, never a search upwards). Returns stdout, or with ``out``
        writes it there as it comes, for a blob that may be large."""
        _, hooks = self._quiet()
        command = [
            self.executable,
            "-c",
            f"core.hooksPath={hooks}",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "protocol.allow=never",
            "-c",
            "core.autocrlf=false",
            *(item for name in allow for item in ("-c", f"protocol.{name}.allow=always")),
            *args,
        ]
        env = {**self.environment(repository, index), **(extra or {})}
        with contextlib.ExitStack() as stack:
            target = stack.enter_context(out.open("wb")) if out is not None else asyncio.subprocess.PIPE
            try:
                process = await asyncio.create_subprocess_exec(
                    *command,
                    stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
                    stdout=target,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                )
            except OSError as exc:
                raise GitError(f"git could not start: {type(exc).__name__}") from None
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(stdin), timeout=self.timeout)
            except TimeoutError:
                process.kill()
                await process.wait()
                raise GitError(f"git {args[0]} exceeded {self.timeout} s") from None
        stdout = stdout or b""
        if process.returncode not in answers:
            lines = stderr.decode("utf-8", "replace").strip().splitlines()
            raise GitError(f"git {args[0]}: {lines[-1][:200] if lines else f'exit code {process.returncode}'}")
        return stdout

    async def version(self) -> str:
        """git's version, as ``git --version`` says it."""
        return (await self.run(None, "--version")).decode("ascii", "replace").strip()

    # -- the repository ------------------------------------------------------------------------------------------

    async def ensure(self, project_id: str) -> Path:
        """The project's repository, made when it is missing: in a folder beside it and then renamed, so that half a
        repository is never seen."""
        target = self.repository(project_id)
        if target.is_dir():
            return target
        self.root.mkdir(parents=True, exist_ok=True)
        making = Path(tempfile.mkdtemp(prefix=".new-", dir=self.root))
        try:
            await self.run(None, "init", "--bare", "--quiet", "--initial-branch=main", "--", str(making))
            for name, value in CONFIG:
                await self.run(making, "config", "--local", name, value)
            try:
                os.replace(making, target)
            except OSError:
                if not target.is_dir():  # another writer made it first: theirs is as good
                    raise
        finally:
            remove_tree(making, missing_ok=True)
        return target

    async def head(self, repository: Path) -> str | None:
        """The commit ``main`` names, or None before the first."""
        return await self.ref(repository, MAIN)

    async def ref(self, repository: Path, name: str) -> str | None:
        """The commit a ref of the server's own names, or None."""
        out = await self.run(repository, "rev-parse", "--verify", "--quiet", f"{name}^{{commit}}", answers=(0, 1))
        return out.decode("ascii").strip() or None

    async def has(self, repository: Path, commit: str) -> bool:
        """Whether the repository holds ``commit``."""
        out = await self.run(repository, "cat-file", "-t", commit, answers=(0, 128))
        return out.strip() == b"commit"

    async def blob(self, repository: Path, path: Path) -> str:
        """Write the file at ``path`` as a blob, its bytes as they are; returns its id."""
        out = await self.run(repository, "hash-object", "-w", "--no-filters", "--", str(path))
        return out.decode("ascii").strip()

    async def tree(self, repository: Path, parent: str | None, entries: dict[str, str]) -> str:
        """The tree of ``parent`` (empty without one) with each path of ``entries`` set to its blob."""
        with tempfile.TemporaryDirectory(prefix="index-", dir=self.root) as folder:
            index = Path(folder) / "index"
            if parent is not None:
                await self.run(repository, "read-tree", f"{parent}^{{tree}}", index=index)
            else:
                await self.run(repository, "read-tree", "--empty", index=index)
            listing = "".join(f"100644 {blob}\t{path}\0" for path, blob in sorted(entries.items()))
            await self.run(
                repository, "update-index", "--add", "-z", "--index-info", stdin=listing.encode(), index=index
            )
            return (await self.run(repository, "write-tree", index=index)).decode("ascii").strip()

    async def tree_without(self, repository: Path, commit: str, paths: list[str]) -> str:
        """The tree of ``commit`` with ``paths`` left out (a forge's mirror, ADR 0032)."""
        with tempfile.TemporaryDirectory(prefix="index-", dir=self.root) as folder:
            index = Path(folder) / "index"
            await self.run(repository, "read-tree", f"{commit}^{{tree}}", index=index)
            listing = "".join(f"0 {ZERO}\t{path}\0" for path in paths)  # mode 0: the path goes
            await self.run(repository, "update-index", "-z", "--index-info", stdin=listing.encode(), index=index)
            return (await self.run(repository, "write-tree", index=index)).decode("ascii").strip()

    async def commit(
        self, repository: Path, tree: str, parents: list[str], author: Person, committer: Person, message: str
    ) -> str:
        """A commit of ``tree`` on ``parents``; the message goes on stdin, as it may be long."""
        arguments = ["commit-tree", tree]
        for parent in parents:
            arguments += ["-p", parent]
        extra = {
            "GIT_AUTHOR_NAME": author.name,
            "GIT_AUTHOR_EMAIL": author.email,
            "GIT_AUTHOR_DATE": author.date,
            "GIT_COMMITTER_NAME": committer.name,
            "GIT_COMMITTER_EMAIL": committer.email,
            "GIT_COMMITTER_DATE": committer.date,
        }
        out = await self.run(repository, *arguments, stdin=message.encode("utf-8"), extra=extra)
        return out.decode("ascii").strip()

    async def move_main(self, repository: Path, new: str, old: str | None) -> None:
        """Point ``main`` at ``new`` only while it still names ``old`` (none: while it does not exist)."""
        await self.move_ref(repository, MAIN, new, old)

    async def move_ref(self, repository: Path, name: str, new: str, old: str | None) -> None:
        """Point a ref at ``new`` only while it still names ``old`` (none: while it does not exist)."""
        await self.run(repository, "update-ref", name, new, old or ZERO)

    async def delete_ref(self, repository: Path, name: str, old: str) -> None:
        """Take a ref away while it still names ``old``; its commits stay while another ref reaches them."""
        await self.run(repository, "update-ref", "-d", name, old)

    async def proposals(self, repository: Path) -> dict[str, str]:
        """The proposals' branches (``proposal_ref``): each proposal's id and the commit its branch names."""
        return await self._branches(repository, PROPOSALS)

    async def conflicts(self, repository: Path) -> dict[str, str]:
        """The branches of uploads that did not merge (``conflict_ref``), by their ids."""
        return await self._branches(repository, CONFLICTS)

    async def _branches(self, repository: Path, prefix: str) -> dict[str, str]:
        out = await self.run(repository, "for-each-ref", "--format=%(refname) %(objectname)", prefix)
        found: dict[str, str] = {}
        for line in out.decode("ascii", "replace").splitlines():
            name, _, commit = line.partition(" ")
            named = name.removeprefix(prefix)
            if name.startswith(prefix) and is_uuid(named):
                found[named] = commit
        return found

    async def log(self, repository: Path, path: str, *, skip: int, limit: int) -> list[Logged]:
        """The commits on any of the server's branches that change ``path``, newest first, sides of merges too."""
        out = await self.run(
            repository,
            "log",
            "--branches",
            "--full-history",
            f"--format={LOGGED}",
            f"--skip={int(skip)}",
            f"--max-count={int(limit)}",
            "--",
            path,
        )
        return _logged(out)

    async def show(self, repository: Path, commit: str) -> Logged | None:
        """One commit as the history lists it; None for a value that is not a commit of the repository."""
        if not SHA.fullmatch(commit) or not await self.has(repository, commit):
            return None
        found = _logged(await self.run(repository, "log", "-1", f"--format={LOGGED}", commit))
        return found[0] if found else None

    async def count(self, repository: Path, path: str) -> int:
        """How many commits ``log`` lists for ``path``."""
        out = await self.run(repository, "rev-list", "--count", "--branches", "--full-history", "--", path)
        return int(out.strip() or b"0")

    async def holds(self, repository: Path, commit: str, path: str) -> bool:
        """Whether ``commit`` is a commit on one of the server's branches whose tree has ``path``: one a branch no
        longer reaches, such as an expired conflict's, holds nothing; a value that is not a commit's 40 hexadecimal
        characters never reaches git."""
        if not SHA.fullmatch(commit):
            return False
        out = await self.run(repository, "cat-file", "-t", f"{commit}:{path}", answers=(0, 128))
        if out.strip() != b"blob":
            return False
        reached = await self.run(
            repository, "for-each-ref", "--count=1", f"--contains={commit}", "--format=%(refname)", "refs/heads/"
        )
        return bool(reached.strip())

    async def blob_to(self, repository: Path, commit: str, path: str, target: Path) -> None:
        """Write the file ``path`` at ``commit`` to ``target``, as it comes."""
        await self.run(repository, "cat-file", "blob", f"{commit}:{path}", out=target)

    async def message(self, repository: Path, commit: str) -> str:
        """A commit's whole message."""
        return (await self.run(repository, "log", "-1", "--format=%B", commit)).decode("utf-8", "replace")

    def projects(self) -> list[str]:
        """The projects that have a repository, by the names of their folders: a UUID's alone."""
        if not self.root.is_dir():
            return []
        return sorted(
            entry.name.removesuffix(".git")
            for entry in self.root.iterdir()
            if entry.is_dir() and entry.name.endswith(".git") and is_uuid(entry.name.removesuffix(".git"))
        )

    async def bundle(self, project_id: str, target: Path) -> bool:
        """Write the project's repository as one bundle file, every ref; False for one without a commit yet."""
        repository = self.repository(project_id)
        if await self.head(repository) is None:
            return False
        await self.run(repository, "bundle", "create", "--quiet", str(target), "--all")
        return True

    async def unbundle(self, project_id: str, source: Path) -> Path:
        """Make the project's repository from a bundle: verified first, then cloned bare beside it and renamed, as
        ``ensure`` makes one; it must not exist yet."""
        target = self.repository(project_id)
        if target.exists():
            raise GitError("the project already has a repository")
        await self.run(None, "bundle", "verify", "--quiet", str(source))
        self.root.mkdir(parents=True, exist_ok=True)
        making = Path(tempfile.mkdtemp(prefix=".new-", dir=self.root))
        try:
            remove_tree(making)
            await self.run(  # a bundle is read through the file transport, which every other call refuses
                None, "clone", "--bare", "--quiet", "--no-hardlinks", "--", str(source), str(making), allow=("file",)
            )
            await self.run(making, "remote", "remove", "origin", answers=(0, 2))
            for name, value in CONFIG:
                await self.run(making, "config", "--local", name, value)
            os.replace(making, target)
        finally:
            remove_tree(making, missing_ok=True)
        return target

    async def read(self, repository: Path, commit: str, path: str) -> bytes:
        """The bytes of ``path`` at ``commit``."""
        return await self.run(repository, "cat-file", "blob", f"{commit}:{path}")


def remove_tree(path: Path, missing_ok: bool = False) -> None:
    """Remove a folder git wrote, whose objects it made read-only (Windows refuses to remove those as they are)."""
    if missing_ok and not path.exists():
        return

    def writable(function, name, *_):
        os.chmod(name, stat.S_IWRITE)  # clears the read-only flag, as Python's documentation of rmtree shows
        function(name)

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=writable)
    else:  # pragma: no cover
        shutil.rmtree(path, onerror=writable)


def remove_repository(root: Path, project_id: str) -> bool:
    """Take a deleted project's repository away, if it had one: moved aside first, so that no reader sees half of
    one, then removed. Returns False when some of it stayed (a file another process holds open on Windows), for the
    cleaner to take later."""
    repository = Git(root).repository(project_id)
    if not repository.is_dir():
        return True
    aside = trash(root, repository)
    try:
        remove_tree(aside)
    except OSError:
        return False
    return True


def trash(root: Path, repository: Path) -> Path:
    """Move a repository aside before it is removed, so that no reader sees half of one."""
    aside = root / f".trash-{repository.stem}-{uuid.uuid4()}"
    os.replace(repository, aside)
    return aside
