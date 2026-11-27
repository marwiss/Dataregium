# This file was generated with the assistance of an AI coding tool.
"""What the IFC-DB service, the merge, the named queries and the MCP tool ``list_models`` read and write of the
server's own data (ADR 0029), against a real database: the documents', the BCF and the jobs' methods they use, the
same cases on the graph's repositories and on PostgreSQL's; and on PostgreSQL those paths themselves, with graphs
that fail at the first statement, so that nothing of them reaches a graph when the store is PostgreSQL.

PostgreSQL runs in a container of its own (``postgres_target`` and ``pg`` in the conftest), one per test session;
every test starts from empty tables with the migrations applied. Each case runs once on the graph and once on
PostgreSQL, which needs no graph: ``-k on_postgresql`` starts PostgreSQL alone. The flows themselves on the graph
are the IFC-DB module's own integration tests (the trials, the merge, the clash checks, the checks, the queries and
the MCP tools).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from ifc_db.validation import pack_report

from opencde.accounts.roles import Role
from opencde.bcf.schemas import TopicPOST, ViewpointPOST
from opencde.graph.graph import Graph
from opencde.ids import new_id
from opencde.ifcgraph import merging
from opencde.ifcgraph import service as service_module
from opencde.ifcgraph.queries import model_state
from opencde.ifcgraph.service import IfcGraphService
from opencde.mcp.ifc_models import ListModelsArguments, list_models
from opencde.mcp.registry import ToolContext
from opencde.postgres.database import Postgres
from opencde.storage import LocalStorage, make_key
from opencde.store.accounts.postgres import PostgresProjectRepository, PostgresUserRepository
from opencde.store.accounts.projects import ProjectRepository
from opencde.store.accounts.users import UserRepository
from opencde.store.bcf.postgres import PostgresTopicRepository, PostgresViewpointRepository
from opencde.store.bcf.topics import TopicRepository
from opencde.store.bcf.viewpoints import ViewpointRepository
from opencde.store.documents.postgres import PostgresDocumentRepository
from opencde.store.documents.versions import DocumentRepository, default_metadata
from opencde.store.jobs.conversions import ConversionRepository
from opencde.store.jobs.postgres import PostgresConversionRepository, PostgresValidationRepository
from opencde.store.jobs.validations import ValidationRepository
from opencde.store.oauth2.tokens import Principal
from opencde.store.protocols import (
    ConversionStore,
    DocumentStore,
    ProjectStore,
    TopicStore,
    UserStore,
    ValidationStore,
    ViewpointStore,
)
from tests.conftest import test_settings as make_settings

pytestmark = pytest.mark.integration

PASSWORD = "a-long-and-strong-passphrase"
IFC = [".ifc", ".ifczip"]
WALL, SLAB, DOOR = "1Xezbq735AdBO_4DerG66G", "2Xezbq735AdBO_4DerG66G", "3Xezbq735AdBO_4DerG66G"
FIRST, SECOND = "00000000-0000-4000-8000-000000000001", "00000000-0000-4000-8000-000000000002"
CAMERA = {  # a viewpoint needs a camera or a snapshot (BCF 3.5.2)
    "orthogonal_camera": {
        "camera_view_point": {"x": 1.0, "y": 2.0, "z": 3.0},
        "camera_direction": {"x": 0.0, "y": -1.0, "z": 0.0},
        "camera_up_vector": {"x": 0.0, "y": 0.0, "z": 1.0},
        "view_to_world_scale": 2.0,
        "aspect_ratio": 1.0,
    }
}


@dataclass(frozen=True)
class Stores:
    users: UserStore
    projects: ProjectStore
    documents: DocumentStore
    validations: ValidationStore
    conversions: ConversionStore
    topics: TopicStore
    viewpoints: ViewpointStore
    storage: LocalStorage


@dataclass(frozen=True)
class World:
    project_id: str  # "Building permit", the editor's
    other_id: str  # "Archive", the editor's and the outsider's
    editor: str  # user ids
    outsider: str


async def _world(stores: Stores) -> World:
    editor = await stores.users.create("editor", PASSWORD)
    outsider = await stores.users.create("outsider", PASSWORD)
    project, other = await stores.projects.create("Building permit"), await stores.projects.create("Archive")
    await stores.projects.set_member(project.project_id, editor.id, Role.EDITOR)
    await stores.projects.set_member(other.project_id, editor.id, Role.EDITOR)
    await stores.projects.set_member(other.project_id, outsider.id, Role.VIEWER)
    return World(project.project_id, other.project_id, editor.id, outsider.id)


async def _version(
    stores: Stores, project_id: str, file_name: str, *, document_id: str | None = None, title: str = "Plan", **more: Any
) -> Any:
    """A version as an upload makes it: a new document unless ``document_id`` names one, or ``requested`` gives it."""
    requested = more.pop("requested", None)
    key = make_key("documents", new_id())
    return await stores.documents.create_version(
        project_id=project_id,
        document_id=document_id,
        requested_document_id=requested,
        title=title,
        version_number="",
        file_name=file_name,
        size_in_bytes=3,
        sha256="0" * 64,
        storage_key=key,
        author="editor",
        metadata=default_metadata(
            file_name=file_name, size_in_bytes=3, sha256="0" * 64, author="editor", project_name="Building permit"
        ),
        **more,
    )


async def _tick() -> None:
    """Past the millisecond of the server's timestamps, so that newest first has one answer."""
    await asyncio.sleep(0.003)


