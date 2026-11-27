# This file was generated with the assistance of an AI coding tool.
"""ifcmerge, the three-way merge of an upload on an older base with the versions since (ADR 0030, UL-48, fas 5.3).

The server runs Bruno Postle's ifcmerge (GPL-3.0-or-later) as a program of its own, never as code of this one:
``perl ifcmerge --prioritise-local base.ifc theirs.ifc ours.ifc merged.ifc``, where *theirs* is the document's latest
version (main) and *ours* the upload, so that main's STEP ids are never rewritten and the upload's additions move up.
The four files have the server's own names in a folder of their own; Perl gets ``PATH``, ``TZ=UTC`` and ``LC_ALL=C``
alone (never ``PERL5LIB`` or ``PERL5OPT``, which load modules), a time limit, an address-space limit on POSIX, and one
merge runs at a time. Exit 0 with ``Success!`` is a merge, exit 1 with a JSON report on stdout its conflicts, and
anything else an error whose words never hold the files' text.

ifcmerge reads a line ``#id=...;`` as an instance and everything else as the header, so a file is merged only when
every line of its data is one whole instance (``ready``), the three have one schema, and none is a re-export, whose
GlobalIds have other STEP ids than its base's (``re_exported``).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from opencde.documents.step import MAX_LINE

_DATA = re.compile(rb"^\s*DATA\s*;\s*$")
_ENDSEC = re.compile(rb"^\s*ENDSEC\s*;\s*$")
_WHOLE = re.compile(rb"^#[0-9]+=.*;\s*$")
_SCHEMA = re.compile(rb"FILE_SCHEMA\s*\(\s*\(\s*'([A-Za-z0-9_]+)'")
_ROOTED = re.compile(rb"^#([0-9]+)=IFC[A-Z0-9_]*\('([0-9A-Za-z_$]{22})'")
KEPT = ("PATH", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "TEMP", "TMP")
#: The share of the GlobalIds two files have in common whose STEP ids may differ before one counts as a re-export.
RE_EXPORT_SHARE = 0.5
_one_at_a_time: dict[int, asyncio.Semaphore] = {}


class MergeError(RuntimeError):
    """A merge that could not run, or whose answer could not be read; the message never holds a file's text."""


@dataclass
class Merged:
    """What ifcmerge answered: the merged file, or the conflicts it found."""

    path: Path | None = None
    conflicts: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class Shape:
    """What a file must have for ifcmerge to read it as it is: whole instances, its schema, its GlobalIds."""

    whole: bool
    schema: str | None
    global_ids: dict[str, int]


def shape(path: Path) -> Shape:
    """Read a file once, line by line, for ``ready`` and ``re_exported``."""
    whole = True
    schema: str | None = None
    ids: dict[str, int] = {}
    in_data = False
    with path.open("rb") as stream:
        while line := stream.readline(MAX_LINE):
            if not line.endswith(b"\n") and len(line) == MAX_LINE:
                whole = False  # a line longer than the reader takes: not the one-instance-a-line ifcmerge needs
                while (rest := stream.readline(MAX_LINE)) and not rest.endswith(b"\n"):
                    pass
                continue
            if not in_data:
                if schema is None and (named := _SCHEMA.search(line)):
                    schema = named.group(1).decode("ascii").upper()
                in_data = bool(_DATA.match(line))
                continue
            if _ENDSEC.match(line):
                in_data = False
                continue
            if not line.strip():
                continue
            if not _WHOLE.match(line):
                whole = False
                continue
            if rooted := _ROOTED.match(line):
                ids[rooted.group(2).decode("ascii")] = int(rooted.group(1))
    return Shape(whole=whole, schema=schema, global_ids=ids)


def re_exported(before: Shape, after: Shape) -> bool:
    """Whether more than half of the GlobalIds two files share have another STEP id in the later one."""
    shared = [guid for guid in after.global_ids if guid in before.global_ids]
    if not shared:
        return False
    moved = sum(1 for guid in shared if before.global_ids[guid] != after.global_ids[guid])
    return moved > RE_EXPORT_SHARE * len(shared)


def why_not(base: Shape, theirs: Shape, ours: Shape) -> str | None:
    """Why ifcmerge cannot merge these three, or None when it can."""
    if not (base.whole and theirs.whole and ours.whole):
        return "a file has a line that is not one whole instance, which ifcmerge cannot read"
    if len({base.schema, theirs.schema, ours.schema}) != 1 or base.schema is None:
        return "the files are not of one IFC schema"
    if re_exported(base, ours) or re_exported(base, theirs):
        return "a file is a re-export, whose GlobalIds have other STEP ids than its base's"
    return None


def environment() -> dict[str, str]:
    """What Perl gets: the system's paths, UTC and the C locale; nothing that loads a module."""
    env = {name: os.environ[name] for name in KEPT if name in os.environ}
    env.update(TZ="UTC", LC_ALL="C")
    return env


def command(perl: str, ifcmerge: str, folder: Path, memory_mb: int) -> list[str]:
    """The merge's command line: the three files and the result in ``folder``, under an address-space limit where the
    system has one."""
    files = [str(folder / name) for name in ("base.ifc", "theirs.ifc", "ours.ifc", "merged.ifc")]
    run = [perl, ifcmerge, "--prioritise-local", *files]
    if sys.platform == "win32":
        return run
    return [sys.executable, "-I", "-m", "opencde.repos.limited", str(memory_mb), *run]


