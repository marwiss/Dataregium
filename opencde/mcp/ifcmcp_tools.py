# This file was generated with the assistance of an AI coding tool.
"""IfcMCP's tools behind the MCP endpoint (ADR 0031, F60, F204 a, F205 d).

The reading tools work on a version of an IFC document of the caller's projects, or on a working copy; ``ifc_open``
makes a copy of a version's file for the caller (``ifc_copies``, with ``expires_at``, at most ``MAX_COPIES`` open),
the editing tools change it, and ``ifc_propose`` makes it a proposal: a BCF topic whose snippet is the whole file
(``DataregiumIfcFile``, SA-129), tried on the copy's version and approved by a person, never by a tool. Every call
runs in a worker (``opencde.ifcgraph.mcp_worker``) with the server's own path; nothing a caller sends names a file.
A working copy of a large model keeps its worker between the calls (``opencde.ifcgraph.residents``, F210 c).
What an answer quotes from a model is data. The pictures are Dataspatium's (F205 d): there is no drawing tool here.
"""

from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import HTTPException
from pydantic import Field

from opencde.accounts.roles import PROPOSER, at_least
from opencde.auth.scopes import MCP_PROPOSE
from opencde.bcf.schemas import TEXT_MAX, BimSnippet, TopicPOST
from opencde.documents.changes import FILE_SNIPPET_TYPE
from opencde.documents.step import is_step
from opencde.ids import is_uuid, new_id, normalize_guid
from opencde.ifcgraph.residents import CopyLost
from opencde.mcp.registry import MAX_ANSWER_BYTES, Arguments, Tool, ToolContext, ToolRefusal, member_project
from opencde.mcp.registry import member_version as version_of
from opencde.repos.reading import restore_record
from opencde.storage import make_key, snippet_key
from opencde.store.accounts.postgres import project_store
from opencde.store.bcf.postgres import PostgresTopicLinkRepository, PostgresTopicRepository
from opencde.store.bcf.topics import validate_against_extensions
from opencde.store.documents.postgres import document_store
from opencde.store.jobs.postgres import PostgresWorkingCopies
from opencde.timeutil import later_iso

if TYPE_CHECKING:
    from opencde.ifcgraph.service import IfcGraphService
    from opencde.postgres.database import Postgres
    from opencde.storage import LocalStorage

MAX_COPIES = 3  # the working copies one user has open at once
COPY_SECONDS = 4 * 3600  # how long a copy lasts after its last call
MAX_TITLE = 200
MAX_PARAMS = 100_000
MODULE_OFF = "IfcMCP's tools are not on this server: the BIM functions or PostgreSQL are off"
_BUDGET = MAX_ANSWER_BYTES - 4096  # what the worker's answer may take of the tool's
SCHEMAS = {  # the address of each schema's documentation, for the snippet's reference_schema
    "IFC2X3": "https://standards.buildingsmart.org/IFC/RELEASE/IFC2x3/TC1/HTML/",
    "IFC4": "https://standards.buildingsmart.org/IFC/RELEASE/IFC4/ADD2_TC1/HTML/",
    "IFC4X3": "https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3/",
}

CopyId = Annotated[str, Field(max_length=64, description="The working copy's handle, as ifc_open gave it.")]
OptionalCopy = Annotated[
    str | None,
    Field(max_length=64, description="A working copy's handle from ifc_open; null to read a version instead."),
]
DocumentId = Annotated[
    str | None,
    Field(max_length=64, description="With no copy: the IFC document, as list_models gave it; null with a copy."),
]
VersionIndex = Annotated[int | None, Field(ge=1, description="With no copy: the version; null for the latest.")]


class ModelArguments(Arguments):
    """The model a reading tool reads: a working copy, or a version of a document."""

    copy_id: OptionalCopy
    document_id: DocumentId
    version_index: VersionIndex


class ElementArguments(ModelArguments):
    element_id: int = Field(ge=1, description="The instance's STEP id, as ifc_select or ifc_tree gave it.")


class SelectArguments(ModelArguments):
    query: str = Field(min_length=1, max_length=2000, description="An IfcOpenShell selector, such as IfcWall.")


class RelationsArguments(ElementArguments):
    traverse: str | None = Field(max_length=100, description="A relationship to follow further; null for none.")


