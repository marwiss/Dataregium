# This file was generated with the assistance of an AI coding tool.
"""BCF API 3.2.6-3.2.7 BIM snippet, 3.3.2-3.3.3 files, 3.6 related topics, 3.7 document references."""

from __future__ import annotations

import contextlib
import json
from typing import Annotated, Any

import anyio
from fastapi import APIRouter, HTTPException, Path, Query, Request, Response
from fastapi.responses import StreamingResponse

from opencde.bcf.access import parse_guid
from opencde.bcf.binary import file_response, filename_from_content_disposition, receive_upload
from opencde.bcf.schemas import (
    MAX_FILES,
    MAX_RELATED,
    DocumentReferencePOST,
    DocumentReferencePUT,
    FileRef,
    RelatedTopic,
    dump,
)
from opencde.bcf.topic_access import TopicCtx
from opencde.deps import SettingsDep, StorageDep
from opencde.documents.changes import FILE_SNIPPET_TYPE, MAX_CHANGE_SET_BYTES, SNIPPET_TYPE, problems
from opencde.documents.step import is_step
from opencde.ids import is_uuid, new_id, normalize_guid
from opencde.ifcgraph.service import IfcGraphDep
from opencde.ratelimit import RATE_UPLOAD, limiter
from opencde.repos.writer import clean
from opencde.storage import LocalStorage, make_key, snippet_key
from opencde.store.bcf.topic_links import LinksDep
from opencde.store.documents.versions import DocumentsDep

router = APIRouter(prefix="/projects/{project_id}/topics/{topic_id}", tags=["BCF API"])

ReferenceId = Annotated[str, Path(alias="reference_id", max_length=64)]
MAX_PROVENANCE = 200  # an AI model's name or a tool call's, as the change set's provenance holds them


# -- BIM snippet (3.2.6, 3.2.7) -----------------------------------------------------------------


@router.get("/snippet", summary="3.2.6 GET Topic BIM Snippet", response_class=StreamingResponse)
async def snippet_get(topic: TopicCtx, links: LinksDep, storage: StorageDep) -> StreamingResponse:
    """The stored snippet file. 404 when the topic's snippet is external or was never uploaded."""
    info = await links.snippet(user_id=topic.user_id, project_id=topic.project_id, topic_guid=topic.guid)
    if info is None:
        raise HTTPException(404, "Topic has no stored BIM snippet")
    return file_response(storage, info["storage_key"], media_type="application/octet-stream", filename=info["filename"])


