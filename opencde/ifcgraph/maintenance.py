# This file was generated with the assistance of an AI coding tool.
"""Housekeeping of imported models for the ``opencde ifcgraph`` commands.

Every upload of an IFC file adds a ``Conversion`` and a model with its entities, so a project
that receives new versions (or a test suite that uploads the same file again
and again) accumulates graphs nobody queries. ``prune_models`` removes the
models that failed and the finished models superseded by a newer finished one
of the same document; queued and running models are never touched, and the
documents and their files stay. ``everything`` empties a whole project.
``delete_project`` deletes a project's models in the IFC graph, and then the
project with what it owns in the store (ADR 0027). The conversions are the
store's: in PostgreSQL when a ``postgres`` is given (ADR 0029), else in the graph.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from ifc_db.importer import delete_model

from opencde.graph.graph import FREE_MEMORY_INTERVAL, DatabaseFull, Graph
from opencde.logging import get_logger
from opencde.storage import LocalStorage

if TYPE_CHECKING:
    from opencde.postgres.database import Postgres
    from opencde.store.protocols import ConversionStore, ProjectStore

log = get_logger(__name__)

DROP_ROUNDS = 5  # rounds of deleting a failed import's part from a full database, each after it gave memory back


async def drop_partial(graph: Graph, model_id: str, *, keep_model: bool = False) -> bool:
    """Remove what a failed import or merge wrote; True when it is gone. A full database refuses the deletes too,
    until it has given back the memory of those before, which each refusal asks of it (``Graph.free_memory``): so a
    few rounds, each after a pause and a step further. Any other failure is logged and left, as the import's own
    error is what counts."""
    for round in range(DROP_ROUNDS):
        if round:
            await asyncio.sleep(FREE_MEMORY_INTERVAL)
        try:
            await delete_model(graph, model_id, keep_model=keep_model)
            return True
        except DatabaseFull:
            continue
        except Exception as exc:
            log.warning("ifcgraph_partial_left", model_id=model_id, error_type=type(exc).__name__)
            return False
    log.warning("ifcgraph_partial_left", model_id=model_id, error_type=DatabaseFull.__name__)
    return False


def _conversions(graph: Graph, postgres: Postgres | None) -> ConversionStore:
    from opencde.store.jobs.postgres import conversion_store  # it imports what imports this module

    return conversion_store(graph, postgres)


def delete_geometry(storage: LocalStorage, model_id: str) -> int:
    """Delete a model's geometry records and the glTF files built from them. Returns how many files went. A file
    that cannot be removed, as one a download holds open on Windows, stays and is named in the log, and the model's
    other files still go."""
    gone = 0
    for key in list(storage.iter_keys("geometry")):
        if key.startswith(f"geometry/{model_id}."):
            try:
                storage.delete(key)
            except OSError as exc:
                log.warning("geometry_file_left", model_id=model_id, storage_key=key, error_type=type(exc).__name__)
                continue
            gone += 1
    return gone


async def list_models(
    graph: Graph, *, project_id: str | None = None, postgres: Postgres | None = None
) -> list[dict[str, Any]]:
    return await _conversions(graph, postgres).all(project_id=project_id)


async def superseded_or_failed(
    graph: Graph, *, project_id: str | None = None, postgres: Postgres | None = None
) -> list[str]:
    """Ids of the models to drop: every failed one, and every finished one that a newer finished conversion of the
    same document (Documents API ``document_id`` or BCF ``bcf_document_guid``) has replaced."""
    return await _conversions(graph, postgres).superseded_or_failed(project_id=project_id)


async def prune_models(
    graph: Graph,
    *,
    store: Graph | None = None,
    project_id: str | None = None,
    everything: bool = False,
    storage: LocalStorage | None = None,
    postgres: Postgres | None = None,
) -> dict[str, int]:
    """Delete models from the IFC graph ``graph``, their conversions from the ``store`` (the IFC graph itself when
    none is given, PostgreSQL when ``postgres`` is) and their geometry files; returns ``{"models": n, "entities": n}``."""
    store = store or graph
    conversions = _conversions(store, postgres)
    if everything:
        if project_id is None:
            raise ValueError("deleting every model needs a project")
        ids = [model["id"] for model in await conversions.all(project_id=project_id)]
    else:
        ids = await conversions.superseded_or_failed(project_id=project_id)
    entities = 0
    for model_id in ids:
        entities += await delete_model(graph, model_id)
        await conversions.delete(model_id)
        if storage is not None:
            delete_geometry(storage, model_id)
    return {"models": len(ids), "entities": entities}


async def delete_project(projects: ProjectStore, graph: Graph | None, project_id: str, storage: LocalStorage) -> bool:
    """A project's models in the IFC graph ``graph``, in batches of their own (the entities of a model reach far beyond
    what the store's deletion follows, and they live in the other graph), and then the project, what it owns and its
    stored files in the store behind ``projects``. Returns whether the project was there. ``graph`` is None on a
    server that uses no graph (tiers 1 and 2, F58 a): its records of models made before go with the project.

    The models are taken out once more after the project: a job still running may import between the two, and
    once the records are gone the job takes out what it imports itself (``IfcGraphService._gone``,
    ``merging._prepare``). A model already gone is nothing to delete."""
    if graph is None:
        return await projects.delete_with_files(project_id, storage)
    model_ids = await projects.model_ids(project_id)
    for model_id in model_ids:
        await delete_model(graph, model_id)
    deleted = await projects.delete_with_files(project_id, storage)
    for model_id in model_ids:
        await delete_model(graph, model_id)
    return deleted
