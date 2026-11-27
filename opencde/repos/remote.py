# This file was generated with the assistance of an AI coding tool.
"""A project's repository mirrored to a forge, GitHub, Gitea, GitLab or another (ADR 0032, NY-05, F206 c-F209 c).

Off unless ``OPENCDE_GIT_REMOTES_ENABLED`` is set (F209 c), and then only to a forge whose host the server names
(``OPENCDE_GIT_REMOTE_HOSTS``), over https, with no sign-in in the address and never to a private address. A project's
manager connects it with a fine-grained token for that one repository (F206 c), which is kept sealed with a key
derived from the server's secret (``seal``) and given to git through the environment, never in its arguments.

The mirror goes one way (F207 c): main and the proposals' branches are pushed, each with ``--force-with-lease`` on
what the forge held when it was read, and only where that is what the server pushed last or an older commit of the
server's own: a change someone made at the forge is reported, never overwritten. A commit that holds a file above the
connection's limit (F208 c) is mirrored as a commit of its own without that file, with the same author, time and
message and the trailers ``Dataregium-Left-Out`` and ``Dataregium-Mirror-Of``, and so are the commits after it; a
history with nothing to leave out is mirrored as itself. The mirror's commits are kept under ``refs/mirror/``, so that
each round goes on from the last.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import socket
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from opencde import audit
from opencde.logging import get_logger
from opencde.repos.git import MAIN, PROPOSALS, SHA, Git, GitError
from opencde.repos.writer import trailers
from opencde.timeutil import later_iso, now_iso

if TYPE_CHECKING:
    from pathlib import Path

    from opencde.store.accounts.remotes import PostgresRemotes
    from opencde.store.protocols import AuditLogStore

log = get_logger(__name__)

MIRROR = "refs/mirror/"
#: The protocols a push may use; a test widens it to a folder.
PROTOCOLS: tuple[str, ...] = ("https",)
RETRY_SECONDS = 3600  # after a failed attempt, the next waits this long
PUSH_SECONDS = 3600  # a first push of a large repository takes a while
MOST_PER_ROUND = 20  # connections attempted in one round
_INFO = b"dataregium-forge-token-v1"
_NONCE = 12


class RemoteRefused(ValueError):
    """An address or a token the server does not take; the text never holds the token."""


def check_url(url: str, hosts: list[str]) -> str:
    """The forge's repository as the server takes it: https, a host it names, no sign-in or query, not private."""
    parts = urlsplit(url.strip())
    if parts.scheme != "https":
        raise RemoteRefused("The address of the repository is an https address")
    if parts.username or parts.password or "@" in parts.netloc:
        raise RemoteRefused("The address holds no sign-in: the token is given apart")
    if parts.query or parts.fragment or not parts.path.strip("/") or ".." in parts.path:
        raise RemoteRefused("The address names a repository, without a query")
    host = (parts.hostname or "").lower()
    if host not in hosts:
        raise RemoteRefused("The server does not mirror to this host; its administrator names the forges")
    try:
        port = parts.port
    except ValueError:
        port = -1
    if port not in (None, 443):
        raise RemoteRefused("The address of the repository uses the port of https")
    try:
        found = {info[4][0] for info in socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)}
    except OSError:
        raise RemoteRefused("The host of the repository is not found") from None
    if not found or any(not ipaddress.ip_address(str(address).split("%", 1)[0]).is_global for address in found):
        raise RemoteRefused("The host of the repository is on a private network")
    return f"https://{host}{parts.path.rstrip('/')}"


def _key(secret: str) -> bytes:
    return hashlib.sha256(_INFO + secret.encode("utf-8")).digest()


def seal(secret: str, token: str) -> str:
    """A token as the server keeps it: AES-GCM with a key derived from the server's secret, and a nonce of its own."""
    import os

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = os.urandom(_NONCE)
    sealed = AESGCM(_key(secret)).encrypt(nonce, token.encode("utf-8"), _INFO)
    return base64.urlsafe_b64encode(nonce + sealed).decode("ascii")


def unseal(secret: str, sealed: str) -> str:
    """The token again; ``RemoteRefused`` when the server's secret has changed since it was sealed."""
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    try:
        raw = base64.urlsafe_b64decode(sealed.encode("ascii"))
        return AESGCM(_key(secret)).decrypt(raw[:_NONCE], raw[_NONCE:], _INFO).decode("utf-8")
    except (InvalidTag, ValueError):
        raise RemoteRefused("The token was sealed with another secret of the server: give it again") from None


# -- the mirror ------------------------------------------------------------------------------------------------------


@dataclass
class Mirrored:
    """A round of the mirror: the commits the forge is to hold by its refs, and the files it left out."""

    refs: dict[str, str] = field(default_factory=dict)  # the forge's ref: the mirror's commit
    left_out: list[dict[str, Any]] = field(default_factory=list)  # {"path", "size", "blob", "commit"}, each once


async def sources(git: Git, repository: Path) -> dict[str, str]:
    """The server's refs the forge mirrors: main and the proposals' branches."""
    found = {f"{PROPOSALS}{name}": commit for name, commit in (await git.proposals(repository)).items()}
    main = await git.ref(repository, MAIN)
    return {MAIN: main, **found} if main else found