# -- the same on both ----------------------------------------------------------------------------------------------


async def what_the_jobs_read_of_a_projects_versions(stores: Stores) -> None:
    w = await _world(stores)
    documents = stores.documents
    walls = await _version(stores, w.project_id, "walls.ifc", requested=SECOND, title="Walls")
    walls_2 = await _version(stores, w.project_id, "Walls.IFC", document_id=SECOND, title="Walls, revised")
    slab = await _version(stores, w.project_id, "slab.ifczip", requested=FIRST, title="Slab")
    rules = await _version(stores, w.project_id, "rules.IDS", title="Rules")
    notes = await _version(stores, w.project_id, "model.ifc", title="Notes")
    await _version(stores, w.project_id, "notes.txt", document_id=notes.document_id, title="Notes")
    archived = await _version(stores, w.other_id, "archive.ifc", title="Archive")

    head = await documents.head(project_id=w.project_id, document_id=SECOND)
    assert head is not None and (head.version_index, head.title, head.file_name) == (2, "Walls, revised", "Walls.IFC")
    assert head.version_id == walls_2.version_id and head.project_name == "Building permit"
    assert await documents.head(project_id=w.other_id, document_id=SECOND) is None, "only the project's own"

    where = {"project_id": w.project_id, "document_id": SECOND, "version_index": 1}
    unchecked = await documents.version_file(**where)
    assert unchecked is not None and unchecked.pop("commit", None) is None, "without a repository no commit"
    assert unchecked == {
        "storage_key": walls.storage_key,
        "file_name": "walls.ifc",
        "report": None,
        "checks": None,
    }, "a version not checked yet"
    for state, report in (("done", [{"m": "older"}]), ("done", [{"m": "newer"}]), ("failed", None)):
        check = new_id()
        assert await stores.validations.queue(
            validation_id=check, version_id=walls.version_id, storage_key=walls.storage_key, file_name="walls.ifc"
        )
        fields = {"report": pack_report(report), "checks": json.dumps([{"name": "schema"}])} if report else {}
        await stores.validations.set_state(check, state, **fields)
        await _tick()
    found = await documents.version_file(**where)
    assert found is not None and found["report"] == pack_report([{"m": "newer"}]), "the newest finished check"
    assert found["checks"] == json.dumps([{"name": "schema"}])
    assert await documents.version_file(**(where | {"project_id": w.other_id})) is None
    assert await documents.version_file(**(where | {"version_index": 9})) is None

    ifc = await documents.project_heads(project_id=w.project_id, suffixes=IFC, limit=500)
    assert [(h.document_id, h.version_index, h.title) for h in ifc] == [
        (FIRST, 1, "Slab"),
        (SECOND, 2, "Walls, revised"),
    ], "the latest version of each, by document id; a document whose latest is no IFC file is left out"
    assert [h.version_id for h in ifc] == [slab.version_id, walls_2.version_id]
    assert [h.document_id for h in await documents.project_heads(project_id=w.project_id, suffixes=IFC, limit=1)] == [
        FIRST
    ]
    ids = await documents.project_heads(project_id=w.project_id, suffixes=[".ids"], limit=50)
    assert [(h.document_id, h.storage_key, h.title) for h in ids] == [(rules.document_id, rules.storage_key, "Rules")]

    every = [w.project_id, w.other_id]
    listed = await documents.member_heads(user_id=w.editor, project_ids=every, suffixes=[*IFC, ".ifcxml"], limit=201)
    assert [(h.project_name, h.title) for h in listed] == [
        ("Archive", "Archive"),
        ("Building permit", "Slab"),
        ("Building permit", "Walls, revised"),
    ], "by project name, then by title"
    assert [h.version_id for h in listed] == [archived.version_id, slab.version_id, walls_2.version_id]
    assert [
        h.title for h in await documents.member_heads(user_id=w.editor, project_ids=every, suffixes=IFC, limit=2)
    ] == [
        "Archive",
        "Slab",
    ]
    mine = await documents.member_heads(user_id=w.outsider, project_ids=every, suffixes=IFC, limit=201)
    assert [h.document_id for h in mine] == [archived.document_id], "only the projects the user is a member of"
    only = await documents.member_heads(user_id=w.editor, project_ids=[w.project_id], suffixes=IFC, limit=201)
    assert {h.project_id for h in only} == {w.project_id}, "only the projects asked for"
    assert await documents.member_heads(user_id=w.editor, project_ids=[], suffixes=IFC, limit=201) == []


