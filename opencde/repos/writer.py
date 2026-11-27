# This file was generated with the assistance of an AI coding tool.
"""A version of a text file as a commit on its project's main (ADR 0030, UL-48, fas 5.2).

The writer decides whether a version's file is text, writes its blob before the store's transaction, and hands the
store a ``VersionCommit``: inside the transaction, under the project's lock, the store asks it for a commit on the
head it holds and then to move main to it, so that the version's row, the project's head and the ref change
together, and a head moved by another document of the project is no conflict. A change set's trial is a commit on
its proposal's branch (``propose``, fas 5.4), and its approval a merge commit of main and that commit, with the one
who approved it as the committer. Every value in a path or a ref is the
server's own: the project's and the document's UUIDs and a name from ``TEXT_NAMES``; the client's file name, title and
message are data in the commit's message, cleaned of control characters, never arguments.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from opencde.ids import is_uuid, new_id, normalize_guid
from opencde.logging import get_logger
from opencde.repos.git import MAIN, Git, GitError, Person, conflict_ref, proposal_ref

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
    *,
    title: str,
    version_number: str,
    message: str | None,
    document_id: str,
    version_index: int | None,
    base: int | None,
    merged_with: int | None = None,
    subject: str | None = None,
    approved_by: str | None = None,
    proposal: str | None = None,
    topic: str | None = None,
    conflict_with: int | None = None,
    expires_at: str | None = None,
) -> str:
    """The commit's message: the title and the version, the client's message, and trailers a clone can be read by;
    a proposal's commit has no version yet."""
    first = clean(title, MAX_TITLE).replace("\n", " ") or "Document"
    number = clean(version_number, 40).replace(chr(10), " ") or version_index
    lines = [f"{first}: {subject or f'version {number}'}", ""]
    if message and (body := clean(message, MAX_MESSAGE)):
        lines += [body, ""]
    lines.append(f"Dataregium-Document: {document_id}")
    if version_index is not None:
        lines.append(f"Dataregium-Version: {version_index}")
    if base is not None:
        lines.append(f"Dataregium-Base: {base}")
    if merged_with is not None:
        lines.append(f"Dataregium-Merged-With: {merged_with}")
    if proposal is not None:
        lines.append(f"Dataregium-Proposal: {proposal}")
    if topic is not None and (guid := clean(topic, 64).replace("\n", " ")):
        lines.append(f"Dataregium-Topic: {guid}")
    if conflict_with is not None:
        lines.append(f"Dataregium-Conflict-With: {conflict_with}")
    if expires_at is not None:
        lines.append(f"Dataregium-Expires-At: {expires_at}")
    if approved_by is not None and (name := clean(approved_by, 200).replace("\n", " ")):
        lines.append(f"Dataregium-Approved-By: {name}")
    return "\n".join(lines) + "\n"


