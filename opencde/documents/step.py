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

import hashlib
import os
import re
from array import array
from bisect import bisect_left
from dataclasses import dataclass, field
from typing import IO, TYPE_CHECKING

if TYPE_CHECKING:
    from opencde.storage import LocalStorage

MAX_LINE = 1 << 20
ALL = 1 << 62  # no limit on what is read of a stored file, which the upload has limited already
#: The largest id read as a number; a longer one is read as this, as no tool numbers that high (and ``int`` of a long
#: run of digits is refused).
LARGEST = (1 << 63) - 1
#: The highest id the safety net works with. Its sets are bitmaps, a bit an id (16 MiB at the most); a file or a
#: document with an id above it is kept as it came.
MAX_ID = 1 << 27
#: The most moved ids a version's metadata names one by one; a file that moves more (a re-export) gives the count too.
MAX_LISTED = 1000
_INSTANCE = re.compile(rb"^\s*#([0-9]+)\s*=")
_GUARD = re.compile(
    rb"^\s*#([0-9]+)\s*=\s*IFCDOCUMENTREFERENCE\s*\(\s*(?:\$|'[^']*')\s*,\s*'([0-9]+)'\s*,\s*'StepIdGuard'"
)
_SCHEMA = re.compile(rb"FILE_SCHEMA\s*\(\s*\(\s*'([A-Za-z0-9_]+)'")
_HEADER = 1 << 16  # the most of a file's start searched for its schema


def _number(digits: bytes) -> int:
    return LARGEST if len(digits) > 18 else int(digits)


class IdSet:
    """Instance ids, a bit each, up to ``MAX_ID``; ``over`` says that one above it was left out."""

    def __init__(self) -> None:
        self.bits = bytearray()
        self.over = False

    def add(self, value: int) -> None:
        if value > MAX_ID:
            self.over = True
            return
        index = value >> 3
        if index >= len(self.bits):
            size = min(max(index + 1, 2 * len(self.bits)), (MAX_ID >> 3) + 1)
            self.bits.extend(bytes(size - len(self.bits)))
        self.bits[index] |= 1 << (value & 7)

    def number(self) -> int:
        """The set as one number, bit ``n`` for id ``n``."""
        return int.from_bytes(self.bits, "little")


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


def scan(stream: IO[bytes], limit: int, into: IdSet | None = None) -> StepIds:
    """The ids of the file in ``stream``, read up to ``limit`` bytes; with ``into``, every instance id but a guard's is
    put there as well. A file that is not STEP has none."""
    highest = 0
    guards: dict[int, int] = {}
    schema: str | None = None
    header = b""
    read = 0
    starts = True
    while read < limit and (line := stream.readline(MAX_LINE)):
        read += len(line)
        if schema is None and len(header) < _HEADER:
            header += line[: _HEADER - len(header)]
            if named := _SCHEMA.search(header):
                schema = named.group(1).decode("ascii").upper()
        if starts and (found := _INSTANCE.match(line)):
            value = _number(found.group(1))
            highest = max(highest, value)
            if guard := _GUARD.match(line):
                guards[value] = _number(guard.group(2))
            elif into is not None:
                into.add(value)
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


# -- the safety net of an upload (UL-48, fas 5.0) -------------------------------------------------------------------

_TOKEN = re.compile(rb"'|/\*|\*/|#([0-9]+)")
_SET = re.compile(rb"[^\x00]")
_DATA = re.compile(rb"^\s*DATA\s*;\s*$")
_ENDSEC = re.compile(rb"^\s*ENDSEC\s*;\s*$")
_PLAIN, _QUOTED, _COMMENT = 0, 1, 2


@dataclass(frozen=True)
class Protection:
    """What the server did to an uploaded file before keeping it: the ids it moved, the guard it added."""

    renumbered: list[tuple[int, int]] = field(default_factory=list)  # the first MAX_LISTED moved, (old id, new id)
    renumbered_count: int = 0
    guard: int | None = None  # the id of the guard added
    size: int | None = None  # the file's size and SHA-256 after the change; None when nothing changed
    sha256: str | None = None