class ValidateArguments(ModelArguments):
    express_rules: bool | None = Field(description="true also checks the schema's EXPRESS rules, which takes longer.")


class DepthArguments(ModelArguments):
    max_depth: int | None = Field(ge=1, le=50, description="How deep the tree goes; null for all of it.")


class SchemaArguments(ModelArguments):
    entity_type: str = Field(min_length=1, max_length=100, description="An IFC class, such as IfcWall.")


class ListArguments(Arguments):
    module: str | None = Field(max_length=100, description="An API module, such as root; null for the modules.")


class DocsArguments(Arguments):
    function_path: str = Field(max_length=200, description="module.function, as ifc_list names it.")


class ShapeDocsArguments(Arguments):
    method: str = Field(max_length=100, description="A ShapeBuilder method, as ifc_shape_list names it.")


class EditArguments(Arguments):
    copy_id: CopyId
    function_path: str = Field(max_length=200, description="module.function, as ifc_list names it.")
    params: str = Field(
        max_length=MAX_PARAMS,
        description="The function's keyword arguments as a JSON object, entities as their STEP ids; {} for none.",
    )


class QuantifyArguments(Arguments):
    copy_id: CopyId
    rule: str = Field(max_length=100, description="A quantity rule set, such as IFC4QtoBaseQuantities.")
    selector: str | None = Field(max_length=2000, description="An IfcOpenShell selector; null for every element.")


class ShapeArguments(Arguments):
    copy_id: CopyId
    method: str = Field(max_length=100, description="A ShapeBuilder method, as ifc_shape_list names it.")
    params: str = Field(max_length=MAX_PARAMS, description="Its keyword arguments as a JSON object; {} for none.")


class OpenArguments(Arguments):
    document_id: str = Field(max_length=64, description="The IFC document, as list_models gave it.")
    version_index: VersionIndex


class CopyArguments(Arguments):
    copy_id: CopyId


class ProposeArguments(CopyArguments):
    title: str = Field(min_length=1, max_length=MAX_TITLE, description="The topic's title: what the change does.")
    description: str | None = Field(max_length=TEXT_MAX, description="The topic's description: why, what to look at.")
    ai_model: str | None = Field(
        max_length=200, description="The AI model that made it, as you know yourself; kept with the proposal."
    )


@dataclass(frozen=True)
class _Here:
    """What the tools need, all of it there: the copies, the storage, the BIM functions' service, PostgreSQL."""

    copies: PostgresWorkingCopies
    storage: LocalStorage
    ifcgraph: IfcGraphService
    postgres: Postgres


def _here(context: ToolContext) -> _Here:
    if context.ifcgraph is None or context.storage is None or context.postgres is None:
        raise ToolRefusal(MODULE_OFF)
    return _Here(PostgresWorkingCopies(context.postgres), context.storage, context.ifcgraph, context.postgres)


async def _copy(context: ToolContext, copy_id: str) -> dict[str, Any]:
    """The caller's own open copy, its time extended; a refusal for any other."""
    copies, user_id = _here(context).copies, context.principal.user_id
    found = await copies.get(user_id=user_id, copy_id=normalize_guid(copy_id)) if is_uuid(copy_id) else None
    if found is None:
        raise ToolRefusal("There is no such working copy of yours: ifc_open makes one, and one expires unused")
    await member_project(context, str(found["project_id"]))
    await copies.touch(user_id=user_id, copy_id=str(found["copy_id"]), expires_at=later_iso(COPY_SECONDS))
    return found


async def _model(context: ToolContext, arguments: ModelArguments) -> dict[str, Any]:
    """Where a reading tool reads: the copy's file, or the version's, read back out of git when it must be."""
    here = _here(context)
    if arguments.copy_id is not None:
        found = await _copy(context, arguments.copy_id)
        return {"path": here.storage.path(str(found["storage_key"])), "copy": True}
    if arguments.document_id is None:
        raise ToolRefusal("Name a working copy, or a document and its version")
    record = await version_of(context, arguments.document_id, arguments.version_index)
    if not is_step(record.file_name):
        raise ToolRefusal("The version is not an IFC file in STEP")
    await restore_record(here.ifcgraph.git, here.storage, record)
    return {"path": here.storage.path(record.storage_key), "copy": False}


