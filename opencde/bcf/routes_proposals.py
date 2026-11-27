# This file was generated with the assistance of an AI coding tool.
"""The trial of a topic's change set: an extension of the BCF API (ADR 0021).

When a topic's ``bim_snippet`` of the type ``DataregiumIfcChangeSet`` is uploaded and the IFC-DB module is on, the
server applies the changes to the base version's file in a worker process (Dataspatium's write tools first, with the
app's own code, where the change set carries them as ``ops``: ``ifcgraph/ops.py``), runs the checks an upload gets on
the result and compares the findings with the base version's: the proposal is ``clean`` when every change and
operation applied and no finding is new, else ``issues``; the rooms of the applied file, where the server finds them,
are shown beside and never change that (F38 a). ``GET /bcf/3.0/projects/{id}/topics/{guid}/ifc-proposal``
gives the trial to every member of the project, with the same bearer token as the topic.
``POST …/ifc-proposal/approve`` lets a ``manager``,
or a team's responsible (below), make a clean trial the next version of the base document: the applied file becomes
the version, with the base, the change set and who proposed and approved it in its metadata; the topic gets a comment
with the version's address and the status ``Closed`` where the project has it. An AI only proposes: approval is a
person's.

Where the project has teams (``accounts/teams.py``), the trial also finds the teams whose part of the model the
change set touches. A change to the part of one team is approved by that team's responsible alone (F20 a); one that
touches the parts of several teams, or of none, by a ``manager`` (F21 b), and so is one whose parts could not be
found. The responsible is the team's at the time of the approval, so a manager who sets another one lets them
approve.

Where the project has a repository (ADR 0030), the trial's file is a commit on the proposal's branch, on the base
version's commit, and the approval a merge commit of main and it, with the one who approved it as the committer.
When the document has moved on since the base, ifcmerge merges the trial's file with the versions since, as it
merges an upload (``documents/three_way.py``): the merged file is checked against the latest version's findings and
its teams found again, and it becomes the version; the ids ifcmerge moved are in the trial's ``renumbered`` and the
version's ``ifc_renumbered``. A collision is 412 with ifcmerge's conflicts, and a server without ifcmerge answers
412 with ``["*"]``, for the client to propose again on the latest version.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any

import anyio
from fastapi import APIRouter, HTTPException
from ifc_db.validation import pack_report
from pydantic import BaseModel, ConfigDict, Field

from opencde.accounts.roles import Role, at_least
from opencde.bcf.schemas import CommentPOST, Component, Components, TopicPUT, ViewpointPOST, Visibility
from opencde.bcf.topic_access import TopicCtx, TopicsDep
from opencde.deps import GitDep, ProjectsDep, SettingsDep, StorageDep, UsersDep
from opencde.documents import three_way
from opencde.documents.links import api_base
from opencde.documents.routes_clash import camera_looking_at
from opencde.documents.schemas import DocumentMetadataEntry, DocumentVersion
from opencde.documents.step import MAX_LISTED, is_step, protect_stored, stored_history
from opencde.documents.validation import IfcRooms, IfcValidationCheck, IfcValidationIssue, rooms_of
from opencde.errors import CODE_VERSION_CONFLICT, ApiError
from opencde.ids import new_id
from opencde.ifcgraph.parts import owning_objects
from opencde.ifcgraph.service import STATE_CLEAN, IfcGraphDep, IfcGraphService
from opencde.logging import get_logger
from opencde.repos.git import Git, GitError, proposal_ref
from opencde.repos.writer import prepare, propose
from opencde.storage import LocalStorage, make_key
from opencde.store.accounts.teams import TeamsDep
from opencde.store.bcf.comments import CommentsDep
from opencde.store.bcf.viewpoints import ViewpointsDep
from opencde.store.documents.versions import DocumentsDep, VersionRecord, default_metadata
from opencde.store.protocols import TeamStore
from opencde.timeutil import now_iso

TAG = "IFC change sets (extension)"

router = APIRouter(prefix="/projects/{project_id}/topics/{topic_id}", tags=[TAG])
log = get_logger(__name__)

CLOSED = "Closed"  # the topic's status after the approval, where the project's values have it


class IfcProposalBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str
    version_index: int


class IfcProposalTeam(BaseModel):
    team_id: str
    name: str


class IfcProposalIssue(IfcValidationIssue):
    op: int | None = Field(None, description="For `op`: the operation that failed, by its index in `ops` from 0.")
    tool: str | None = Field(None, description="For `op`: the tool of the operation that failed.")


class IfcProposalReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: str = Field(
        description="`queued` or `running` until the trial is over; then `clean` when every change applied and no "
        "finding is new compared with the base version, `issues` otherwise, or `failed` when the trial could not run."
    )
    created_at: str = Field(description="When the change set was received, ISO 8601 in UTC.")
    checked_at: str | None = Field(None, description="When the trial ended.")
    base: IfcProposalBase | None = Field(None, description="The document version the changes were applied to.")
    change_count: int | None = Field(None, description="How many changes and operations the set holds.")
    applied: int | None = Field(
        None, description="How many of them applied; the rest are issues of kind `proposal` or `op`."
    )
    issue_count: int | None = Field(None, description="The new findings and the changes that did not apply.")
    error_count: int | None = Field(None, description="How many of the issues are errors.")
    truncated: bool = Field(False, description="True when the checks found more issues than are listed.")
    ruleset: str | None = Field(None, description="What the result was checked with.")
    checks: list[IfcValidationCheck] = Field(default_factory=list)
    issues: list[IfcProposalIssue] = Field(
        default_factory=list,
        description="The findings the base version lacks, as in a version's report, a `proposal` issue per change or "
        "`keep_theirs` key that could not be applied, which says why without quoting the file, and an `op` issue for "
        "the operation that failed, in the words of Dataspatium's code, which may quote its arguments and the file.",
    )
    created: dict[str, str] | None = Field(
        None,
        description="For a change set with `ops`: the GlobalId of each element the operations created, by its ref "
        "(`new-1`, `new-2` …).",
    )
    notes: list[str] | None = Field(
        None,
        description="For a change set with `ops`: what an operation could not do in the file's schema, such as the "
        "map conversion in IFC2X3.",
    )
    error: str | None = Field(None, description="Why the trial could not run, when it `failed`.")
    approved_by: str | None = Field(None, description="Who approved it, when it is `approved`.")
    version_index: int | None = Field(None, description="The version it became, when it is `approved`.")
    merged_with_version_index: int | None = Field(
        None,
        description="The latest version that the approval merged the change set with, by ifcmerge, when the document "
        "had moved on since the base: the state, the issues and the teams are then those of the merged file against "
        "that version.",
    )
    renumbered: dict[str, int] | None = Field(
        None,
        description="When it is `approved` and the merge moved ids of the trial's file: each such STEP id, as a "
        f"string, and the id it has in the version, the first {MAX_LISTED} in order. The GlobalIds in `created` "
        "stay as they are.",
    )
    renumbered_count: int | None = Field(None, description="How many ids moved, when more than `renumbered` lists.")
    teams: list[IfcProposalTeam] = Field(
        default_factory=list, description="The teams whose part of the model the change set touches."
    )
    teams_error: str | None = Field(None, description="Why the teams' parts could not be found, when they could not.")
    rooms: IfcRooms | None = Field(
        None,
        description="The rooms of each storey of the applied file, as in a version's report, when the server finds "
        "them: they only inform, and neither `state` nor the approval depends on them (F38 a). Left out when it is off "
        "or the rooms could not be found.",
    )


def proposal_report(node: dict[str, Any]) -> IfcProposalReport:
    """The trial as ``GET …/ifc-proposal`` gives it, from the service's node; the MCP endpoint's too."""
    base = None
    if node.get("base_document_id"):
        base = IfcProposalBase(document_id=str(node["base_document_id"]), version_index=int(node["base_version_index"]))
    return IfcProposalReport(
        state=str(node["state"]),
        created_at=str(node["created_at"]),
        checked_at=node.get("checked_at"),
        base=base,
        change_count=None if node.get("change_count") is None else int(node["change_count"]),
        applied=None if node.get("applied") is None else int(node["applied"]),
        issue_count=None if node.get("issue_count") is None else int(node["issue_count"]),
        error_count=None if node.get("error_count") is None else int(node["error_count"]),
        truncated=bool(node.get("truncated")),
        ruleset=node.get("ruleset"),
        checks=[IfcValidationCheck.model_validate(check) for check in node["checks"]],
        issues=[IfcProposalIssue.model_validate(issue) for issue in node["issues"]],
        created=node.get("created"),
        notes=node.get("notes"),
        error=node.get("error"),
        approved_by=node.get("approved_by"),
        version_index=None if node.get("version_index") is None else int(node["version_index"]),
        merged_with_version_index=(
            None if node.get("merged_with_version_index") is None else int(node["merged_with_version_index"])
        ),
        renumbered=node.get("renumbered"),
        renumbered_count=None if node.get("renumbered_count") is None else int(node["renumbered_count"]),
        teams=[
            IfcProposalTeam(team_id=str(team["team_id"]), name=str(team["name"])) for team in node.get("teams") or []
        ],
        teams_error=node.get("teams_error"),
        rooms=rooms_of(node.get("rooms")),
    )


async def _may_approve(topic: TopicCtx, teams: TeamStore, node: dict[str, Any]) -> None:
    """F20 a and F21 b: the responsible of the one team whose part the change set touches approves it, as that team
    has it set up now; a change set that touches the parts of several teams, or of none, or whose parts are unknown,
    a ``manager``."""
    current = {team["team_id"]: team for team in await teams.of_project(topic.project_id)}
    touched = [current[team["team_id"]] for team in node.get("teams") or [] if team.get("team_id") in current]
    if len(touched) == 1 and not node.get("teams_error"):
        if topic.user_id != touched[0]["responsible_user_id"]:
            raise HTTPException(
                403, f"Only the responsible of the team {touched[0]['name']} approves a change to its part"
            )
        return
    if not at_least(topic.role, Role.MANAGER):
        raise HTTPException(
            403,
            "Only a manager approves a change set" + (" that touches the parts of several teams" if touched else ""),
        )


@router.get(
    "/ifc-proposal",
    summary="Get the trial of the topic's change set against its base version",
    response_model=IfcProposalReport,
    response_model_exclude_none=True,
)
async def proposal_get(topic: TopicCtx, ifcgraph: IfcGraphDep) -> IfcProposalReport:
    """The state of the trial and its findings. 404 when the topic's snippet is not a change set that was tried, or
    the BIM functions are off."""
    if ifcgraph is None:
        raise HTTPException(404, "Change sets are not tried on this server: the BIM functions are off")
    node = await ifcgraph.proposal(user_id=topic.user_id, project_id=topic.project_id, topic_guid=topic.guid)
    if node is None:
        raise HTTPException(404, "This topic has no change set that was tried")
    return proposal_report(node)


def _copy_hashed(storage: LocalStorage, source: Path, key: str) -> tuple[int, str]:
    """The applied file copied into the documents, with its size and SHA-256 as an upload has them."""
    digest = hashlib.sha256()
    with source.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return storage.copy_in(source, key), digest.hexdigest()


def _change_set(storage: LocalStorage, snippet_key: str) -> dict[str, Any]:
    with storage.open_read(snippet_key) as handle:
        return dict(json.loads(handle.read().decode("utf-8")))


@router.post(
    "/ifc-proposal/approve",
    summary="Approve the topic's change set: its applied file becomes the next version of the base document",
    response_model=DocumentVersion,
    response_model_exclude_none=True,
    status_code=201,
)
async def proposal_approve(
    topic: TopicCtx,
    ifcgraph: IfcGraphDep,
    documents: DocumentsDep,
    comments: CommentsDep,
    topics: TopicsDep,
    viewpoints: ViewpointsDep,
    projects: ProjectsDep,
    storage: StorageDep,
    settings: SettingsDep,
    teams: TeamsDep,
    users: UsersDep,
    git: GitDep,
) -> DocumentVersion:
    """A `manager`, or the responsible of the one team whose part the change set touches, approves a `clean` trial:
    the answer is the new version as the Documents API gives it, with
    `base_version_index`, `change_set_id`, `proposed_by`, `approved_by` and, where an AI proposed it, `ai_model` and
    `tool_call` in its metadata, and the topic gets a viewpoint that selects the changed elements (OB-32). When the
    document has moved on since the base, ifcmerge merges the trial's file with the latest version (ADR 0030): the
    merged file is tried against the latest's findings and becomes the version, with `merged_with_version_index` and
    the ids the merge moved in `ifc_renumbered`. 412 `version_conflict` with `conflicts`, `conflict_details` and `merge`
    when the two collide, or with `["*"]` when the files cannot be merged or the server has no ifcmerge; 409 when the
    trial is not clean, was approved already, or has a new finding once merged. 403 for other roles."""
    if ifcgraph is None:
        raise HTTPException(404, "Change sets are not tried on this server: the BIM functions are off")
    node = await ifcgraph.proposal(user_id=topic.user_id, project_id=topic.project_id, topic_guid=topic.guid)
    if node is None:
        raise HTTPException(404, "This topic has no change set that was tried")
    await _may_approve(topic, teams, node)
    if node["state"] != STATE_CLEAN:
        raise HTTPException(409, f"The trial is {node['state']}; only a clean one is approved")
    document_id, base_index = str(node["base_document_id"]), int(node["base_version_index"])
    latest = await documents.latest(user_id=topic.user_id, document_id=document_id)
    if latest is None:
        raise HTTPException(404, "The base document is gone")
    based = latest
    if latest.version_index != base_index:
        found = await documents.version(user_id=topic.user_id, document_id=document_id, version_index=base_index)
        if found is None:
            raise HTTPException(404, "The base version is gone")
        based = found
    change_set = await anyio.to_thread.run_sync(_change_set, storage, str(node["snippet_key"]))
    provenance = dict(change_set.get("provenance") or {})
    proposer = str(node["author"])
    proposing = await users.get_by_username(proposer)
    branch = node.get("commit") or await _branch_now(git, storage, topic.project_id, node, based, proposing)
    key = make_key("documents", new_id())
    renumbered: list[tuple[int, int]] = []
    renumbered_count = 0
    merged_with = None
    try:
        if latest.version_index != base_index:
            size, sha256, renumbered, renumbered_count = await _merged(
                ifcgraph, storage, topic.project_id, node, change_set, (based, latest), key
            )
            merged_with = latest.version_index
            node = await ifcgraph.proposal(user_id=topic.user_id, project_id=topic.project_id, topic_guid=topic.guid)
            if node is None:
                raise HTTPException(404, "This topic has no change set that was tried")
            await _may_approve(topic, teams, node)  # merged with the latest, it may touch other parts
        else:
            size, sha256 = await anyio.to_thread.run_sync(
                _copy_hashed, storage, storage.path(str(node["storage_key"])), key
            )
        metadata = default_metadata(
            file_name=latest.file_name,
            size_in_bytes=size,
            sha256=sha256,
            author=proposer,
            project_name=latest.project_name,
        )
        metadata += [
            DocumentMetadataEntry(name="base_version_index", value=[str(base_index)], data_type="integer32"),
            DocumentMetadataEntry(name="change_set_id", value=[topic.guid], data_type="string"),
            DocumentMetadataEntry(name="proposed_by", value=[proposer], data_type="string"),
            DocumentMetadataEntry(name="approved_by", value=[topic.username], data_type="string"),
        ]
        for name in ("ai_model", "tool_call"):
            if provenance.get(name):
                metadata.append(DocumentMetadataEntry(name=name, value=[str(provenance[name])], data_type="string"))
        if merged_with is not None:
            metadata.append(
                DocumentMetadataEntry(name="merged_with_version_index", value=[str(merged_with)], data_type="integer32")
            )
        metadata += three_way.renumbered_metadata(renumbered, renumbered_count)
        commit = await prepare(
            git,
            project_id=topic.project_id,
            path=storage.path(key),
            file_name=latest.file_name,
            title=latest.title,
            author=proposer,
            author_id=proposing.id if proposing else None,
            message=f"Change set {topic.guid}",
            base=base_index,
        )
        if commit is not None:
            commit.proposal_commit, commit.proposal_id = branch, str(node["id"])
            commit.approver, commit.approver_id = topic.username, topic.user_id
            commit.merged_with = merged_with
        record = await documents.create_version(
            project_id=topic.project_id,
            document_id=document_id,
            title=latest.title,
            version_number=str(latest.version_index + 1),
            file_name=latest.file_name,
            size_in_bytes=size,
            sha256=sha256,
            storage_key=key,
            author=proposer,
            metadata=metadata,
            parent_version_index=base_index,
            approved_by=topic.username,
            change_set_id=topic.guid,
            change_set=pack_report(list(change_set.get("changes") or [])),
            on_latest=latest.version_index,  # a version made meanwhile is 412, as for an upload
            max_step_id=await anyio.to_thread.run_sync(stored_history, storage, key, latest.file_name),
            commit=commit,
        )
    except BaseException:
        storage.delete(key)
        raise
    await ifcgraph.approve(
        proposal_id=str(node["id"]),
        approved_by=topic.username,
        version_index=record.version_index,
        renumbered={str(old): new for old, new in renumbered} or None,
        renumbered_count=renumbered_count if renumbered_count > len(renumbered) else None,
    )
    await _drop_branch(git, topic.project_id, str(node["id"]), branch)
    await _schedule(ifcgraph, topic.project_id, record.storage_key, record.file_name, record)
    version = record.to_model(api_base(settings))
    await comments.create(
        project_id=topic.project_id,
        topic_guid=topic.guid,
        author=topic.username,
        body=CommentPOST(
            comment=f"Approved as version {record.version_index} of {latest.title}: {version.links.document_version.url}"
        ),
    )
    touched = [*(change_set.get("changes") or []), *({"guid": guid} for guid in node.get("ops_objects") or [])]
    guids, centre, extent = await anyio.to_thread.run_sync(
        _changed_guids, storage.path(str(node["storage_key"])), touched
    )
    if guids:
        await viewpoints.create(
            project_id=topic.project_id,
            topic_guid=topic.guid,
            author=topic.username,
            body=ViewpointPOST(
                perspective_camera=camera_looking_at(centre, extent),
                components=Components(
                    selection=[Component(ifc_guid=guid) for guid in guids],
                    visibility=Visibility(default_visibility=True),
                ),
            ),
        )
    statuses = (await projects.extensions(topic.project_id) or {}).get("topic_status") or []
    if CLOSED in statuses and topic.topic.topic_status != CLOSED:
        fields = topic.topic.model_dump(include=set(TopicPUT.model_fields))
        body = TopicPUT.model_validate({**fields, "topic_status": CLOSED})
        await topics.update(project_id=topic.project_id, guid=topic.guid, author=topic.username, body=body)
    return version


async def _merged(
    ifcgraph: IfcGraphService,
    storage: LocalStorage,
    project_id: str,
    node: dict[str, Any],
    change_set: dict[str, Any],
    versions: tuple[VersionRecord, VersionRecord],
    key: str,
) -> tuple[int, str, list[tuple[int, int]], int]:
    """The trial's file merged by ifcmerge with the versions since its base (ADR 0030), stored at ``key`` with the
    safety net on it, and tried on the latest version: its size and SHA-256, and the ids of the trial's file that are
    not where they were (``three_way.renumbering``). 412 when the server cannot merge or the two collide, 409 when the
    merged file has a finding the latest version lacks."""
    based, latest = versions
    if not ifcgraph.settings.ifcmerge_on or not (is_step(based.file_name) and is_step(latest.file_name)):
        raise ApiError(
            412,
            f"The change set builds on version {based.version_index}, but the document's latest version is "
            f"{latest.version_index}, and this server does not merge it: propose it again on the latest",
            code=CODE_VERSION_CONFLICT,
            details={
                "conflicts": [three_way.EVERYTHING],
                "conflict_details": [],
                "merge": {"base_version_index": based.version_index, "latest_version_index": latest.version_index},
            },
        )
    ours = storage.path(str(node["storage_key"]))
    with tempfile.TemporaryDirectory(prefix="ifcmerge-") as folder:
        try:
            done = await three_way.merged(
                ifcgraph=ifcgraph,
                storage=storage,
                what="The change set",
                project_id=project_id,
                based=based,
                latest=latest,
                ours=(str(node["storage_key"]), "proposal.ifc"),
                folder=Path(folder),
            )
        except ApiError as exc:
            await _pair(ifcgraph, based, change_set, exc)
            raise
        size, sha256 = await anyio.to_thread.run_sync(_copy_hashed, storage, done.path, key)
    after = await anyio.to_thread.run_sync(
        protect_stored, storage, key, latest.storage_key, latest.file_name, latest.max_step_id
    )
    if after.size is not None and after.sha256 is not None:
        size, sha256 = after.size, after.sha256
    pairs, count = await anyio.to_thread.run_sync(three_way.renumbering, ours, done, after)
    touching = [*(change_set.get("changes") or []), *({"guid": guid} for guid in node.get("ops_objects") or [])]
    state = await ifcgraph.merged_trial(
        proposal_id=str(node["id"]), project_id=project_id, latest=latest, key=key, touching=touching
    )
    if state != STATE_CLEAN:
        raise HTTPException(409, f"Merged with version {latest.version_index}, the trial is {state}; see the trial")
    return size, sha256, pairs, count


async def _pair(ifcgraph: IfcGraphService, based: VersionRecord, change_set: dict[str, Any], exc: ApiError) -> None:
    """Each conflict of a 412 with ``change_index``, the place in the change set's ``changes`` of the change to the
    same instance and attribute, read in the base the change set was made on, for the client to pair them; null for
    a conflict no change names (one the operations made)."""
    listed = (exc.details or {}).get("conflict_details") or []
    changes = [dict(change) for change in change_set.get("changes") or []]
    if not listed or not changes:
        return
    try:
        reached = await ifcgraph.values_of(based.storage_key, based.file_name, changes)
    except RuntimeError:
        reached = []
    places: dict[tuple[int, str | None], int] = {}
    for index, (change, found) in enumerate(zip(changes, reached, strict=False)):
        if found.get("step_id") is not None:
            places.setdefault((int(found["step_id"]), change.get("attribute")), index)
            places.setdefault((int(found["step_id"]), None), index)
    for detail in listed:
        at = (int(detail["step_id"]), detail.get("attribute")) if detail.get("step_id") is not None else None
        detail["change_index"] = places.get(at) if at is not None else None


async def _branch_now(
    git: Git | None,
    storage: LocalStorage,
    project_id: str,
    node: dict[str, Any],
    based: VersionRecord,
    proposing: Any,
) -> str | None:
    """The proposal's branch for a trial that has none, as one from before the branches (UL-48): made now, or None.
    A failure is logged and stops nothing: the version is then a commit on main alone."""
    try:
        return await propose(
            git,
            project_id=project_id,
            proposal_id=str(node["id"]),
            path=storage.path(str(node["storage_key"])),
            document_id=based.document_id,
            file_name=based.file_name,
            title=based.title,
            base=based.version_index,
            base_commit=based.commit,
            author=str(node["author"] or ""),
            author_id=proposing.id if proposing else None,
            created=now_iso(),
        )
    except (GitError, OSError, ValueError) as exc:
        log.warning("ifc_proposal_branch_failed", proposal_id=str(node["id"]), error=str(exc)[:200])
        return None


async def _drop_branch(git: Git | None, project_id: str, proposal_id: str, commit: str | None) -> None:
    """The proposal's branch goes once it is approved: its commit is the version's second parent. One left behind is
    taken by the repositories' upkeep."""
    if git is None or commit is None:
        return
    try:
        await git.delete_ref(git.repository(project_id), proposal_ref(proposal_id), commit)
    except (GitError, ValueError) as exc:
        log.warning("ifc_proposal_branch_left", proposal_id=proposal_id, error=str(exc)[:200])