async def the_newest_conversion_of_each_version(stores: Stores) -> None:
    w = await _world(stores)
    walls = await _version(stores, w.project_id, "walls.ifc", requested=SECOND)
    archived = await _version(stores, w.other_id, "archive.ifc")
    conversions = stores.conversions
    made = []
    for project_id, record in ((w.project_id, walls), (w.project_id, walls), (w.other_id, archived)):
        model_id = new_id()
        await conversions.create(
            project_id=project_id,
            model_id=model_id,
            file_name=record.file_name,
            storage_key=record.storage_key,
            document_id=record.document_id,
            version_index=record.version_index,
        )
        made.append(model_id)
        await _tick()
    await conversions.set_state(made[1], "done", schema="IFC4")
    await conversions.create(  # another project's record of the same version names no version of that project
        project_id=w.other_id,
        model_id=new_id(),
        file_name="x.ifc",
        storage_key="k",
        document_id=SECOND,
        version_index=1,
    )

    asked = [(w.project_id, SECOND, 1), (w.project_id, SECOND, 2), (w.other_id, archived.document_id, 1)]
    found = await conversions.for_member_versions(user_id=w.editor, versions=asked)
    newest = {(c["project_id"], c["document_id"], c["version_index"]): c for c in found}
    assert len(found) == 2 and set(newest) == {asked[0], asked[2]}, "one each, and none for a version without one"
    assert (newest[asked[0]]["id"], newest[asked[0]]["state"], newest[asked[0]]["schema"]) == (made[1], "done", "IFC4")
    assert newest[asked[2]]["id"] == made[2] and newest[asked[2]]["state"] == "queued"
    theirs = await conversions.for_member_versions(user_id=w.outsider, versions=asked)
    assert [c["id"] for c in theirs] == [made[2]], "only in the projects the user is a member of"
    assert await conversions.for_member_versions(user_id=w.editor, versions=[]) == []


