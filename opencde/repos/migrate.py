# This file was generated with the assistance of an AI coding tool.
"""The history of the versions from before the repositories (ADR 0030, UL-48, fas 5.2): ``opencde git migrate``.

A project without a repository gets its main built linearly from its versions of text files, in the order of their
creation, then document and index; each commit has the version's author and time, as the writer makes them, and
the versions' rows get their commits, the project its head and each IFC document its highest STEP id, in one
transaction under the project's lock, before main is set. A project that already has a repository is left as it is:
its history goes on from its first commit, and its older versions stay without one. A version whose file is gone or
not text is passed over. Run again, it does what is left.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from opencde.documents.step import stored_history
from opencde.repos.git import Git, Person
from opencde.repos.writer import SERVER, clean, is_text, message_of, tree_path

if TYPE_CHECKING:
    from opencde.postgres.database import Postgres, Tx
    from opencde.storage import LocalStorage

PROJECTS = "SELECT project_id FROM projects ORDER BY project_id"
HAS_REPO = "SELECT head FROM project_repos WHERE project_id = $1"
VERSIONS = """
SELECT v.id, v.document_id, v.version_index, v.version_number, v.title, v.file_name, v.storage_key, v.author,
       v.uploaded_by, v.creation_date, v.parent_version_index
FROM document_versions v JOIN documents d ON d.document_id = v.document_id
WHERE d.project_id = $1 AND v.commit IS NULL
ORDER BY v.creation_date, v.document_id, v.version_index
"""
LOCK = "SELECT pg_advisory_xact_lock(hashtextextended($1, 30))"
SET_COMMIT = "UPDATE document_versions SET commit = $2 WHERE id = $1 AND commit IS NULL"
SET_HEAD = "INSERT INTO project_repos (project_id, head, created_at) VALUES ($1, $2, $3)"
SET_MAX_STEP_ID = "UPDATE documents SET max_step_id = GREATEST(max_step_id, $2::bigint) WHERE document_id = $1"


@dataclass
class Migrated:
    """What ``migrate`` did with one project."""

    project_id: str
    commits: int = 0
    passed_over: int = 0
    skipped: str | None = None  # why nothing was done
    head: str | None = None
    versions: list[tuple[str, str]] = field(default_factory=list)  # (version id, commit)


async def migrate(git: Git, postgres: Postgres, storage: LocalStorage, project_id: str | None = None) -> list[Migrated]:
    """Build the history of every project without a repository, or of ``project_id``."""
    projects = [project_id] if project_id else [row["project_id"] for row in await postgres.run(PROJECTS)]
    return [await _project(git, postgres, storage, project) for project in projects]


async def _project(git: Git, postgres: Postgres, storage: LocalStorage, project_id: str) -> Migrated:
    done = Migrated(project_id)
    if await postgres.one(HAS_REPO, project_id) is not None:
        done.skipped = "has a repository"
        return done
    rows = await postgres.run(VERSIONS, project_id)
    if not rows:
        done.skipped = "has no versions"
        return done
    repository = git.repository(project_id)
    if _exists(repository) and await git.head(repository) is not None:
        done.skipped = "has a main the store does not name"
        return done
    repository = await git.ensure(project_id)
    highest: dict[str, int] = {}
    for row in rows:
        path = tree_path(str(row["document_id"]), str(row["file_name"]))
        file = storage.path(str(row["storage_key"]))
        if path is None or not _exists(file) or not is_text(str(row["file_name"]), _start(file)):
            done.passed_over += 1
            continue
        blob = await git.blob(repository, file)
        tree = await git.tree(repository, done.head, {path: blob})
        when = str(row["creation_date"])
        done.head = await git.commit(
            repository,
            tree,
            [done.head] if done.head else [],
            Person(
                clean(str(row["author"]), 200) or "unknown", f"{row['uploaded_by'] or 'unknown'}@users.invalid", when
            ),
            Person(*SERVER, when),
            message_of(
                title=str(row["title"]),
                version_number=str(row["version_number"]),
                message=None,
                document_id=str(row["document_id"]),
                version_index=int(row["version_index"]),
                base=row["parent_version_index"],
            ),
        )
        done.versions.append((str(row["id"]), done.head))
        if (high := stored_history(storage, str(row["storage_key"]), str(row["file_name"]))) is not None:
            highest[str(row["document_id"])] = max(high, highest.get(str(row["document_id"]), 0))
    if done.head is None:
        done.skipped = "has no versions of text files"
        return done

    async def record(tx: Tx) -> None:
        await tx.execute(LOCK, project_id)
        if await tx.one(HAS_REPO, project_id) is not None:
            raise RuntimeError("another writer made the project's repository meanwhile; run it again")
        for version_id, commit in done.versions:
            await tx.execute(SET_COMMIT, version_id, commit)
        for document_id, high in highest.items():
            await tx.execute(SET_MAX_STEP_ID, document_id, high)
        await tx.execute(SET_HEAD, project_id, done.head, _now())
        await git.move_main(repository, str(done.head), None)

    await postgres.write(record)
    done.commits = len(done.versions)
    return done


def _exists(path: Any) -> bool:
    return path.exists()


def _start(path: Any) -> bytes:
    with path.open("rb") as stream:
        return stream.read(8192)


def _now() -> str:
    from opencde.timeutil import now_iso

    return now_iso()