MAX_SELECTED = 1_000


def _changed_guids(applied: Path, changes: list[dict[str, Any]]) -> tuple[list[str], list[float], float | None]:
    """The objects the changes touched, for the viewpoint: their GlobalIds, where they are (the middle of their
    placements, in metres) and how far they spread. A change named by GlobalId gives its object; one named by
    STEP id gives the nearest object that refers to the instance, a few steps back."""
    import ifcopenshell
    import ifcopenshell.util.placement
    import ifcopenshell.util.unit

    model = ifcopenshell.open(str(applied))
    guids: set[str] = set()
    for change in changes:
        if change.get("guid"):
            guids.add(str(change["guid"]))
        elif change.get("step_id") is not None:
            try:
                guids.update(owning_objects(model, model.by_id(int(change["step_id"]))))
            except RuntimeError:
                continue
        if len(guids) >= MAX_SELECTED:
            break
    selected = sorted(guids)[:MAX_SELECTED]
    scale = float(ifcopenshell.util.unit.calculate_unit_scale(model))
    points: list[list[float]] = []
    for guid in selected:
        try:
            placement = getattr(model.by_guid(guid), "ObjectPlacement", None)
        except RuntimeError:
            continue
        if placement is not None and placement.is_a("IfcLocalPlacement"):
            matrix = ifcopenshell.util.placement.get_local_placement(placement)
            points.append([float(matrix[i][3]) * scale for i in range(3)])
    if not points:
        return selected, [0.0, 0.0, 0.0], None
    centre = [sum(point[i] for point in points) / len(points) for i in range(3)]
    extent = max((max(abs(point[i] - centre[i]) for point in points) for i in range(3)), default=0.0) * 2
    return selected, centre, extent or None


async def _schedule(ifcgraph: IfcGraphService, project_id: str, key: str, file_name: str, record: Any) -> None:
    """The new version is converted and checked as an upload is."""
    await ifcgraph.schedule(
        project_id=project_id,
        storage_key=key,
        file_name=file_name,
        document_id=record.document_id,
        version_index=record.version_index,
        version_id=record.version_id,
    )