async def the_gtin_onto_the_components(stores: Stores) -> None:
    w = await _world(stores)
    viewpoints = stores.viewpoints
    topic = await stores.topics.create(project_id=w.project_id, author="editor", body=TopicPOST(title="Walls"))
    elsewhere = await stores.topics.create(project_id=w.other_id, author="editor", body=TopicPOST(title="Old"))
    one = await viewpoints.create(
        project_id=w.project_id,
        topic_guid=topic.guid,
        author="editor",
        body=_viewpoint(selection=[WALL, None], exceptions=[WALL]),
    )
    two = await viewpoints.create(
        project_id=w.project_id,
        topic_guid=topic.guid,
        author="editor",
        body=_viewpoint(selection=[WALL], coloring=[SLAB, "not a GlobalId"]),
    )
    other = await viewpoints.create(
        project_id=w.other_id, topic_guid=elsewhere.guid, author="editor", body=_viewpoint(selection=[WALL, DOOR])
    )

    assert sorted(await viewpoints.component_guids(project_id=w.project_id)) == sorted(
        [WALL, SLAB, "not a GlobalId"]
    ), "each once, the project's alone"
    assert await viewpoints.component_guids(project_id=w.project_id, viewpoint_guid=one.model.guid) == [WALL]
    assert await viewpoints.component_guids(project_id=w.project_id, viewpoint_guid=other.model.guid) == [], (
        "a viewpoint of another project is not this project's"
    )

    await viewpoints.set_gtins(project_id=w.project_id, gtins={WALL: "7310000000001"}, viewpoint_guid=one.model.guid)
    assert await _gtins(stores, w, topic.guid, one.model.guid) == {WALL: {"7310000000001"}, None: {None}}
    assert await _gtins(stores, w, topic.guid, two.model.guid) == {WALL: {None}, SLAB: {None}, "not a GlobalId": {None}}
    await viewpoints.set_gtins(project_id=w.project_id, gtins={WALL: "7310000000002", SLAB: "7310000000003"})
    assert await _gtins(stores, w, topic.guid, one.model.guid) == {WALL: {"7310000000002"}, None: {None}}
    assert await _gtins(stores, w, topic.guid, two.model.guid) == {
        WALL: {"7310000000002"},
        SLAB: {"7310000000003"},
        "not a GlobalId": {None},
    }
    theirs = {"topic_guid": elsewhere.guid, "guid": other.model.guid, "project_id": w.other_id}
    assert await _gtins(stores, w, **theirs) == {WALL: {None}, DOOR: {None}}, "another project's keep theirs"
    await viewpoints.set_gtins(project_id=w.project_id, gtins={DOOR: "7310000000004"})
    assert await _gtins(stores, w, **theirs) == {WALL: {None}, DOOR: {None}}


def _viewpoint(
    *, selection: list[str | None], coloring: list[str] | None = None, exceptions: list[str] | None = None
) -> ViewpointPOST:
    def components(guids: list[Any]) -> list[dict[str, Any]]:
        return [{"ifc_guid": guid} if guid else {"authoring_tool_id": "42"} for guid in guids]

    visibility: dict[str, Any] = {"default_visibility": True, "exceptions": components(exceptions or [])}
    found: dict[str, Any] = {"selection": components(selection), "visibility": visibility}
    if coloring:
        found["coloring"] = [{"color": "ff0000", "components": components(coloring)}]
    return ViewpointPOST.model_validate({"components": found} | CAMERA)


async def _gtins(
    stores: Stores, w: World, topic_guid: str, guid: str, *, project_id: str | None = None
) -> dict[str | None, set[str | None]]:
    """The GTINs of a viewpoint's components, by the GlobalId they name, as a member reads them."""
    scope = {"user_id": w.editor, "project_id": project_id or w.project_id, "topic_guid": topic_guid, "guid": guid}
    found: dict[str | None, set[str | None]] = {}
    for role in ("selection", "coloring", "exception"):
        for node in await stores.viewpoints.components(**scope, role=role):
            found.setdefault(node.get("ifc_guid"), set()).add(node.get("gtin"))
    return found


CASES = (what_the_jobs_read_of_a_projects_versions, the_newest_conversion_of_each_version, the_gtin_onto_the_components)


def _on_the_graph(graph: Graph, storage: LocalStorage) -> Stores:
    topics = TopicRepository(graph)
    return Stores(
        UserRepository(graph),
        ProjectRepository(graph),
        DocumentRepository(graph),
        ValidationRepository(graph),
        ConversionRepository(graph),
        topics,
        ViewpointRepository(graph, storage, topics, max_image_bytes=1_000_000),
        storage,
    )


def _on_postgresql(pg: Postgres, storage: LocalStorage) -> Stores:
    return Stores(
        PostgresUserRepository(pg),
        PostgresProjectRepository(pg),
        PostgresDocumentRepository(pg),
        PostgresValidationRepository(pg),
        PostgresConversionRepository(pg),
        PostgresTopicRepository(pg),
        PostgresViewpointRepository(pg, storage, max_image_bytes=1_000_000),
        storage,
    )


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.__name__)
async def test_on_the_graph(case: Callable[[Stores], Awaitable[None]], graph: Graph, tmp_path: Path) -> None:
    await case(_on_the_graph(graph, LocalStorage(tmp_path / "data")))


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.__name__)
async def test_on_postgresql(case: Callable[[Stores], Awaitable[None]], pg: Postgres, tmp_path: Path) -> None:
    await case(_on_postgresql(pg, LocalStorage(tmp_path / "data")))