async def mirror(git: Git, repository: Path, refs: dict[str, str], limit: int) -> Mirrored:
    """``refs`` as the forge gets them, each commit mirrored once and kept under ``refs/mirror/``."""
    mapped = await _mapped(git, repository)
    done = Mirrored()
    for ref, tip in refs.items():
        order = await git.run(repository, "rev-list", "--topo-order", "--reverse", tip)
        for commit in order.decode("ascii").split():
            if commit not in mapped:
                mapped[commit] = await _mirror_one(git, repository, commit, mapped, limit, done)
        own = MIRROR + ref.removeprefix("refs/heads/")
        before = await git.ref(repository, own)
        if before != mapped[tip]:
            await git.move_ref(repository, own, mapped[tip], before)
        done.refs[ref] = mapped[tip]
    kept = {MIRROR + ref.removeprefix("refs/heads/") for ref in refs}
    listed = await git.run(repository, "for-each-ref", "--format=%(refname) %(objectname)", MIRROR)
    for line in listed.decode("ascii", "replace").splitlines():
        own, _, commit = line.partition(" ")
        if own not in kept and own != MIRROR + "main":  # a proposal the server no longer has
            await git.delete_ref(repository, own, commit)
    return done


async def _mapped(git: Git, repository: Path) -> dict[str, str]:
    """The server's commits mirrored before, and their mirrors; a commit mirrored as itself names itself. Only the
    trailers of a message's last paragraph count, which the server wrote (``trailers``)."""
    tips = (await git.run(repository, "for-each-ref", "--format=%(objectname)", MIRROR)).decode("ascii").split()
    if not tips:
        return {}
    out = await git.run(repository, "log", "--format=%H%x1f%B%x1e", *tips)
    mapped: dict[str, str] = {}
    for record in out.decode("utf-8", "replace").split("\x1e"):
        commit, _, message = record.strip("\n").partition("\x1f")
        if SHA.fullmatch(commit):
            source = trailers(message).get("Mirror-Of", "")
            mapped[source if SHA.fullmatch(source) else commit] = commit
    return mapped


async def _mirror_one(
    git: Git, repository: Path, commit: str, mapped: dict[str, str], limit: int, done: Mirrored
) -> str:
    """One commit as the mirror has it: itself when it holds nothing above ``limit`` and its parents are mirrored as
    themselves, else a commit of its own without the large files."""
    listed = await git.run(repository, "ls-tree", "-r", "-l", "-z", commit)
    large: list[tuple[str, int, str]] = []
    for entry in listed.split(b"\0"):
        head, _, path = entry.partition(b"\t")
        parts = head.split()
        if len(parts) == 4 and parts[1] == b"blob" and parts[3].isdigit() and int(parts[3]) > limit:
            large.append((path.decode("utf-8", "replace"), int(parts[3]), parts[2].decode("ascii")))
    shown = (await git.run(repository, "log", "-1", "--format=%P", commit)).decode("ascii").split()
    parents = [mapped.get(parent, parent) for parent in shown]
    if not large and parents == shown:
        return commit
    named = {entry["blob"] for entry in done.left_out}
    done.left_out += [
        {"path": path, "size": size, "blob": blob, "commit": commit}
        for path, size, blob in large
        if blob not in named  # the commit that brought it, not each after
    ]
    tree = await git.tree_without(repository, commit, [path for path, _, _ in large])
    fields = await git.run(repository, "log", "-1", "--format=%an%x00%ae%x00%aI%x00%cn%x00%ce%x00%cI%x00%B", commit)
    an, ae, ad, cn, ce, cd, message = fields.decode("utf-8", "replace").split("\x00", 6)
    lines = [message.rstrip("\n")]  # its last paragraph is the server's trailers, which these join
    lines += [f"Dataregium-Left-Out: {path} ({size} bytes)" for path, size, _ in large]
    lines.append(f"Dataregium-Mirror-Of: {commit}")
    arguments = ["commit-tree", tree]
    for parent in parents:
        arguments += ["-p", parent]
    extra = {
        "GIT_AUTHOR_NAME": an,
        "GIT_AUTHOR_EMAIL": ae,
        "GIT_AUTHOR_DATE": ad,
        "GIT_COMMITTER_NAME": cn,
        "GIT_COMMITTER_EMAIL": ce,
        "GIT_COMMITTER_DATE": cd,
    }
    out = await git.run(repository, *arguments, stdin=("\n".join(lines) + "\n").encode("utf-8"), extra=extra)
    return out.decode("ascii").strip()


# -- the forge -------------------------------------------------------------------------------------------------------


def _header(token: str) -> dict[str, str]:
    """git's configuration for the token, through the environment: never an argument a process list shows; and no
    redirect, which could lead the push to another host than the one checked."""
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode("ascii")
    return {
        "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": "http.extraHeader",
        "GIT_CONFIG_VALUE_0": f"Authorization: Basic {basic}",
        "GIT_CONFIG_KEY_1": "http.followRedirects",
        "GIT_CONFIG_VALUE_1": "false",
    }


