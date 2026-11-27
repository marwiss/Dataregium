# This file was generated with the assistance of an AI coding tool.
"""A file merged with the versions since its base by ifcmerge (ADR 0030, UL-48): an upload on an older base (fas 5.3)
and a change set approved on one (fas 5.4) go the same way.

``merged`` gives the merged file, or 412 ``version_conflict`` with ifcmerge's conflicts in the approval's form and the
placements both sides moved (F201 b), or with ``["*"]`` when the files cannot be merged as text; the body has
``conflicts``, ``conflict_details`` and ``merge`` at its top level. ifcmerge moves the other side's new instances up
past main's highest id (``--prioritise-local``), the actors both sides made alike stand once
(``collapse_identities``), and ``renumbering`` says where each one went, with what the safety net moved after it, so
that a client can follow its new elements (SA-99).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import anyio
from fastapi import HTTPException

from opencde.documents.schemas import DocumentMetadataEntry
from opencde.documents.step import ALL, MAX_LISTED, IdSet, Protection, scan
from opencde.errors import CODE_VERSION_CONFLICT, ApiError
from opencde.logging import get_logger
from opencde.repos.conflicts import details as conflict_details
from opencde.repos.merge import (
    MergeError,
    collapse_identities,
    inputs,
    merge,
    placements_moved_twice,
    shape,
    why_not,
)

if TYPE_CHECKING:
    from pathlib import Path

    from opencde.ifcgraph.service import IfcGraphService
    from opencde.storage import LocalStorage
    from opencde.store.documents.versions import VersionRecord

log = get_logger(__name__)

EVERYTHING = "*"  # the files cannot be merged as text: anything may collide


@dataclass(frozen=True)
class ThreeWay:
    """The merged file, in the merge's folder, and how the other side's ids moved: those above ``above``, the base's
    highest, went up by ``offset``, main's highest less the base's (ifcmerge), and an actor main had already went
    into main's (``collapsed``)."""

    path: Path
    above: int
    offset: int
    collapsed: dict[int, int] = field(default_factory=dict)


async def merged(
    *,
    ifcgraph: IfcGraphService,
    storage: LocalStorage,
    what: str,
    project_id: str,
    based: VersionRecord,
    latest: VersionRecord,
    ours: tuple[str, str],
    folder: Path,
) -> ThreeWay:
    """Merge ``ours`` (a stored file's key and name: the upload, or the trial's file) built on ``based`` with
    ``latest``, in ``folder``; ``what`` names it in the answers ("The upload")."""
    paths = (storage.path(based.storage_key), storage.path(latest.storage_key), storage.path(ours[0]))
    shapes = [await anyio.to_thread.run_sync(shape, path) for path in paths]
    where = {"base_version_index": based.version_index, "latest_version_index": latest.version_index}
    if (reason := why_not(*shapes)) is not None:
        raise ApiError(
            412,
            f"{what} builds on version {based.version_index}, and the document's latest version is "
            f"{latest.version_index}: {reason}, so it cannot be merged",
            code=CODE_VERSION_CONFLICT,
            details={"conflicts": [EVERYTHING], "conflict_details": [], "merge": where},
        )
    settings = ifcgraph.settings
    given = await anyio.to_thread.run_sync(inputs, folder, *paths)
    moved = await anyio.to_thread.run_sync(placements_moved_twice, *given.paths)
    try:
        done = await merge(
            perl=settings.perl_path,
            ifcmerge=str(settings.ifcmerge_path),
            base=given.paths[0],
            theirs=given.paths[1],
            ours=given.paths[2],
            folder=folder,
            seconds=settings.ifcmerge_timeout_seconds,
            memory_mb=settings.ifcmerge_memory_mb,
        )
    except MergeError as exc:
        log.warning("ifcmerge_failed", project_id=project_id, error=str(exc)[:200])
        raise HTTPException(500, f"{what} could not be merged") from None
    conflicts = done.conflicts + moved
    if conflicts or done.path is None:
        guids = {step_id: guid for guid, step_id in shapes[1].global_ids.items()}
        keys, listed = await conflict_details(
            ifcgraph,
            conflicts,
            schema=str(shapes[1].schema),
            files=((based.storage_key, based.file_name), (latest.storage_key, latest.file_name), ours),
            guids=guids,
            latest_index=latest.version_index,
        )
        raise ApiError(
            412,
            f"{what} builds on version {based.version_index}, and what it changes collides with what version "
            f"{latest.version_index} changed",
            code=CODE_VERSION_CONFLICT,
            details={"conflicts": keys or [EVERYTHING], "conflict_details": listed, "merge": where},
        )
    collapsed = await anyio.to_thread.run_sync(collapse_identities, done.path, given.paths[1])
    base, main, _ = given.highest
    return ThreeWay(path=done.path, above=base, offset=max(main - base, 0), collapsed=collapsed)


def renumbering(ours: Path, merge: ThreeWay, after: Protection) -> tuple[list[tuple[int, int]], int]:
    """The ids of the file at ``ours`` that are not where they were in the version: moved by ifcmerge, and then by the
    safety net (``after``), as ``(id in ours, id in the version)``, the first ``MAX_LISTED`` in order, and how many
    there are. Should the safety net have moved more than it lists, the count alone is given, as the rest is not known.
    Blocking: run it in a thread."""
    found = IdSet()
    with ours.open("rb") as stream:
        scan(stream, ALL, into=found)
    netted = dict(after.renumbered)
    if after.renumbered_count > len(after.renumbered):
        netted = {}
    pairs: list[tuple[int, int]] = []
    count = 0
    start = (merge.above + 1) >> 3
    for index in range(start, len(found.bits)):
        byte = found.bits[index]
        if not byte:
            continue
        for bit in range(8):
            old = index * 8 + bit
            if not byte >> bit & 1 or old <= merge.above:
                continue
            moved = old + merge.offset
            moved = merge.collapsed.get(moved, moved)
            new = netted.get(moved, moved)
            if new != old:
                count += 1
                if len(pairs) < MAX_LISTED:
                    pairs.append((old, new))
    if after.renumbered_count > len(after.renumbered):
        return [], max(count, after.renumbered_count)
    return pairs, count


def renumbered_metadata(pairs: list[tuple[int, int]], count: int) -> list[DocumentMetadataEntry]:
    """The ids that moved, as ``old>new``, the first ``MAX_LISTED``, and their number beyond: the client fetches the
    version again (SA-103)."""
    entries = []
    if pairs:
        entries.append(
            DocumentMetadataEntry(
                name="ifc_renumbered", value=[f"{old}>{new}" for old, new in pairs], data_type="string"
            )
        )
    if count > len(pairs):
        entries.append(DocumentMetadataEntry(name="ifc_renumbered_count", value=[str(count)], data_type="integer32"))
    return entries