def trailers(message: str) -> dict[str, str]:
    """The ``Dataregium-`` trailers of a commit's message, by their names without the prefix."""
    found: dict[str, str] = {}
    for line in message.splitlines():
        name, colon, value = line.partition(": ")
        if colon and name.startswith("Dataregium-") and " " not in name:
            found[name.removeprefix("Dataregium-")] = value.strip()
    return found


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
    # A merged upload (fas 5.3): the upload as it came, as a side commit on its base's commit, and the version a
    # merge commit of it and main, which ``merged_with`` names.
    side_blob: str | None = None
    side_parent: str | None = None
    merged_with: int | None = None
    side: str | None = None  # the side commit ``make`` gave
    # An approved change set (fas 5.4): its trial's commit on the proposal's branch (``propose``) as the second
    # parent, and the one who approved it as the committer.
    proposal_commit: str | None = None
    proposal_id: str | None = None
    approver: str | None = None
    approver_id: str | None = None

    async def make(
        self, *, head: str | None, document_id: str, version_index: int, version_number: str, created: str
    ) -> str:
        """The commit of this version on ``head`` (main as the store holds it); see ``_agree``."""
        self.parent = await self._agree(head)
        path = tree_path(document_id, self.file_name)
        if path is None:
            raise GitError("the file is not of a kind the repository holds")
        when = created
        author = Person(clean(self.author, 200) or "unknown", f"{self.author_id or 'unknown'}@users.invalid", when)
        parents = [self.parent] if self.parent else []
        if self.side_blob is not None:
            side_parent = (
                self.side_parent if self.side_parent and await self.git.has(self.repository, self.side_parent) else None
            )
            self.side = await self.git.commit(
                self.repository,
                await self.git.tree(self.repository, side_parent, {path: self.side_blob}),
                [side_parent] if side_parent else [],
                author,
                Person(*SERVER, when),
                message_of(
                    title=self.title,
                    version_number=version_number,
                    message=self.message,
                    document_id=document_id,
                    version_index=version_index,
                    base=self.base,
                    subject=f"upload on version {self.base}",
                ),
            )
            parents.append(self.side)
        if self.proposal_commit is not None and await self.git.has(self.repository, self.proposal_commit):
            parents.append(self.proposal_commit)
        committer = Person(*SERVER, when)
        if self.approver is not None:
            committer = Person(
                clean(self.approver, 200) or "unknown", f"{self.approver_id or 'unknown'}@users.invalid", when
            )
        self.commit = await self.git.commit(
            self.repository,
            await self.git.tree(self.repository, self.parent, {path: self.blob}),
            parents,
            author,
            committer,
            message_of(
                title=self.title,
                version_number=version_number,
                message=self.message,
                document_id=document_id,
                version_index=version_index,
                base=self.base,
                merged_with=self.merged_with,
                approved_by=self.approver,
                proposal=self.proposal_id,
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


async def propose(
    git: Git | None,
    *,
    project_id: str,
    proposal_id: str,
    path: Path,
    document_id: str,
    file_name: str,
    title: str,
    base: int,
    base_commit: str | None,
    author: str,
    author_id: str | None,
    created: str,
    topic: str | None = None,
) -> str | None:
    """A change set's trial as a commit on its proposal's branch (``proposal_ref``, fas 5.4): the tree of the base
    version's commit with the trial's file, the proposer its author, and the topic's guid for the history to link
    the trial. None without repositories, for a file that is not text, or a base without a commit, which the approval
    then goes without."""
    where = tree_path(document_id, file_name)
    if git is None or where is None or base_commit is None or not is_text(file_name, _start(path)):
        return None
    repository = await git.ensure(project_id)
    if not await git.has(repository, base_commit):
        return None
    blob = await git.blob(repository, path)
    commit = await git.commit(
        repository,
        await git.tree(repository, base_commit, {where: blob}),
        [base_commit],
        Person(clean(author, 200) or "unknown", f"{author_id or 'unknown'}@users.invalid", created),
        Person(*SERVER, created),
        message_of(
            title=title,
            version_number="",
            message=None,
            document_id=document_id,
            version_index=None,
            base=base,
            subject=f"change set on version {base}",
            proposal=proposal_id,
            topic=topic,
        ),
    )
    branch = proposal_ref(proposal_id)
    await git.move_ref(repository, branch, commit, await git.ref(repository, branch))
    return commit


async def keep_conflict(
    git: Git,
    *,
    repository: Path,
    blob: str,
    document_id: str,
    file_name: str,
    title: str,
    base: int,
    base_commit: str | None,
    latest: int,
    author: str,
    author_id: str | None,
    created: str,
    expires_at: str,
) -> str | None:
    """An upload that did not merge, as a commit on a conflict's branch (``conflict_ref``, F96 b, fas 5.5) on its
    base's commit, so that it can be compared with the latest version until ``expires_at``, when the upkeep takes
    it. None for a base without a commit."""
    where = tree_path(document_id, file_name)
    if where is None or base_commit is None or not await git.has(repository, base_commit):
        return None
    commit = await git.commit(
        repository,
        await git.tree(repository, base_commit, {where: blob}),
        [base_commit],
        Person(clean(author, 200) or "unknown", f"{author_id or 'unknown'}@users.invalid", created),
        Person(*SERVER, created),
        message_of(
            title=title,
            version_number="",
            message=None,
            document_id=document_id,
            version_index=None,
            base=base,
            subject=f"upload on version {base} that did not merge with version {latest}",
            conflict_with=latest,
            expires_at=expires_at,
        ),
    )
    await git.move_ref(repository, conflict_ref(new_id()), commit, None)
    return commit


def _start(path: Path) -> bytes:
    with path.open("rb") as stream:
        return stream.read(SNIFF)