async def _answer(context: ToolContext, tool: str, arguments: dict[str, Any], **where: Any) -> dict[str, Any]:
    try:
        return await _here(context).ifcgraph.ifcmcp(tool, arguments, budget=_BUDGET, **where)
    except CopyLost as exc:
        raise ToolRefusal(str(exc)) from None
    except RuntimeError:
        raise ToolRefusal("The tool could not finish on this model, in the time or the memory a call has") from None


def _reading(tool: str, model: type[ModelArguments], title: str, description: str) -> Tool:
    async def run(context: ToolContext, arguments: Any) -> dict[str, Any]:
        where = await _model(context, arguments)
        given = arguments.model_dump(exclude={"copy_id", "document_id", "version_index"}, exclude_none=True)
        return await _answer(context, tool, given, path=where["path"], copy=where["copy"])

    return Tool(name=tool, title=title, description=description, arguments=model, run=run, needs_ifcgraph=True)


def _discovering(tool: str, model: type[Arguments], title: str, description: str) -> Tool:
    async def run(context: ToolContext, arguments: Any) -> dict[str, Any]:
        return await _answer(context, tool, arguments.model_dump(exclude_none=True), path=None)

    return Tool(name=tool, title=title, description=description, arguments=model, run=run, needs_ifcgraph=True)


def _params(raw: str) -> dict[str, Any]:
    try:
        given = json.loads(raw or "{}")
    except ValueError:
        raise ToolRefusal("params is not a JSON object") from None
    if not isinstance(given, dict):
        raise ToolRefusal("params is not a JSON object")
    return given


async def _proposer(context: ToolContext, project_id: str) -> None:
    _, role = await member_project(context, project_id)
    if not at_least(role, PROPOSER):
        await context.deny("role", "Only an editor or a manager of the project changes a model", project_id=project_id)


def _changing(tool: str, model: type[Arguments], title: str, description: str) -> Tool:
    async def run(context: ToolContext, arguments: Any) -> dict[str, Any]:
        found = await _copy(context, arguments.copy_id)
        await _proposer(context, str(found["project_id"]))
        here = _here(context)
        given = arguments.model_dump(exclude={"copy_id"}, exclude_none=True)
        if "params" in given:
            given["params"] = _params(given["params"])
        head = await document_store(context.graph, context.postgres).head(
            project_id=str(found["project_id"]), document_id=str(found["document_id"])
        )
        answer = await _answer(
            context,
            tool,
            given,
            path=here.storage.path(str(found["storage_key"])),
            write=True,
            known=head.max_step_id if head is not None else None,
            proposer=context.principal.username,
            copy=True,
        )
        await context.record("mcp.ifcmcp.edited", copy_id=str(found["copy_id"]), project_id=str(found["project_id"]))
        return answer

    return Tool(
        name=tool,
        title=title,
        description=description,
        arguments=model,
        run=run,
        scope=MCP_PROPOSE,
        read_only=False,
        needs_ifcgraph=True,
    )


async def ifc_open(context: ToolContext, arguments: OpenArguments) -> dict[str, Any]:
    here = _here(context)
    record = await version_of(context, arguments.document_id, arguments.version_index)
    await _proposer(context, record.project_id)
    if not is_step(record.file_name):
        raise ToolRefusal("The version is not an IFC file in STEP")
    await restore_record(here.ifcgraph.git, here.storage, record)
    copy_id = new_id()
    key = make_key("copies", copy_id, "ifc")
    here.storage.copy_in(here.storage.path(record.storage_key), key)
    expires_at = later_iso(COPY_SECONDS)
    made = await here.copies.open(
        user_id=context.principal.user_id,
        project_id=record.project_id,
        document_id=record.document_id,
        version_index=record.version_index,
        copy_id=copy_id,
        storage_key=key,
        expires_at=expires_at,
        most=MAX_COPIES,
    )
    if not made:
        here.storage.delete(key)
        raise ToolRefusal(f"You have {MAX_COPIES} working copies open; close one with ifc_close first")
    await context.record("mcp.ifcmcp.opened", copy_id=copy_id, project_id=record.project_id)
    return {
        "copy_id": copy_id,
        "project_id": record.project_id,
        "document_id": record.document_id,
        "version_index": record.version_index,
        "expires_at": expires_at,
    }


