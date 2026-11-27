# This file was generated with the assistance of an AI coding tool.
"""The difference between two IFC document versions: an extension of the Documents API (ADR 0021, OB-30).

``GET /documents/1.0/documents/{id}/versions/{index}/ifc-diff/{other_index}`` compares two versions of a document, or
with ``?other_document_id=`` a version of another document of a project the member is in, and
``GET /documents/1.0/documents/{id}/ifc-diff?a=&b=`` two sides of a document that are each a version or a commit of
its history (ADR 0030, fas 5.5), read out of the project's repository, element by element: what
the other has that this one lacks, what it lacks, and what differs, by GlobalId and path, as the worker
``opencde.ifcgraph.diff`` finds it. Two versions of one document whose GlobalIds keep their STEP ids are compared
instance by instance too, and each element names the STEP ids brought to it (``by: "step"``, UL-48, fas 5.1); a
re-export, or two documents, are compared by GlobalId alone (``by: "guid"``). The difference is computed on request in
a worker process and never stored. The same bearer token as the Documents API.
"""

from __future__ import annotations

import tempfile
from pathlib import Path as FilePath
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict, Field

from opencde.auth.dependencies import DocumentsPrincipal
from opencde.deps import GitDep, SettingsDep
from opencde.ids import is_uuid, normalize_guid
from opencde.ifcgraph.service import IfcGraphDep, IfcGraphService
from opencde.repos.git import Git
from opencde.repos.writer import tree_path
from opencde.store.documents.versions import DocumentsDep, VersionRecord

TAG = "IFC diff (extension)"

router = APIRouter(prefix="/documents/1.0", tags=[TAG])

DocumentId = Annotated[str, Path(alias="document_id", max_length=64)]
VersionIndex = Annotated[int, Path(alias="version_index", ge=1, le=1_000_000_000)]
OtherIndex = Annotated[int, Path(alias="other_index", ge=1, le=1_000_000_000)]
OtherDocument = Annotated[str | None, Query(alias="other_document_id", max_length=64)]
#: A side of ``ifc-diff?a=&b=``: a version's index, or a commit's 40 hexadecimal characters.
Side = Annotated[str, Query(min_length=1, max_length=40, pattern=r"^(?:[0-9]{1,10}|[0-9a-f]{40})$")]


class IfcDiffElement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    guid: str
    type: str
    name: str | None = None
    storey: str | None = None
    step_ids: list[int] | None = Field(
        None,
        description="With `by` `step`: the STEP ids in b's file of the instances added or changed that were brought "
        "to this element (the element itself, or what it reaches), at most 1000.",
    )
    step_ids_count: int | None = Field(None, description="How many there were, when `step_ids` was cut.")
    step_ids_removed: list[int] | None = Field(
        None,
        description="With `by` `step`: the STEP ids in a's file of the instances removed (or, for a relationship, "
        "that let the element go) that were brought to this element, at most 1000.",
    )
    step_ids_removed_count: int | None = Field(
        None, description="How many there were, when `step_ids_removed` was cut."
    )


