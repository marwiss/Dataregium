# This file was generated with the assistance of an AI coding tool.
"""A version's file read back out of its project's repository (ADR 0030, F94 c, fas 5.6).

Where ``OPENCDE_GIT_FILES_KEPT_DAYS`` is set, the cleaner's round takes the file of an old version out of the storage
once its project's repository holds the same bytes (``prune``): not the latest version of a document, not a published
one, and not one read within those days. Whatever reads a version calls ``restore`` first: a file that is not in its
place is written back from the version's commit (``git cat-file blob``, as it comes), beside it and then renamed, so
that a reader never sees half a file, and the storage is again its cache until the round takes it anew. The bytes are
those of the upload, as git keeps them (``--no-filters``, no line-ending conversion).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from opencde.ids import new_id
from opencde.logging import get_logger
from opencde.repos.git import SHA, Git, GitError
from opencde.repos.writer import tree_path

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from opencde.storage import LocalStorage

log = get_logger(__name__)

DAY = 86_400.0
MAX_PRUNED = 1_000  # the files one round takes away at most
PAGE = 500  # the candidates read at a time


async def restore(
    git: Git | None,
    storage: LocalStorage,
    *,
    storage_key: str,
    project_id: str,
    document_id: str,
    file_name: str,
    commit: str | None,
) -> bool:
    """Put a version's file back in its place out of its commit when the round took it; whether it was read back.
    Without a repository or a commit there is nothing to read it from, and the reader finds what the storage has."""
    where = tree_path(document_id, file_name)
    if git is None or not commit or where is None:
        return False
    path = storage.path(storage_key)
    if path.is_file():
        return False
    repository = git.repository(project_id)
    if not repository.is_dir() or not await git.holds(repository, commit, where):
        return False
    written = path.with_name(f"{path.name}.{new_id()}.tmp")  # no sweep takes a .tmp, and two readers write apart
    try:
        await git.blob_to(repository, commit, where, written)
        os.replace(written, path)
    except BaseException:
        written.unlink(missing_ok=True)
        raise
    log.info("version_file_restored", project_id=project_id, document_id=document_id)
    return True


async def restore_record(git: Git | None, storage: LocalStorage, record: Any) -> bool:
    """``restore`` for a ``VersionRecord``."""
    return await restore(
        git,
        storage,
        storage_key=str(record.storage_key),
        project_id=str(record.project_id),
        document_id=str(record.document_id),
        file_name=str(record.file_name),
        commit=record.commit,
    )


@dataclass
class Pruned:
    removed: int = 0  # files taken out of the storage
    kept: int = 0  # files whose bytes the repository does not hold, or that were read within the days


async def prune(
    git: Git,
    storage: LocalStorage,
    days: float,
    candidates: Callable[[int, int], Awaitable[list[dict[str, Any]]]],
) -> Pruned:
    """One round: the files of the versions ``candidates`` names page by page (old, with a commit, not the latest of
    their document, not published) taken out of the storage when they have not been written for ``days`` and the
    repository's blob at the version's commit is the same bytes, at most ``MAX_PRUNED``."""
    done = Pruned()
    now = datetime.now(UTC).timestamp()
    offset = 0
    while done.removed < MAX_PRUNED and (rows := await candidates(offset, PAGE)):
        offset += len(rows)
        for row in rows:
            if done.removed >= MAX_PRUNED:
                break
            await _prune_one(git, storage, row, now - days * DAY, done)
    return done


async def _prune_one(git: Git, storage: LocalStorage, row: dict[str, Any], before: float, done: Pruned) -> None:
    path = storage.path(str(row["storage_key"]))
    where = tree_path(str(row["document_id"]), str(row["file_name"]))
    commit = str(row["commit"] or "")
    try:
        if where is None or not SHA.fullmatch(commit) or not path.is_file() or path.stat().st_mtime > before:
            return
        repository = git.repository(str(row["project_id"]))
        held = await git.run(repository, "rev-parse", "--verify", "--quiet", f"{commit}:{where}", answers=(0, 1))
        mine = await git.run(repository, "hash-object", "--no-filters", "--", str(path))
        if not held.strip() or held.strip() != mine.strip():
            done.kept += 1
            return
        path.unlink()
        done.removed += 1
    except (GitError, OSError, ValueError) as exc:
        done.kept += 1
        log.warning("version_file_kept", project_id=str(row["project_id"]), error=str(exc)[:200])