async def ifc_close(context: ToolContext, arguments: CopyArguments) -> dict[str, Any]:
    here, copy_id = _here(context), arguments.copy_id
    key = (
        await here.copies.close(user_id=context.principal.user_id, copy_id=normalize_guid(copy_id))
        if is_uuid(copy_id)
        else None
    )
    if key is None:
        raise ToolRefusal("There is no such working copy of yours")
    await here.ifcgraph.residents.drop(here.storage.path(key))
    here.storage.delete(key)
    return {"ok": True}


async def ifc_propose(context: ToolContext, arguments: ProposeArguments) -> dict[str, Any]:
    found = await _copy(context, arguments.copy_id)
    await _proposer(context, str(found["project_id"]))
    here = _here(context)
    try:
        async with here.ifcgraph.residents.settled(here.storage.path(str(found["storage_key"]))):
            return await _propose(context, arguments, found, here)
    except CopyLost as exc:
        raise ToolRefusal(str(exc)) from None


async def _propose(
    context: ToolContext, arguments: ProposeArguments, found: dict[str, Any], here: _Here
) -> dict[str, Any]:
    """The proposal from the copy's file, as its worker last wrote it."""
    project_id, copy_id = str(found["project_id"]), str(found["copy_id"])
    topics = PostgresTopicRepository(here.postgres, id_prefix=context.settings.server_assigned_id_prefix)
    links = PostgresTopicLinkRepository(here.postgres)
    author, public_url = context.principal.username, context.settings.public_url
    guid = str(found["topic_guid"]) if found.get("topic_guid") else None
    if (
        guid is not None
        and await topics.get(user_id=context.principal.user_id, project_id=project_id, guid=guid) is None
    ):
        guid = None  # the topic went: a new one
    if guid is None:
        guid = new_id()
        extensions = await project_store(context.graph, context.postgres).extensions(project_id) or {}
        schema = await _schema_of(here.storage.path(str(found["storage_key"])))
        body = TopicPOST(
            guid=guid,
            title=arguments.title[:MAX_TITLE],
            description=arguments.description,
            topic_type="Request" if "Request" in (extensions.get("topic_type") or []) else None,
            topic_status="Open" if "Open" in (extensions.get("topic_status") or []) else None,
            bim_snippet=BimSnippet(
                snippet_type=FILE_SNIPPET_TYPE,
                is_external=False,  # the file is the topic's own, as an app's upload of a whole file is (SA-129)
                reference="proposal.ifc",
                reference_schema=SCHEMAS.get(schema, SCHEMAS["IFC4"]),
            ),
        )
        try:
            validate_against_extensions(body, extensions)
        except HTTPException:
            raise ToolRefusal(
                "This project's topics do not take a whole file (DataregiumIfcFile) as their snippet; a manager of "
                "the project can allow it"
            ) from None
        await topics.create(project_id=project_id, author=author, body=body)
        await here.copies.set_topic(user_id=context.principal.user_id, copy_id=copy_id, topic_guid=guid)
    key = snippet_key(project_id, guid)
    size = here.storage.copy_in(here.storage.path(str(found["storage_key"])), key)
    before = await links.set_snippet(
        project_id=project_id, topic_guid=guid, author=author, storage_key=key, filename="proposal.ifc", size=size
    )
    if before and before != key:
        with contextlib.suppress(ValueError, OSError):
            here.storage.delete(before)
    provenance = {"ai_model": (arguments.ai_model or "")[:200] or None, "tool_call": "mcp:ifcmcp"}
    await here.ifcgraph.schedule_proposal(
        project_id=project_id,
        topic_guid=guid,
        snippet_key=key,
        author=author,
        base={"document_id": str(found["document_id"]), "version_index": int(found["version_index"])},
        fields={"provenance": json.dumps({k: v for k, v in provenance.items() if v}, separators=(",", ":"))},
    )
    await context.record("mcp.ifcmcp.proposed", copy_id=copy_id, project_id=project_id, topic_guid=guid)
    return {
        "project_id": project_id,
        "topic_guid": guid,
        "state": "queued",
        "proposal_url": f"{public_url}/bcf/3.0/projects/{project_id}/topics/{guid}/ifc-proposal",
    }