class IfcDiffAttribute(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(
        description="A direct attribute by its name, a property as `Pset.Property`, `ObjectPlacement` (the 4 by 4 "
        "matrix), `Representation` (a fingerprint of the shape), `Container` (the storey's GlobalId), `Type` (the "
        "type object's GlobalId), or `type` when the class changed."
    )
    old: Any = None
    new: Any = None


class IfcDiffChanged(IfcDiffElement):
    attributes: list[IfcDiffAttribute]


class IfcDiffSide(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str
    version_index: int | None = Field(None, description="The version, unless the side is a commit that is none.")
    commit: str | None = Field(None, description="The side's commit, where the server keeps a repository.")


class IfcDiffLoose(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_id: int
    type: str | None = Field(None, description="The class as the schema writes it, such as `IfcOwnerHistory`.")
    side: Literal["a", "b"] = Field(description="The file the STEP id is in.")
    change: Literal["added", "removed", "changed"]
    objects: int | None = Field(None, description="In `shared`: how many objects reach the instance.")


class IfcDiff(BaseModel):
    model_config = ConfigDict(extra="forbid")

    by: Literal["step", "guid"] = Field(
        description="`step` when the versions are of one document and every GlobalId in both has the same STEP id in "
        "both, so that the instances were compared too; `guid` when one is a re-export, or for two documents, and "
        "the comparison is by GlobalId alone, without STEP ids."
    )
    a: IfcDiffSide = Field(description="This version, `{version_index}`.")
    b: IfcDiffSide = Field(description="The version compared with, `{other_index}`.")
    added: list[IfcDiffElement] = Field(description="Rooted entities only `b` has.")
    removed: list[IfcDiffElement] = Field(description="Rooted entities only `a` has.")
    changed: list[IfcDiffChanged] = Field(
        description="Rooted entities in both whose attributes differ, or, with `by` `step`, under which an instance "
        "changed (then perhaps with no attributes)."
    )
    same: int = Field(description="How many rooted entities are in both and the same.")
    truncated: bool = Field(description="True when a list had more than the server keeps (10,000).")
    unplaced: list[IfcDiffLoose] = Field(
        default_factory=list,
        description="With `by` `step`: instances added, removed or changed that no object reaches.",
    )
    unplaced_count: int | None = Field(None, description="How many there were, when `unplaced` was cut at 10,000.")
    shared: list[IfcDiffLoose] = Field(
        default_factory=list,
        description="With `by` `step`: instances that more than 100 objects reach, such as a style or a unit, "
        "brought to none of them.",
    )
    shared_count: int | None = Field(None, description="How many there were, when `shared` was cut at 10,000.")


async def _version(documents: Any, user_id: str, document_id: str, version_index: int) -> VersionRecord:
    if not is_uuid(document_id):
        raise HTTPException(404, "Document version not found")
    record = await documents.version(
        user_id=user_id, document_id=normalize_guid(document_id), version_index=version_index
    )
    if record is None:
        raise HTTPException(404, "Document version not found")
    if not IfcGraphService.wants(record.file_name):
        raise HTTPException(400, f"Version {version_index} of {record.title} is not an IFC file")
    return record


@router.get(
    "/documents/{document_id}/versions/{version_index}/ifc-diff/{other_index}",
    summary="Compare two IFC document versions element by element",
    response_model=IfcDiff,
    response_model_exclude_none=True,
)
async def ifc_diff(
    principal: DocumentsPrincipal,
    document_id: DocumentId,
    version_index: VersionIndex,
    other_index: OtherIndex,
    documents: DocumentsDep,
    ifcgraph: IfcGraphDep,
    settings: SettingsDep,
    other_document_id: OtherDocument = None,
) -> IfcDiff:
    """What `other_index` (of this document, or of `other_document_id`) has that this version lacks, what it lacks,
    and what differs, by GlobalId and path. Computed on request; a large model takes a while. 404 when the BIM
    functions are off or a version is not yours, 400 when a version is not an IFC file."""
    if ifcgraph is None:
        raise HTTPException(404, "The BIM functions are off on this server")
    a = await _version(documents, principal.user_id, document_id, version_index)
    b = await _version(documents, principal.user_id, other_document_id or document_id, other_index)
    if a.document_id == b.document_id and a.version_index == b.version_index:
        raise HTTPException(400, "A version is compared with another one")
    try:
        report = await ifcgraph.diff(
            a=(a.storage_key, a.file_name), b=(b.storage_key, b.file_name), by_guid=a.document_id != b.document_id
        )
    except RuntimeError as exc:
        raise HTTPException(400, f"The comparison could not be made: {exc}") from None
    return IfcDiff(
        a=IfcDiffSide(document_id=a.document_id, version_index=a.version_index, commit=a.commit),
        b=IfcDiffSide(document_id=b.document_id, version_index=b.version_index, commit=b.commit),
        **report,
    )


async def _side(
    documents: Any, git: Git | None, user_id: str, latest: VersionRecord, value: str, folder: FilePath
) -> tuple[IfcDiffSide, tuple[str, str] | FilePath]:
    """A side of the comparison: a version of the document, or a commit of its history read out of the repository
    into ``folder``; 404 for a commit that is not the project's or does not hold the document."""
    if len(value) < 40:
        record = await _version(documents, user_id, latest.document_id, int(value))
        side = IfcDiffSide(document_id=record.document_id, version_index=record.version_index, commit=record.commit)
        return side, (record.storage_key, record.file_name)
    where = tree_path(latest.document_id, latest.file_name)
    repository = git.repository(latest.project_id) if git is not None else None
    if git is None or repository is None or where is None or not repository.is_dir():
        raise HTTPException(404, "No such commit of the document")
    if not await git.holds(repository, value, where):
        raise HTTPException(404, "No such commit of the document")
    target = folder / f"{value}.ifc"
    await git.blob_to(repository, value, where, target)
    versions = {
        record.commit: record.version_index
        for record in await documents.versions(user_id=user_id, document_id=latest.document_id)
        if record.commit
    }
    return IfcDiffSide(document_id=latest.document_id, version_index=versions.get(value), commit=value), target


@router.get(
    "/documents/{document_id}/ifc-diff",
    summary="Compare two versions or commits of an IFC document element by element",
    response_model=IfcDiff,
    response_model_exclude_none=True,
)
async def ifc_diff_of_sides(
    principal: DocumentsPrincipal,
    document_id: DocumentId,
    documents: DocumentsDep,
    ifcgraph: IfcGraphDep,
    git: GitDep,
    a: Side,
    b: Side,
) -> IfcDiff:
    """What `b` has that `a` lacks, what it lacks, and what differs, where each is a version's index or a commit of
    the document's history (`GET …/history`): an upload as it came, a trial's file, an upload that did not merge.
    404 when the BIM functions are off, the document is not yours, or a commit is not the document's; 400 when the
    document is not an IFC file or the two sides are one."""
    if ifcgraph is None:
        raise HTTPException(404, "The BIM functions are off on this server")
    if a == b:
        raise HTTPException(400, "A side is compared with another one")
    if not is_uuid(document_id):
        raise HTTPException(404, "Document not found")
    latest = await documents.latest(user_id=principal.user_id, document_id=normalize_guid(document_id))
    if latest is None:
        raise HTTPException(404, "Document not found")
    if not IfcGraphService.wants(latest.file_name):
        raise HTTPException(400, f"{latest.title} is not an IFC file")
    with tempfile.TemporaryDirectory(prefix="ifcdiff-sides-") as folder:
        side_a, input_a = await _side(documents, git, principal.user_id, latest, a, FilePath(folder))
        side_b, input_b = await _side(documents, git, principal.user_id, latest, b, FilePath(folder))
        if side_a.commit is not None and side_a.commit == side_b.commit:
            raise HTTPException(400, "A side is compared with another one")
        try:
            report = await ifcgraph.diff(a=input_a, b=input_b)
        except RuntimeError as exc:
            raise HTTPException(400, f"The comparison could not be made: {exc}") from None
    return IfcDiff(a=side_a, b=side_b, **report)
