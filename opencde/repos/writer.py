# This file was generated with the assistance of an AI coding tool.
"""A version of a text file as a commit on its project's main (ADR 0030, UL-48, fas 5.2).

The writer decides whether a version's file is text, writes its blob before the store's transaction, and hands the
store a ``VersionCommit``: inside the transaction, under the project's lock, the store asks it for a commit on the
head it holds and then to move main to it, so that the version's row, the project's head and the ref change
together, and a head moved by another document of the project is no conflict. Every value in a path or a ref is the
server's own: the project's and the document's UUIDs and a name from ``TEXT_NAMES``; the client's file name, title and
message are data in the commit's message, cleaned of control characters, never arguments.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from opencde.ids import is_uuid, normalize_guid
from opencde.logging import get_logger
from opencde.repos.git import MAIN, Git, GitError, Person

log = get_logger(__name__)

#: The name of a document's file in its project's tree, by the kind of file; only these are text, and get a commit.
TEXT_NAMES = {
    ".ifc": "model.ifc",
    ".ids": "rules.ids",
    ".xml": "data.xml",
    ".json": "data.json",
    ".geojson": "data.geojson",
    ".csv": "data.csv",
    ".txt": "notes.txt",
    ".md": "notes.md",
}
SNIFF = 8192  # the start of a file that must hold no NUL for it to be text
MAX_MESSAGE = 2000
MAX_TITLE = 120
SERVER = ("Dataregium", "server@users.invalid")
_CONTROL = re.compile(r"[\x00-\x09\x0b-\x1f\x7f-\x9f\u2028\u2029]")


def tree_path(document_id: str, file_name: str) -> str | None:
    """Where a document's file stands in the tree, or None for a kind that is not text."""
    name = TEXT_NAMES.get(Path(file_name).suffix.lower())
    if name is None or not is_uuid(document_id):
        return None
    return f"documents/{normalize_guid(document_id)}/{name}"


def is_text(file_name: str, start: bytes) -> bool:
    """Whether a file of this name whose first bytes are ``start`` is text: a kind in the table, no NUL in its first
    8 KiB, and for IFC the STEP header."""
    suffix = Path(file_name).suffix.lower()
    if suffix not in TEXT_NAMES or b"\0" in start[:SNIFF]:
        return False
    return suffix != ".ifc" or start.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"ISO-10303-21;")


def clean(text: str, limit: int) -> str:
    """Text as a commit's message holds it: no control characters but line breaks, at most ``limit`` characters."""
    nfc = unicodedata.normalize  # aliased so the Cypher lint does not mistake it for a Neo4j-only function
    text = nfc("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))
    return _CONTROL.sub("", text).strip()[:limit].strip()


def message_of(
    *, title: str, version_number: str, message: str | None, document_id: str, version_index: int, base: int | None
) -> str:
    """The commit's message: the title and the version, the client's message, and trailers a clone can be read by."""
    first = clean(title, MAX_TITLE).replace("\n", " ") or "Document"
    lines = [f"{first}: version {clean(version_number, 40).replace(chr(10), ' ') or version_index}", ""]
    if message and (body := clean(message, MAX_MESSAGE)):
        lines += [body, ""]
    lines += [f"Dataregium-Document: {document_id}", f"Dataregium-Version: {version_index}"]
    if base is not None:
        lines.append(f"Dataregium-Base: {base}")
    return "\n".join(lines) + "\n"


@dataclass
class VersionCommit:
    """A version's file, its blob written, waiting for the store to give it its place on main."""

    git: Git
    repository: Path
    blob: str
    file_name: str
    title: str
    author: str  # the user's name
    author_id: str | None  # the user's id, the address's local part; None for a user who is gone
    message: str | None = None
    base: int | None = None
    commit: str | None = None  # what ``make`` gave
    parent: str | None = None  # what it was made on

    async def make(
        self, *, head: str | None, document_id: str, version_index: int, version_number: str, created: str
    ) -> str:
        """The commit of this version on ``head`` (main as the store holds it); see ``_agree``."""
        self.parent = await self._agree(head)
        path = tree_path(document_id, self.file_name)
        if path is None:
            raise GitError("the file is not of a kind the repository holds")
        tree = await self.git.tree(self.repository, self.parent, {path: self.blob})
        when = created
        self.commit = await self.git.commit(
            self.repository,
            tree,
            [self.parent] if self.parent else [],
            Person(clean(self.author, 200) or "unknown", f"{self.author_id or 'unknown'}@users.invalid", when),
            Person(*SERVER, when),
            message_of(
                title=self.title,
                version_number=version_number,
                message=self.message,
                document_id=document_id,
                version_index=version_index,
                base=self.base,
            ),
        )
        return self.commit

    async def move(self, *, new: str, old: str | None) -> None:
        """Point main at ``new`` while it still names what ``make`` built on (the store's ``old``, unless the
        repository had lost it); inside the store's transaction, which rolls back if this fails."""
        await self.git.move_main(self.repository, new, self.parent if self.commit == new else old)

    async def _agree(self, head: str | None) -> str | None:
        """What to build on. PostgreSQL decides: main is set back to the store's head when a process moved it and
        then lost its transaction (main a descendant of the head, or the head none). A repository that no longer has
        the head, as one restored without its repositories, goes on from main as it is, and the log says so."""
        actual = await self.git.head(self.repository)
        if actual == head:
            return head
        if head is None:
            if actual is not None:
                await self.git.run(self.repository, "update-ref", "-d", MAIN, actual)
            return None
        if not await self.git.has(self.repository, head):
            log.warning("project_repo_history_lost", repository=self.repository.name)
            return actual
        if actual is None:
            await self.git.move_main(self.repository, head, None)
            return head
        await self.git.run(self.repository, "merge-base", "--is-ancestor", head, actual)  # raises when it is not
        await self.git.move_main(self.repository, head, actual)
        return head


async def prepare(
    git: Git | None,
    *,
    project_id: str,
    path: Path,
    file_name: str,
    title: str,
    author: str,
    author_id: str | None,
    message: str | None = None,
    base: int | None = None,
) -> VersionCommit | None:
    """A version's commit, its blob written, or None when the server keeps no repositories or the file is not
    text."""
    if git is None or not is_text(file_name, _start(path)):
        return None
    repository = await git.ensure(project_id)
    blob = await git.blob(repository, path)
    return VersionCommit(
        git=git,
        repository=repository,
        blob=blob,
        file_name=file_name,
        title=title,
        author=author,
        author_id=author_id,
        message=message,
        base=base,
    )


def _start(path: Path) -> bytes:
    with path.open("rb") as stream:
        return stream.read(SNIFF)