async def _schema_of(path: Any) -> str:
    import anyio

    from opencde.documents.step import scan

    def read() -> str:
        with path.open("rb") as stream:
            return str(scan(stream, 1 << 16).schema or "IFC4")

    found = await anyio.to_thread.run_sync(read)
    return "IFC4X3" if found.startswith("IFC4X3") else found if found in SCHEMAS else "IFC4"


_ON = "the version or working copy named"
READING_TOOLS: tuple[Tool, ...] = (
    _reading("ifc_summary", ModelArguments, "Summarise a model", f"Schema, entity counts and project of {_ON}."),
    _reading("ifc_tree", ModelArguments, "The spatial tree", f"The spatial hierarchy from IfcProject down, of {_ON}."),
    _reading("ifc_info", ElementArguments, "Inspect an entity", f"Everything about one entity, by STEP id, in {_ON}."),
    _reading(
        "ifc_select", SelectArguments, "Select elements", f"The elements an IfcOpenShell selector picks in {_ON}."
    ),
    _reading("ifc_relations", RelationsArguments, "An element's relations", f"An element's relationships in {_ON}."),
    _reading("ifc_contexts", ModelArguments, "Representation contexts", f"The representation contexts of {_ON}."),
    _reading("ifc_materials", ModelArguments, "Materials", f"The materials and material sets of {_ON}."),
    _reading("ifc_validate", ValidateArguments, "Validate a model", f"Schema and rule violations in {_ON}."),
    _reading("ifc_schedule", DepthArguments, "Work schedules", f"The work schedules and their tasks in {_ON}."),
    _reading("ifc_cost", DepthArguments, "Cost schedules", f"The cost schedules and their items in {_ON}."),
    _reading(
        "ifc_schema", SchemaArguments, "Schema documentation", f"An IFC class's documentation, in {_ON}'s schema."
    ),
)
EDITING_TOOLS: tuple[Tool, ...] = (
    Tool(
        name="ifc_open",
        title="Open a working copy",
        description=(
            "Copy a version of an IFC document for you to edit with ifc_edit, ifc_shape and ifc_quantify; the version "
            "itself never changes. Answers the copy_id, which lasts four hours after its last use; at most three open."
        ),
        arguments=OpenArguments,
        run=ifc_open,
        scope=MCP_PROPOSE,
        read_only=False,
        needs_ifcgraph=True,
    ),
    _discovering(
        "ifc_list", ListArguments, "List edit functions", "The API modules, or a module's functions, you may run."
    ),
    _discovering("ifc_docs", DocsArguments, "Document an edit function", "An allowed API function's documentation."),
    _changing(
        "ifc_edit", EditArguments, "Edit the copy", "Run an allowed ifcopenshell.api function on a working copy."
    ),
    _changing("ifc_quantify", QuantifyArguments, "Quantities", "Compute and write base quantities on a working copy."),
    _discovering("ifc_shape_list", Arguments, "List shape methods", "The ShapeBuilder methods you may run."),
    _discovering(
        "ifc_shape_docs", ShapeDocsArguments, "Document a shape method", "A ShapeBuilder method's documentation."
    ),
    _changing(
        "ifc_shape", ShapeArguments, "Build geometry", "Build geometry with a ShapeBuilder method on a working copy."
    ),
    Tool(
        name="ifc_propose",
        title="Propose the working copy",
        description=(
            "Propose the working copy as a whole file: a BCF topic whose snippet is the file, tried on the copy's version "
            "(get_proposal reports the trial) and approved by a person, never by a tool. Calling it again puts the copy "
            "as it is then on the same topic. Answers the topic_guid and the trial's address."
        ),
        arguments=ProposeArguments,
        run=ifc_propose,
        scope=MCP_PROPOSE,
        read_only=False,
        needs_ifcgraph=True,
    ),
    Tool(
        name="ifc_close",
        title="Close a working copy",
        description="Throw a working copy away; a proposal made from it stays.",
        arguments=CopyArguments,
        run=ifc_close,
        scope=MCP_PROPOSE,
        read_only=False,
        needs_ifcgraph=True,
    ),
)
IFCMCP_TOOLS: tuple[Tool, ...] = (*READING_TOOLS, *EDITING_TOOLS)