@router.put("/snippet", summary="3.2.7 PUT Topic BIM Snippet", status_code=200, response_class=Response)
@limiter.limit(RATE_UPLOAD)
async def snippet_put(
    request: Request,
    topic: TopicCtx,
    links: LinksDep,
    storage: StorageDep,
    settings: SettingsDep,
    ifcgraph: IfcGraphDep,
    documents: DocumentsDep,
    base_document_id: Annotated[str | None, Query(max_length=64)] = None,
    base_version_index: Annotated[int | None, Query(ge=1, le=1_000_000_000)] = None,
    ai_model: Annotated[str | None, Query(max_length=MAX_PROVENANCE)] = None,
    tool_call: Annotated[str | None, Query(max_length=MAX_PROVENANCE)] = None,
) -> Response:
    """Upload the snippet as the request body. The topic's ``bim_snippet`` must already describe it.

    A snippet of the type `DataregiumIfcChangeSet` proposes a change set: that takes the role `editor` or `manager`
    (403 otherwise), the snippet is held to the change set's schema (`GET /documents/1.0/ifc-changes/schema`), and
    400 says what is wrong with it; when the IFC-DB module is on, the change set is then tried on its base version
    (`GET …/ifc-proposal`). A snippet of the type `DataregiumIfcFile` proposes a whole IFC file in STEP (an
    extension, F204 a): its base is `base_document_id` and `base_version_index`, a version of an IFC document of the
    project (400 otherwise), `ai_model` and `tool_call` say what made it where an AI did, and it is tried and
    approved as a change set is, with nothing applied.
    """
    topic.require_topic_action("updateBimSnippet")
    if topic.topic.bim_snippet is None:
        raise HTTPException(400, "Set bim_snippet on the topic before uploading the snippet file")
    kind = topic.topic.bim_snippet.snippet_type
    if kind in (SNIPPET_TYPE, FILE_SNIPPET_TYPE):
        topic.project.require_proposer()  # before a byte of it is kept
    base = None
    if kind == FILE_SNIPPET_TYPE:
        base = await _file_base(documents, topic, base_document_id, base_version_index)
    # Received beside the topic's snippet and checked there, so that a refused upload leaves the one before in place.
    received = make_key("snippets", new_id(), "bin")
    size = await receive_upload(request, storage, received, settings.max_upload_bytes)
    if kind == SNIPPET_TYPE:
        found = await anyio.to_thread.run_sync(_change_set_problems, storage, received, size)
        if found:
            storage.delete(received)
            raise HTTPException(400, "The snippet is not a change set: " + "; ".join(found))
    if kind == FILE_SNIPPET_TYPE and not await anyio.to_thread.run_sync(_is_step_file, storage, received):
        storage.delete(received)
        raise HTTPException(400, "The snippet is not an IFC file in STEP")
    key = snippet_key(topic.project_id, topic.guid)
    try:
        storage.replace(received, key)
    except OSError:  # the topic's file held open (Windows): the upload goes, and the one before stays
        storage.delete(received)
        raise
    before = await links.set_snippet(
        project_id=topic.project_id,
        topic_guid=topic.guid,
        author=topic.username,
        storage_key=key,
        filename=filename_from_content_disposition(request.headers.get("content-disposition")),
        size=size,
    )
    if before and before != key:  # a key from before the topic's snippets had their own (snippets/<topic guid>.bin)
        with contextlib.suppress(ValueError, OSError):  # held open (Windows): it stays, as the cleanup does
            storage.delete(before)
    if ifcgraph is not None and kind == SNIPPET_TYPE:
        await ifcgraph.schedule_proposal(
            project_id=topic.project_id, topic_guid=topic.guid, snippet_key=key, author=topic.username
        )
    if ifcgraph is not None and kind == FILE_SNIPPET_TYPE:
        provenance = {
            name: clean(value, MAX_PROVENANCE)
            for name, value in (("ai_model", ai_model), ("tool_call", tool_call))
            if value and clean(value, MAX_PROVENANCE)
        }
        await ifcgraph.schedule_proposal(
            project_id=topic.project_id,
            topic_guid=topic.guid,
            snippet_key=key,
            author=topic.username,
            base=base,
            fields={"provenance": json.dumps(provenance, separators=(",", ":"))} if provenance else None,
        )
    return Response(status_code=200)


async def _file_base(
    documents: Any, topic: TopicCtx, document_id: str | None, version_index: int | None
) -> dict[str, Any]:
    """The base a whole file is proposed on: a version of an IFC document of the topic's project; 400 otherwise."""
    if not document_id or version_index is None or not is_uuid(document_id):
        raise HTTPException(400, "A whole file is proposed on a base: give base_document_id and base_version_index")
    record = await documents.version(
        user_id=topic.user_id, document_id=normalize_guid(document_id), version_index=version_index
    )
    if record is None or record.project_id != topic.project_id or not is_step(record.file_name):
        raise HTTPException(400, "The base is not a version of an IFC document of this project")
    return {"document_id": record.document_id, "version_index": record.version_index}


def _is_step_file(storage: LocalStorage, key: str) -> bool:
    with storage.open_read(key) as handle:
        return handle.read(64).lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"ISO-10303-21;")