async def merge(
    *, perl: str, ifcmerge: str, base: Path, theirs: Path, ours: Path, folder: Path, seconds: float, memory_mb: int
) -> Merged:
    """Merge ``ours`` (the upload) and ``theirs`` (main) from ``base``, in ``folder``, one merge at a time."""
    for name, source in (("base.ifc", base), ("theirs.ifc", theirs), ("ours.ifc", ours)):
        shutil.copyfile(source, folder / name)
    loop = id(asyncio.get_running_loop())
    lock = _one_at_a_time.setdefault(loop, asyncio.Semaphore(1))
    async with lock:
        try:
            process = await asyncio.create_subprocess_exec(
                *command(perl, ifcmerge, folder, memory_mb),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=environment(),
                cwd=str(folder),
            )
        except OSError as exc:
            raise MergeError(f"ifcmerge could not start: {type(exc).__name__}") from None
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=seconds)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise MergeError(f"the merge exceeded {seconds} s") from None
    merged = folder / "merged.ifc"
    if process.returncode == 0 and b"Success!" in stdout and merged.is_file():
        return Merged(path=merged)
    if process.returncode == 1 and stdout.strip():
        try:  # the report is the whole of stdout, over several lines; strict=False takes a raw control character
            report = json.loads(stdout.decode("utf-8", "replace"), strict=False)
        except ValueError:
            raise MergeError("ifcmerge's report could not be read") from None
        if isinstance(report, dict) and report.get("status") == "failed" and isinstance(report.get("conflicts"), list):
            return Merged(conflicts=[c for c in report["conflicts"] if isinstance(c, dict)])
    raise MergeError(f"ifcmerge ended with exit code {process.returncode}")


# -- moved placements, which ifcmerge settles by itself (F201 b) ------------------------------------------------------

_PLACEMENT = re.compile(rb"^IFC(LOCAL|LINEAR|GRID)PLACEMENT\b")
_REFERENCE = re.compile(rb"^#([0-9]+)$")


def split_attributes(body: bytes) -> list[bytes]:
    """The top-level attributes of an instance's body ``CLASS(a,b,(c,d),'e,f')``, as written."""
    start = body.find(b"(")
    if start < 0 or not body.endswith(b")"):
        return []
    inner = body[start + 1 : -1]
    parts: list[bytes] = []
    depth = 0
    quoted = False
    at = 0
    for index, byte in enumerate(inner):
        if byte == 0x27:  # '
            quoted = not quoted
        elif quoted:
            continue
        elif byte == 0x28:  # (
            depth += 1
        elif byte == 0x29:  # )
            depth -= 1
        elif byte == 0x2C and depth == 0:  # ,
            parts.append(inner[at:index].strip())
            at = index + 1
    parts.append(inner[at:].strip())
    return parts


def _lines(path: Path, wanted: set[int]) -> dict[int, bytes]:
    """The bodies (``CLASS(...)``) of the instances ``wanted`` in a file, read line by line as ifcmerge reads it."""
    found: dict[int, bytes] = {}
    with path.open("rb") as stream:
        while line := stream.readline(MAX_LINE):
            if not line.startswith(b"#"):
                continue
            head, _, rest = line.partition(b"=")
            if head[1:].isdigit() and int(head[1:]) in wanted:
                found[int(head[1:])] = rest.rstrip().removesuffix(b";")
    return found


def placements_moved_twice(base: Path, theirs: Path, ours: Path) -> list[dict[str, Any]]:
    """An attribute that both sides changed to different references to a placement, which ifcmerge settles for main
    without a word: F201 b counts it as a conflict, in the form of ifcmerge's own (``type`` ``placement_conflict``).
    The files are compared instance by instance first, so that only the few changed on both sides are read."""
    import numpy as np

    from opencde.documents.step import fingerprints

    prints = []
    for path in (base, theirs, ours):
        with path.open("rb") as stream:
            prints.append(fingerprints(stream))
    if any(found.over for found in prints):
        return []
    size = max(len(found.prints) for found in prints)
    arrays = []
    for found in prints:
        array = np.zeros(size, np.uint64)
        array[: len(found.prints)] = np.frombuffer(found.prints, dtype=np.uint64)
        arrays.append(array)
    b, t, o = arrays
    both = set(np.flatnonzero((b != 0) & (t != 0) & (o != 0) & (b != t) & (b != o) & (t != o)).tolist())
    if not both:
        return []
    then, main, upload = _lines(base, both), _lines(theirs, both), _lines(ours, both)
    candidates: list[tuple[int, int, bytes, bytes, bytes]] = []
    for step_id in sorted(both):
        old, mine, yours = (split_attributes(lines.get(step_id, b"")) for lines in (then, main, upload))
        for index, (x, y, z) in enumerate(zip(old, mine, yours, strict=False)):
            if x != y and x != z and y != z and _REFERENCE.match(y) and _REFERENCE.match(z):
                candidates.append((step_id, index, x, y, z))
    referred = {int(value[1:]) for *_, y, z in candidates for value in (y, z)}
    classes = {**_lines(ours, referred), **_lines(theirs, referred)}
    conflicts = []
    for step_id, index, x, y, z in candidates:
        if all(_PLACEMENT.match(classes.get(int(value[1:]), b"")) for value in (y, z)):
            conflicts.append(
                {
                    "type": "placement_conflict",
                    "entity_id": step_id,
                    "attribute_index": index + 1,
                    "entity_class": main[step_id].partition(b"(")[0].decode("ascii", "replace"),
                    "base_value": x.decode("utf-8", "replace"),
                    "local_value": y.decode("utf-8", "replace"),
                    "remote_value": z.decode("utf-8", "replace"),
                }
            )
    return conflicts