class Moved:
    """The ids an upload reuses, in order, and where they go: the n-th of them gets ``start + n``."""

    def __init__(self, bits: bytes, start: int) -> None:
        self.bits = bits
        self.start = start
        self.order = array("q")
        for found in _SET.finditer(bits):
            byte = found.start()
            self.order.extend(byte * 8 + bit for bit in range(8) if bits[byte] >> bit & 1)

    def __len__(self) -> int:
        return len(self.order)

    @property
    def top(self) -> int:
        return self.start + len(self.order) - 1

    def get(self, old: int) -> int | None:
        byte = old >> 3
        if byte >= len(self.bits) or not self.bits[byte] >> (old & 7) & 1:
            return None
        return self.start + bisect_left(self.order, old)

    def pairs(self, limit: int) -> list[tuple[int, int]]:
        return [(old, self.start + n) for n, old in enumerate(self.order[:limit])]


@dataclass(frozen=True)
class Plan:
    """What the safety net does to an upload: the ids that move, the history after it, and the guard."""

    moved: Moved | None
    history: int
    guard: tuple[int, int] | None  # the guard's own id and the id it protects; None when none is added


def plan(upload: IdSet, ids: StepIds, parent: IdSet | None, parent_ids: StepIds | None, known: int | None) -> Plan:
    """An instance the upload has and its parent has not, at or below the highest the document has given (its own
    value, or for a document from before it was kept, the parent's highest), is at an id a version deleted or the
    parent's guard held, and moves above everything (LD-61); guards are no instances. A file whose highest is then
    below the history gets a guard (F98 a): the parent's own again when it is the one at the top, so that a client
    which leaves it out gets the same back, and otherwise a new one of the history at the id above it. Without a
    parent, a new document's file, nothing changes."""
    if parent is None or parent_ids is None:
        return Plan(None, max(known or 0, ids.history), None)
    given = max(known or 0, parent_ids.history)
    history = max(given, ids.history)
    reused = upload.number() & ~parent.number() & ((2 << given) - 1)
    moved = Moved(reused.to_bytes((reused.bit_length() + 7) // 8, "little"), max(history, ids.highest) + 1)
    if moved:
        return Plan(moved, moved.top, None)
    if ids.highest >= history:
        return Plan(None, history, None)
    theirs = max(parent_ids.guards.items(), default=None)
    if theirs is not None and theirs[0] == history:
        return Plan(None, history, theirs)
    return Plan(None, history + 1, (history + 1, history))


def guard_line(schema: str | None, guard: tuple[int, int], newline: bytes) -> bytes:
    """The app's guard (F70 d), ``(its id, the id it protects)``, in the schema's form (SA-110)."""
    tail = "" if (schema or "").upper() == "IFC2X3" else ",$,$"
    return f"#{guard[0]}=IFCDOCUMENTREFERENCE($,'{guard[1]}','StepIdGuard'{tail});".encode("ascii") + newline


def _rewrite(piece: bytes, moved: Moved, state: int, whole: bool) -> tuple[bytes, int, bytes]:
    """A piece with the moved ids given again outside strings and comments, the state at its end (a string or a
    comment may run over a line break, and ``''`` inside a string reads the same as its end and a new start), and
    what of it is held back to be read with the next piece: unless ``whole``, a reference at its very end, or a ``#``,
    ``/`` or ``*`` that a mark may begin with, as a piece of a long line may end in the middle of one."""
    tokens = list(_TOKEN.finditer(piece))
    cut = len(piece)
    if not whole:
        last = tokens[-1] if tokens else None
        if last is not None and last.end() == cut and last.group(1) is not None and len(last.group(0)) <= 20:
            cut = last.start()
            tokens.pop()
        elif piece.endswith((b"#", b"/", b"*")) and (last is None or last.end() < cut):
            cut -= 1
    out: list[bytes] = []
    at = 0
    for found in tokens:
        token = found.group(0)
        if state == _QUOTED:
            state = _PLAIN if token == b"'" else state
        elif state == _COMMENT:
            state = _PLAIN if token == b"*/" else state
        elif token == b"'":
            state = _QUOTED
        elif token == b"/*":
            state = _COMMENT
        elif found.group(1) is not None and (new := moved.get(_number(found.group(1)))) is not None:
            out += (piece[at : found.start()], b"#%d" % new)
            at = found.end()
    out.append(piece[at:cut])
    return b"".join(out), state, piece[cut:]


def protect(source: IO[bytes], target: IO[bytes], schema: str | None, planned: Plan) -> Protection:
    """Write ``source`` to ``target`` as ``planned`` says: the moved ids given again everywhere outside strings and
    comments, and the guard as the last line of the data section. Each line keeps its place; nothing else changes.
    A line is read in pieces of ``MAX_LINE``, so that a long one takes no more memory than a short one."""
    digest = hashlib.sha256()
    size = 0
    in_data = False
    guarded = False
    state = _PLAIN
    starts = True
    carry = b""
    newline: bytes | None = None

    def put(data: bytes) -> None:
        nonlocal size
        target.write(data)
        digest.update(data)
        size += len(data)

    while read := source.readline(MAX_LINE):
        piece, carry = carry + read, b""
        ends = piece.endswith(b"\n")
        if newline is None and ends:
            newline = b"\r\n" if piece.endswith(b"\r\n") else b"\n"
        if starts and state == _PLAIN:
            if _DATA.match(piece):
                in_data = True
            elif in_data and _ENDSEC.match(piece):
                in_data = False
                if planned.guard is not None and not guarded:
                    put(guard_line(schema, planned.guard, newline or b"\n"))
                    guarded = True
        if planned.moved is not None:
            piece, state, carry = _rewrite(piece, planned.moved, state, ends)
        put(piece)
        starts = ends
    if carry and planned.moved is not None:
        put(_rewrite(carry, planned.moved, state, True)[0])
    moved = planned.moved
    return Protection(
        renumbered=moved.pairs(MAX_LISTED) if moved is not None else [],
        renumbered_count=len(moved) if moved is not None else 0,
        guard=planned.guard[0] if guarded and planned.guard is not None else None,
        size=size,
        sha256=digest.hexdigest(),
    )


def protect_stored(
    storage: LocalStorage, key: str, parent_key: str | None, parent_name: str | None, known: int | None
) -> Protection:
    """The safety net on a stored upload, in its place: ``Protection()`` when nothing had to change, else the file
    rewritten beside it (``<key>.tmp``, which no sweep takes) and moved over it, and what was done. A parent that is
    not IFC in STEP counts as none. Blocking: run it in a thread."""
    upload = IdSet()
    with storage.open_read(key) as stream:
        ids = scan(stream, ALL, into=upload)
    if ids.highest == 0 or upload.over or max(known or 0, ids.history) > MAX_ID:
        return Protection()  # nothing read as STEP, for the check to refuse, or ids no tool gives
    parent = parent_ids = None
    if parent_key is not None and parent_name is not None and is_step(parent_name):
        parent = IdSet()
        with storage.open_read(parent_key) as stream:
            parent_ids = scan(stream, ALL, into=parent)
        if parent.over or parent_ids.history > MAX_ID:
            return Protection()
    planned = plan(upload, ids, parent, parent_ids, known)
    if planned.moved is None and planned.guard is None:
        return Protection()
    path = storage.path(key)
    written = path.with_name(path.name + ".tmp")
    try:
        with storage.open_read(key) as source, written.open("wb") as target:
            done = protect(source, target, ids.schema, planned)
        os.replace(written, path)
    except BaseException:
        written.unlink(missing_ok=True)
        raise
    return done


# -- the instances of a file, statement by statement, for the diff (UL-48, fas 5.1) ---------------------------------

_STATEMENT = re.compile(rb"'|/\*|\*/|;")
_REAL = re.compile(rb"(?<![#A-Za-z0-9_])[-+]?[0-9]+\.[0-9]*(?:[Ee][-+]?[0-9]+)?")
_BLANK = re.compile(rb"\s+")
_OPEN = re.compile(rb"[-+0-9.Ee#/*]{1,64}\Z")  # the end of a piece that the next piece may go on
_HEAD = re.compile(rb"#([0-9]+)=")
_GUARD_HEAD = re.compile(rb"#[0-9]+=IFCDOCUMENTREFERENCE\((?:\$|'[^']*'),'[0-9]+','StepIdGuard'")
_HEAD_SIZE = 256


def _real(found: re.Match[bytes]) -> bytes:
    return b"%.10g" % float(found.group(0))


#: The highest id the diff fingerprints, 8 bytes an id (128 MiB a file at the most); a file with one above it is
#: compared by GlobalId alone.
MAX_PRINTED_ID = 1 << 24


class Fingerprints:
    """A fingerprint of every instance of a file but the guards, by id (8 bytes of BLAKE2b; 0 for no instance), up to
    ``MAX_PRINTED_ID``; ``over`` says that one above it was left out."""

    def __init__(self) -> None:
        self.prints = array("Q")
        self.over = False

    def put(self, value: int, digest: bytes) -> None:
        if value > MAX_PRINTED_ID:
            self.over = True
            return
        if value >= len(self.prints):
            size = min(max(value + 1, 2 * len(self.prints)), MAX_PRINTED_ID + 1)
            self.prints.frombytes(bytes(8 * (size - len(self.prints))))
        self.prints[value] = int.from_bytes(digest, "little") or 1


def fingerprints(stream: IO[bytes]) -> Fingerprints:
    """The fingerprint of every instance of the file in ``stream``, each statement read whole over its lines: outside
    strings without blanks or comments and with every real as ``%.10g``, as ifcmerge compares them, so that the same
    instance written another way gives the same print. The app's guard is left out, as it is no part of the model."""
    found = Fingerprints()
    state = _PLAIN
    head = b""
    digest = hashlib.blake2b(digest_size=8)

    def feed(data: bytes) -> None:
        nonlocal head
        if len(head) < _HEAD_SIZE:
            head += data[: _HEAD_SIZE - len(head)]
        digest.update(data)

    def segment(data: bytes) -> None:
        if state == _PLAIN:
            feed(_REAL.sub(_real, _BLANK.sub(b"", data)))
        elif state == _QUOTED:
            feed(data)

    def end() -> None:
        nonlocal head, digest
        if (own := _HEAD.match(head)) and not _GUARD_HEAD.match(head):
            found.put(_number(own.group(1)), digest.digest())
        head, digest = b"", hashlib.blake2b(digest_size=8)

    def consume(piece: bytes, whole: bool) -> bytes:
        """Read a piece; unless ``whole``, give back the end the next piece may go on."""
        nonlocal state
        at = 0
        for token in _STATEMENT.finditer(piece):
            segment(piece[at : token.start()])
            at = token.end()
            mark = token.group(0)
            if state == _COMMENT:
                state = _PLAIN if mark == b"*/" else state
            elif state == _QUOTED:
                feed(mark)
                state = _PLAIN if mark == b"'" else state
            elif mark == b"/*":
                state = _COMMENT
            else:
                feed(mark)
                if mark == b"'":
                    state = _QUOTED
                elif mark == b";":
                    end()
        rest = piece[at:]
        carry = b""
        if not whole:
            if state == _PLAIN and (cut := _OPEN.search(rest, max(0, len(rest) - 64))):
                rest, carry = rest[: cut.start()], rest[cut.start() :]
            elif state == _COMMENT and rest.endswith(b"*"):
                rest, carry = rest[:-1], b"*"
        segment(rest)
        return carry

    carry = b""
    while read := stream.readline(MAX_LINE):
        piece = carry + read
        if state == _PLAIN and not head and piece.endswith(b"\n") and piece.count(b";") == 1 and b"'" not in piece:
            if b"/" not in piece:  # one whole statement on its line, without strings or comments: most of a model
                text = _REAL.sub(_real, _BLANK.sub(b"", piece))
                if (own := _HEAD.match(text)) and not _GUARD_HEAD.match(text):
                    found.put(_number(own.group(1)), hashlib.blake2b(text, digest_size=8).digest())
                carry = b""
                continue
        carry = consume(piece, piece.endswith(b"\n"))
    if carry:
        consume(carry, True)
    return found