def _change_set_problems(storage: LocalStorage, key: str, size: int) -> list[str]:
    """What keeps the stored snippet from being a change set (ADR 0021); empty when nothing does."""
    if size > MAX_CHANGE_SET_BYTES:
        return [f"a change set is at most {MAX_CHANGE_SET_BYTES} bytes"]
    with storage.open_read(key) as handle:
        raw = handle.read()
    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError:
        return ["the body is not JSON"]
    return problems(data)


# -- header files (3.3.2, 3.3.3) ----------------------------------------------------------------


@router.get("/files", summary="3.3.2 GET Files (Header)")
async def files_get(topic: TopicCtx, links: LinksDep) -> list[dict[str, Any]]:
    return [
        dump(item)
        for item in await links.files(user_id=topic.user_id, project_id=topic.project_id, topic_guid=topic.guid)
    ]


@router.put("/files", summary="3.3.3 PUT Files (Header)")
async def files_put(topic: TopicCtx, body: list[FileRef], links: LinksDep) -> list[dict[str, Any]]:
    """Replace the topic's file references. Needs ``updateFiles``."""
    topic.require_topic_action("updateFiles")
    if len(body) > MAX_FILES:
        raise HTTPException(400, f"At most {MAX_FILES} files per topic")
    stored = await links.set_files(
        project_id=topic.project_id, topic_guid=topic.guid, author=topic.username, files=body
    )
    return [dump(item) for item in stored]


# -- related topics (3.6) -----------------------------------------------------------------------


@router.get("/related_topics", summary="3.6.1 GET Related Topics")
async def related_topics_get(topic: TopicCtx, links: LinksDep) -> list[dict[str, Any]]:
    guids = await links.related(user_id=topic.user_id, project_id=topic.project_id, topic_guid=topic.guid)
    return [dump(RelatedTopic(related_topic_guid=guid)) for guid in guids]


@router.put("/related_topics", summary="3.6.2 PUT Related Topics")
async def related_topics_put(topic: TopicCtx, body: list[RelatedTopic], links: LinksDep) -> list[dict[str, Any]]:
    """Replace the list of related topics (all must belong to the project). Needs ``updateRelatedTopics``."""
    topic.require_topic_action("updateRelatedTopics")
    if len(body) > MAX_RELATED:
        raise HTTPException(400, f"At most {MAX_RELATED} related topics")
    guids = await links.set_related(
        project_id=topic.project_id,
        topic_guid=topic.guid,
        author=topic.username,
        guids=[item.related_topic_guid for item in body],
    )
    return [dump(RelatedTopic(related_topic_guid=guid)) for guid in guids]


# -- document references (3.7) ------------------------------------------------------------------


@router.get("/document_references", summary="3.7.1 GET Document References")
async def document_references_get(topic: TopicCtx, links: LinksDep) -> list[dict[str, Any]]:
    found = await links.references(user_id=topic.user_id, project_id=topic.project_id, topic_guid=topic.guid)
    return [dump(item) for item in found]


@router.post("/document_references", summary="3.7.2 POST Document Reference", status_code=201)
async def document_reference_post(topic: TopicCtx, body: DocumentReferencePOST, links: LinksDep) -> dict[str, Any]:
    """Reference a project document (``document_guid``) or an external resource (``url``). Needs ``updateDocumentReferences``."""
    topic.require_topic_action("updateDocumentReferences")
    created = await links.add_reference(
        project_id=topic.project_id, topic_guid=topic.guid, author=topic.username, body=body
    )
    return dump(created)


@router.put("/document_references/{reference_id}", summary="3.7.3 PUT Document Reference")
async def document_reference_put(
    topic: TopicCtx, reference_id: ReferenceId, body: DocumentReferencePUT, links: LinksDep
) -> dict[str, Any]:
    topic.require_topic_action("updateDocumentReferences")
    guid = parse_guid(reference_id, "Document reference")
    updated = await links.update_reference(
        project_id=topic.project_id, topic_guid=topic.guid, guid=guid, author=topic.username, body=body
    )
    if updated is None:
        raise HTTPException(404, "Document reference not found")
    return dump(updated)