async def held(git: Git, repository: Path, url: str, token: str) -> dict[str, str]:
    """The forge's branches as it holds them now."""
    out = await git.run(repository, "ls-remote", "--heads", url, extra=_header(token), allow=PROTOCOLS)
    found: dict[str, str] = {}
    for line in out.decode("utf-8", "replace").splitlines():
        commit, _, ref = line.partition("\t")
        if SHA.fullmatch(commit):
            found[ref.strip()] = commit
    return found


async def _is_ancestor(git: Git, repository: Path, commit: str, new: str) -> bool:
    try:
        await git.run(repository, "merge-base", "--is-ancestor", commit, new)
    except GitError:
        return False
    return True


async def push(
    git: Git, repository: Path, url: str, token: str, mirrored: Mirrored, pushed: dict[str, str]
) -> tuple[dict[str, str], list[str]]:
    """Push the mirror's refs and take away the proposals' branches the server pushed and no longer has. Returns what
    the forge holds of the server's afterwards, and the branches someone else changed there, which are left alone.
    ``GitError`` when the forge refuses."""
    there = await held(git, repository, url, token)
    refspecs: list[str] = []
    leases: list[str] = []
    changed: list[str] = []
    after: dict[str, str] = {}
    for ref, new in mirrored.refs.items():
        old = there.get(ref)
        if old == new:
            after[ref] = new
        elif old is None or old == pushed.get(ref) or await _is_ancestor(git, repository, old, new):
            refspecs.append(f"{new}:{ref}")
            leases.append(f"--force-with-lease={ref}:{old or ''}")
            after[ref] = new
        else:
            changed.append(ref)
            after[ref] = old
    for ref, old in there.items():
        if ref.startswith(PROPOSALS) and ref not in mirrored.refs and pushed.get(ref) == old:
            refspecs.append(f":{ref}")
            leases.append(f"--force-with-lease={ref}:{old}")
    if refspecs:
        out = await git.run(
            repository,
            "push",
            "--porcelain",
            *leases,
            url,
            *refspecs,
            extra=_header(token),
            allow=PROTOCOLS,
            answers=(0, 1),
        )
        for line in out.decode("utf-8", "replace").splitlines():
            flag, _, rest = line.partition("\t")
            ref = rest.partition("\t")[0].rpartition(":")[2]
            if flag == "!" and ref:  # refused, also when the forge changed since it was read
                changed.append(ref)
                if ref in there:
                    after[ref] = there[ref]
                else:
                    after.pop(ref, None)
    return after, sorted(set(changed))


# -- the cleaner's round ---------------------------------------------------------------------------------------------


async def push_round(
    git: Git, remotes: PostgresRemotes, secret: str, audit_log: AuditLogStore, hosts: list[str]
) -> int:
    """One attempt for each connection that is due and whose server refs moved since its last push; returns how
    many were pushed. The address is checked again, as its host may resolve elsewhere now, and the hosts may have
    changed. An error waits ``RETRY_SECONDS`` before the next attempt."""
    pushed = 0
    slow = Git(git.root, git.executable, max(git.timeout, PUSH_SECONDS))  # for the forge, beyond the server
    for row in await remotes.due(MOST_PER_ROUND):
        project_id, url = row["project_id"], row["url"]
        repository = git.repository(project_id)
        if not repository.is_dir():
            continue
        now = await sources(git, repository)
        if row["status"] == "pushed" and now == row["sources"]:
            continue
        try:
            await asyncio.to_thread(check_url, url, hosts)
            token = unseal(secret, row["token"])
            mirrored = await mirror(git, repository, now, int(row["max_file_bytes"]))
            after, changed = await push(slow, repository, url, token, mirrored, row["pushed"] or {})
        except Exception as exc:  # one project's failure is its own; the round goes on
            text = str(exc)[:300] if isinstance(exc, GitError | RemoteRefused) else f"{type(exc).__name__}"
            await remotes.done(project_id, url, status="error", error=text, attempt_at=later_iso(RETRY_SECONDS))
            await audit.record(audit_log, "git.remote.failed", details={"project_id": project_id, "error": text})
            log.warning("git_remote_failed", project_id=project_id)
            continue
        before = row["left_out"] or []
        known = {entry.get("blob") for entry in before}
        left_out = [*(entry for entry in mirrored.left_out if entry["blob"] not in known), *before]
        status, error = "pushed", None
        if changed:
            status = "changed"
            error = "Changed at the forge, left as they are there: " + ", ".join(changed)[:250]
        await remotes.done(
            project_id,
            url,
            status=status,
            error=error,
            attempt_at=now_iso() if not changed else later_iso(RETRY_SECONDS),
            pushed=after,
            sources=now if not changed else {},
            left_out=left_out,
        )
        details = {"project_id": project_id, "refs": len(mirrored.refs), "left_out": len(mirrored.left_out)}
        await audit.record(audit_log, "git.remote.pushed", details={**details, "changed": changed or None})
        pushed += 1
    return pushed


__all__ = ["GitError", "Mirrored", "RemoteRefused", "check_url", "mirror", "push", "push_round", "seal", "unseal"]
