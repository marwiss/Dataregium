# This file was generated with the assistance of an AI coding tool.
"""A document's history in its project's repository: an extension of the Documents API (ADR 0030, UL-48, fas 5.5).

``GET /documents/1.0/documents/{id}/history`` lists the commits that change the document's file, on any of the
server's branches, newest first: the versions on ``main``, an upload as it came beside the merge it became
(``upload``), the trials of change sets (``proposal``) and the uploads that did not merge, kept a day (``conflict``,
F96 b). ``GET …/versions/{index}/commit`` gives a version's commit with its parents and whole message. Both are for a
member of the document's project, with the same bearer token as the Documents API, and 404 on a server without the
repositories. A commit's author and message are data: names and texts the clients wrote, cleaned of control
characters when they were committed.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Path, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from opencde.auth.dependencies import DocumentsPrincipal
from opencde.deps import GitDep, SettingsDep
from opencde.ids import is_uuid, normalize_guid
from opencde.repos.git import Git, Logged
from opencde.repos.writer import trailers, tree_path
from opencde.store.documents.versions import DocumentsDep, VersionRecord
from opencde.timeutil import canonical_iso

TAG = "Document history (extension)"
router = APIRouter(prefix="/documents/1.0", tags=[TAG])

DocumentId = Annotated[str, Path(alias="document_id", max_length=64)]
VersionIndex = Annotated[int, Path(alias="version_index", ge=1, le=1_000_000_000)]
MAX_PAGE = 500
Kind = Literal["version", "upload", "proposal", "conflict"]


class HistoryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    commit: str = Field(description="The commit's 40 hexadecimal characters, for `ifc-diff?a=` and `?b=`.")
    parents: list[str]
    kind: Kind = Field(
        description="`version` for a version of the document; `upload` for an upload as it came, beside the merge it "
        "became; `proposal` for the trial of a change set, open or approved; `conflict` for an upload that did not "
        "merge, kept until `expires_at`."
    )
    version_index: int | None = Field(
        None, description="The version: the commit's own for `version`, the one it became part of for `upload`."
    )
    base_version_index: int | None = Field(None, description="The version it was built on.")
    merged_with_version_index: int | None = Field(None, description="The latest version a merge took in.")
    proposal_url: str | None = Field(None, description="For `proposal`: the trial's address.")
    expires_at: str | None = Field(None, description="For `conflict`: when the server takes it away.")
    author: str
    date: str = Field(description="When it was made, ISO 8601 in UTC.")
    subject: str = Field(description="The first line of its message.")


class VersionCommit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    commit: str
    parents: list[str]
    author: str
    committer: str = Field(description="Who approved a change set, else the server.")
    date: str
    message: str = Field(description="The whole message, with its `Dataregium-` trailers.")
    base_version_index: int | None = None
    merged_with_version_index: int | None = None
    side_commit: str | None = Field(None, description="The upload as it came, when the version merged one.")
    proposal_commit: str | None = Field(None, description="The trial of the change set the version was approved from.")


async def _document(
    documents: DocumentsDep, git: Git | None, user_id: str, document_id: str
) -> tuple[Git, VersionRecord, str]:
    """The document's latest version for a member of its project, the repository's git, and the file's path in
    its tree; 404 otherwise, and on a server without the repositories."""
    if git is None:
        raise HTTPException(404, "This server keeps no repositories")
    if not is_uuid(document_id):
        raise HTTPException(404, "Document not found")
    latest = await documents.latest(user_id=user_id, document_id=normalize_guid(document_id))
    if latest is None:
        raise HTTPException(404, "Document not found")
    where = tree_path(latest.document_id, latest.file_name)
    if where is None or not git.repository(latest.project_id).is_dir():
        raise HTTPException(404, "The document has no history in a repository")
    return git, latest, where


def _number(value: str | None) -> int | None:
    return int(value) if value is not None and value.isdigit() else None


def _entry(found: Logged, versions: dict[str, int], bcf: str, project_id: str) -> HistoryEntry:
    named = trailers(found.message)
    kind: Kind = "upload"
    version_index = _number(named.get("Version"))
    proposal_url = None
    if found.commit in versions:
        kind, version_index = "version", versions[found.commit]
    elif "Proposal" in named:
        kind, version_index = "proposal", None
        if is_uuid(named.get("Topic", "")):
            proposal_url = f"{bcf}/projects/{project_id}/topics/{normalize_guid(named['Topic'])}/ifc-proposal"
    elif "Conflict-With" in named:
        kind, version_index = "conflict", None
    return HistoryEntry(
        commit=found.commit,
        parents=list(found.parents),
        kind=kind,
        version_index=version_index,
        base_version_index=_number(named.get("Base")),
        merged_with_version_index=_number(named.get("Merged-With") or named.get("Conflict-With")),
        proposal_url=proposal_url,
        expires_at=named.get("Expires-At") if kind == "conflict" else None,
        author=found.author,
        date=canonical_iso(found.date),
        subject=found.message.split("\n", 1)[0],
    )


@router.get(
    "/documents/{document_id}/history",
    summary="List the commits of a document's file in its project's repository",
    response_model=list[HistoryEntry],
    response_model_exclude_none=True,
)
async def document_history(
    request: Request,
    response: Response,
    principal: DocumentsPrincipal,
    document_id: DocumentId,
    documents: DocumentsDep,
    git: GitDep,
    settings: SettingsDep,
    top: Annotated[int, Query(alias="$top", ge=1, le=MAX_PAGE)] = 100,
    skip: Annotated[int, Query(alias="$skip", ge=0, le=1_000_000)] = 0,
) -> list[HistoryEntry]:
    """The commits that change the document's file, newest first, on `main`, beside it and on the proposals' and
    conflicts' branches; `$top` (at most 500) and `$skip` page through them. 404 for a document that is not yours, one
    whose file is not text, and on a server without the repositories. A page that is not the whole list carries
    `X-Total-Count` and `Link: <…>; rel="next"`, as the other lists do."""
    found_git, latest, where = await _document(documents, git, principal.user_id, document_id)
    repository = found_git.repository(latest.project_id)
    commits = await found_git.log(repository, where, skip=skip, limit=top)
    if len(commits) == top and (total := await found_git.count(repository, where)) > skip + top:
        following = request.url.include_query_params(**{"$skip": str(skip + top)})
        response.headers["X-Total-Count"] = str(total)
        response.headers["Link"] = f'<{following}>; rel="next"'
    versions = {
        record.commit: record.version_index
        for record in await documents.versions(user_id=principal.user_id, document_id=latest.document_id)
        if record.commit
    }
    bcf = f"{settings.public_url}/bcf/3.0"
    return [_entry(found, versions, bcf, latest.project_id) for found in commits]


@router.get(
    "/documents/{document_id}/versions/{version_index}/commit",
    summary="Get a version's commit in its project's repository",
    response_model=VersionCommit,
    response_model_exclude_none=True,
)
async def version_commit(
    principal: DocumentsPrincipal,
    document_id: DocumentId,
    version_index: VersionIndex,
    documents: DocumentsDep,
    git: GitDep,
) -> VersionCommit:
    """The version's commit: its parents, who made and who committed it, its whole message, and the upload as it came
    or the trial of the change set when the version is a merge of one. 404 for a version that is not yours or has no
    commit, and on a server without the repositories."""
    found_git, latest, _ = await _document(documents, git, principal.user_id, document_id)
    record = await documents.version(
        user_id=principal.user_id, document_id=latest.document_id, version_index=version_index
    )
    if record is None or not record.commit:
        raise HTTPException(404, "The version has no commit")
    repository = found_git.repository(latest.project_id)
    found = await found_git.show(repository, record.commit)
    if found is None:
        raise HTTPException(404, "The version's commit is not in the repository")
    named = trailers(found.message)
    side = proposal = None
    if len(found.parents) > 1:
        other = await found_git.show(repository, found.parents[1])
        if other is not None and "Proposal" in trailers(other.message):
            proposal = other.commit
        else:
            side = found.parents[1]
    return VersionCommit(
        commit=found.commit,
        parents=list(found.parents),
        author=found.author,
        committer=found.committer,
        date=canonical_iso(found.date),
        message=found.message.strip("\n") + "\n",
        base_version_index=_number(named.get("Base")),
        merged_with_version_index=_number(named.get("Merged-With")),
        side_commit=side,
        proposal_commit=proposal,
    )