# -- the paths themselves on PostgreSQL ----------------------------------------------------------------------------


class Absent:
    """A graph that is not there: a statement that reaches it fails the test."""

    def __init__(self, name: str) -> None:
        self.name = name

    async def run(self, query: str, /, **params: Any) -> list[dict[str, Any]]:
        raise AssertionError(f"a statement reached the {self.name}")

    async def one(self, query: str, /, **params: Any) -> dict[str, Any] | None:
        raise AssertionError(f"a statement reached the {self.name}")

    async def write(self, work: Any) -> Any:
        raise AssertionError(f"a transaction reached the {self.name}")


async def _workers(command: list[str], what: str, answers: tuple[int, ...] = (0,)) -> str:
    """The workers, without IfcOpenShell: the applied change set's file, values read, and checks that find nothing."""
    module = command[2]
    if module == "opencde.ifcgraph.apply" and "--read" in command:
        return json.dumps({"values": [{"value": "EI 60"}]})
    if module == "opencde.ifcgraph.apply":
        await asyncio.to_thread(Path(command[5]).write_bytes, b"ISO-10303-21;")
        return json.dumps({"count": 1, "applied": 1, "problems": []})
    if module == "ifc_db.ids":
        empty = {"issue_count": 0, "error_count": 0, "issues": [], "rules_not_run": [], "specifications": []}
        return json.dumps({"ruleset": "IDS 1.0", "truncated": False, **empty})
    return json.dumps({"ruleset": "IfcOpenShell x", "issue_count": 0, "truncated": False, "issues": []})


