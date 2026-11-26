# This file was generated with the assistance of an AI coding tool.
"""The STEP ids of an IFC file, read as text (LD-61, F70 d, UL-48).

A document must never give an id again that one of its versions had and a later one deleted, so the server keeps the
highest id each document has had (``documents.max_step_id``) and gives it to clients as ``ifc_max_step_id``. The file
is read line by line, as IfcOpenShell and most tools write it, one instance a line: an instance starts a line with
``#id=``, and the app's guard (F70 d, ``ops._ifc_is_guard``) is a loose ``IfcDocumentReference`` named ``StepIdGuard``
whose identification is the id it protects. Nothing of the file is executed or kept; a line longer than
``MAX_LINE`` is read in pieces, of which only the first can start an instance.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import IO, TYPE_CHECKING

if TYPE_CHECKING:
    from opencde.storage import LocalStorage

MAX_LINE = 1 << 20
ALL = 1 << 62  # no limit on what is read of a stored file, which the upload has limited already
_INSTANCE = re.compile(rb"^\s*#([0-9]+)\s*=")
_GUARD = re.compile(
    rb"^\s*#([0-9]+)\s*=\s*IFCDOCUMENTREFERENCE\s*\(\s*(?:\$|'[^']*')\s*,\s*'([0-9]+)'\s*,\s*'StepIdGuard'"
)
_SCHEMA = re.compile(rb"FILE_SCHEMA\s*\(\s*\(\s*'([A-Za-z0-9_]+)'")


@dataclass(frozen=True)
class StepIds:
    """What a file says of its ids: its highest instance, the guards among them and the ids they protect."""

    highest: int = 0  # the highest id of an instance, a guard's included
    guards: dict[int, int] = field(default_factory=dict)  # a guard's id and the id it protects
    schema: str | None = None

    @property
    def history(self) -> int:
        """The highest id the file says the document has had: its own highest, or a guard's protected id above it."""
        return max([self.highest, *self.guards.values()])


def scan(stream: IO[bytes], limit: int) -> StepIds:
    """The ids of the file in ``stream``, read up to ``limit`` bytes. A file that is not STEP has none."""
    highest = 0
    guards: dict[int, int] = {}
    schema: str | None = None
    read = 0
    starts = True
    while read < limit and (line := stream.readline(MAX_LINE)):
        read += len(line)
        if starts:
            if schema is None and (named := _SCHEMA.search(line)):
                schema = named.group(1).decode("ascii").upper()
            if found := _INSTANCE.match(line):
                highest = max(highest, int(found.group(1)))
                if guard := _GUARD.match(line):
                    guards[int(guard.group(1))] = int(guard.group(2))
        starts = line.endswith(b"\n")
    return StepIds(highest=highest, guards=guards, schema=schema)


def stored_history(storage: LocalStorage, storage_key: str, file_name: str) -> int | None:
    """The highest id a stored version's file says its document has had (``StepIds.history``), for the store's
    ``max_step_id``; None for a file that is not IFC in STEP or has no instance. Blocking: run it in a thread."""
    if not is_step(file_name):
        return None
    with storage.open_read(storage_key) as stream:
        return scan(stream, ALL).history or None


def is_step(file_name: str) -> bool:
    """Whether a version's file is IFC in STEP, by its name, as the server names the kinds of files elsewhere."""
    return file_name.lower().endswith(".ifc")