async def test_on_postgresql_the_service_the_merge_and_the_tools_ask_no_graph(
    pg: Postgres, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = LocalStorage(tmp_path / "data")
    stores = _on_postgresql(pg, storage)
    w = await _world(stores)
    await stores.projects.set_ai_policy(w.project_id, "cloud")
    base = await _version(stores, w.project_id, "walls.ifc", requested=SECOND, title="Walls")
    rules = await _version(stores, w.project_id, "rules.ids", title="Rules")
    settings = make_settings(tmp_path, ifcgraph_enabled=True, ifcgraph_spatial=False)
    service = IfcGraphService(
        Absent("IFC graph"),  # type: ignore[arg-type]
        storage,
        settings,
        store=Absent("store's graph"),  # type: ignore[arg-type]
        postgres=pg,
    )
    started: list[str] = []
    monkeypatch.setattr(service, "_in_a_process", _workers)
    monkeypatch.setattr(service, "_prepare_input", lambda key, name, workdir: tmp_path / name)
    monkeypatch.setattr(service, "_prepare_clash_input", lambda key, name, workdir: Path(key))
    monkeypatch.setattr(service, "_start_validation", lambda validation_id, key, name: started.append(name))

    # The checks: the project's IDS documents, and its IFC documents checked again when the IDS changes.
    assert await service._ids_documents(w.project_id) == [
        {"document_id": rules.document_id, "version_index": 1, "title": "Rules", "storage_key": rules.storage_key}
    ]
    assert await service.recheck_after_ids(w.project_id) == 1 and started == ["walls.ifc"]
    # A side of a clash check, and the values of a version for a member of its project alone.
    side = {"document_id": SECOND, "version_index": 1}
    assert await service._clash_input(w.project_id, side, tmp_path) == Path(base.storage_key)
    with pytest.raises(ValueError, match="not found"):
        await service._clash_input(w.other_id, side, tmp_path)
    change = [{"guid": WALL, "path": "", "attribute": "Name"}]
    assert await service.values_in(user_id=w.editor, document_id=SECOND, version_index=1, changes=change) == [
        {"value": "EI 60"}
    ]
    assert await service.values_in(user_id=w.outsider, document_id=SECOND, version_index=1, changes=change) is None

    # A trial on the latest version (keep_theirs), whose base has a finished check.
    latest = await _version(stores, w.project_id, "walls.ifc", document_id=SECOND, title="Walls")
    check = new_id()
    assert await stores.validations.queue(
        validation_id=check, version_id=latest.version_id, storage_key=latest.storage_key, file_name="walls.ifc"
    )
    await stores.validations.set_state(check, "done", report=pack_report([]), checks="[]")
    snippet = make_key("snippets", new_id(), "json")
    change_set = {"base": {"document_id": SECOND, "version_index": 1}, "changes": [], "keep_theirs": ["*"]}
    storage.write_bytes(snippet, json.dumps(change_set).encode("utf-8"))
    (tmp_path / "walls.ifc").write_bytes(b"ISO-10303-21;")
    tried = await service._try_proposal(new_id(), w.project_id, snippet, author="editor")
    assert (tried["state"], tried["base_version_index"], tried["issue_count"]) == ("clean", 2, 0)

    # The GTIN of the components from a finished model, and the model's state for the queries and list_models.
    topic = await stores.topics.create(project_id=w.project_id, author="editor", body=TopicPOST(title="Walls"))
    made = await stores.viewpoints.create(
        project_id=w.project_id, topic_guid=topic.guid, author="editor", body=_viewpoint(selection=[WALL, DOOR])
    )
    model_id = new_id()
    await stores.conversions.create(
        project_id=w.project_id,
        model_id=model_id,
        file_name="walls.ifc",
        storage_key=latest.storage_key,
        document_id=SECOND,
        version_index=2,
    )
    await stores.conversions.set_state(model_id, "done", schema="IFC4")

    async def values(reading: Any, model: str, **asked: Any) -> list[dict[str, Any]]:
        assert model == model_id and sorted(asked["global_ids"]) == sorted([WALL, DOOR])
        return [{"global_id": WALL, "value": 7310000000001}, {"global_id": DOOR, "value": None}]

    monkeypatch.setattr(service_module, "property_values", values)
    assert await service._link_model(model_id) == 2
    assert await service.link_viewpoint(project_id=w.project_id, viewpoint_guid=made.model.guid) == 2
    assert await _gtins(stores, w, topic.guid, made.model.guid) == {WALL: {"7310000000001"}, DOOR: {None}}
    assert await model_state(service, stores.documents, settings, w.editor, SECOND, 2) == ("done", model_id)

    principal = Principal(
        user_id=w.editor,
        username="editor",
        display_name="Editor",
        is_admin=False,
        client_id="ai-client",
        scopes=("mcp",),
        token_hash="hash",
        auth_time="2999-01-01T00:00:00Z",
    )
    context = ToolContext(
        principal=principal,
        settings=settings,
        graph=Absent("store's graph"),  # type: ignore[arg-type]
        tool="list_models",
        postgres=pg,
    )
    listed = await list_models(context, ListModelsArguments(project_id=None))
    assert listed["truncated"] is False
    assert [
        (m["project_name"], m["role"], m["ai_policy"], m["version_index"], m["model_state"], m["schema"])
        for m in listed["models"]
    ] == [("Building permit", "editor", "cloud", 2, "done", "IFC4")], (
        "the cloud project's IFC document, not the IDS one, nor the local project's"
    )
    assert await list_models(context, ListModelsArguments(project_id=w.project_id)) == listed

    # The merged model as the next version of the base document.
    async def export(model: str, target: Path) -> int:
        await asyncio.to_thread(target.write_bytes, b"ISO-10303-21;")
        return 1

    scheduled: list[dict[str, Any]] = []

    async def schedule(**fields: Any) -> str:
        scheduled.append(fields)
        return "model-c"

    monkeypatch.setattr(service, "export", export)
    monkeypatch.setattr(service, "schedule", schedule)
    request = {
        "base": {"model_id": model_id, "document_id": SECOND, "version_index": 2},
        "other": {"model_id": "model-b", "project_id": w.other_id, "document_id": FIRST, "version_index": 1},
        "building": WALL,
    }
    assert await merging._as_version(service, w.project_id, "editor", request, "model-c", {}) == 3
    merged = await stores.documents.head(project_id=w.project_id, document_id=SECOND)
    assert merged is not None and merged.version_index == 3 and merged.file_name == "walls.ifc"
    assert [entry["value"] for entry in merged.metadata if entry["name"] == "merged_from_document"] == [[FIRST]]
    assert scheduled[0]["version_index"] == 3 and scheduled[0]["version_id"] == merged.version_id
    with pytest.raises(merging.MergeError, match="latest version is 3"):  # the same merge again, on an older base
        await merging._as_version(service, w.project_id, "editor", request, "model-c", {})
