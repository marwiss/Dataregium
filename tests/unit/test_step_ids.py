# This file was generated with the assistance of an AI coding tool.
"""The STEP ids of an IFC file read as text (opencde.documents.step), and the highest id a document has had in a
version's metadata (ifc_max_step_id, SA-103, UL-48)."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from opencde.documents.step import (
    MAX_ID,
    MAX_LINE,
    Protection,
    StepIds,
    fingerprints,
    is_step,
    protect_stored,
    scan,
)
from opencde.ids import new_id
from opencde.storage import LocalStorage, make_key
from opencde.store.documents.versions import MAX_STEP_ID, VersionRecord

HEADER = b"ISO-10303-21;\nHEADER;\nFILE_SCHEMA(('IFC4'));\nENDSEC;\nDATA;\n"


def _scan(data: bytes) -> StepIds:
    return scan(io.BytesIO(data), 1 << 30)


def test_the_highest_id_and_the_guards_are_read_line_by_line() -> None:
    data = HEADER + (
        b"#1=IFCPROJECT('0YvctVUKr0kugbFTf53O9L',$,'P',$,$,$,$,$,$);\n"
        b"#7= IFCWALL('2O2Fr$t4X7Zf8NOew3FLOH',$,'#99 in a name',$,$,$,$,$,$);\n"
        b"#12=IFCDOCUMENTREFERENCE($,'11','StepIdGuard',$,$);\n"
        b"ENDSEC;\nEND-ISO-10303-21;\n"
    )
    found = _scan(data)
    assert (found.highest, found.guards, found.schema) == (12, {12: 11}, "IFC4")
    assert found.history == 12, "the guard's own id is above the one it protects"
    protecting = _scan(HEADER + b"#3=IFCDOCUMENTREFERENCE($,'40','StepIdGuard',$,$);\n#4=IFCWALL($);\n")
    assert protecting.history == 40, "a guard that protects more than the file's highest raises the history"


def test_a_reference_or_a_continued_line_is_no_instance() -> None:
    long = b"#5=IFCPOLYLINE((" + b"#1," * (MAX_LINE // 3) + b"#2));\n"
    data = HEADER + long + b"#6=IFCWALL($);\n"
    found = _scan(data)
    assert found.highest == 6, "the pieces of a line longer than MAX_LINE start no instance"
    assert _scan(HEADER + b"#2=IFCWALL(#1,\n#300);\n").highest == 2, "a reference that starts a line is not an id"


def test_a_schema_split_over_lines_and_an_endless_number_are_read() -> None:
    found = _scan(b"ISO-10303-21;\nHEADER;\nFILE_SCHEMA(\n  ('IFC2X3'));\nENDSEC;\nDATA;\n#" + b"9" * 5000 + b"=X();\n")
    assert found.schema == "IFC2X3" and found.highest == (1 << 63) - 1, "a number no tool gives is read as the largest"


def test_a_file_that_is_not_step_has_none() -> None:
    assert _scan(b"not a model\n").history == 0
    assert is_step("Model.IFC") and not is_step("model.ifczip") and not is_step("notes.txt")


def _record(file_name: str, max_step_id: int | None) -> VersionRecord:
    return VersionRecord(
        document_id="d",
        project_id="p",
        project_name="P",
        version_id="v",
        version_index=1,
        version_number="1",
        creation_date="2026-11-26T00:00:00.000Z",
        title="T",
        file_name=file_name,
        size_in_bytes=1,
        sha256="0" * 64,
        storage_key="documents/x",
        author="a",
        metadata=[
            {"name": MAX_STEP_ID, "value": ["3"], "data_type": "integer64"},
            {"name": "n", "value": ["v"], "data_type": "string"},
        ],
        max_step_id=max_step_id,
    )


def test_an_ifc_version_carries_its_documents_highest_id_as_it_stands() -> None:
    entries = [entry.model_dump() for entry in _record("model.ifc", 42).metadata_entries()]
    assert entries[-1] == {"name": MAX_STEP_ID, "value": ["42"], "data_type": "integer64"}
    assert [e["name"] for e in entries].count(MAX_STEP_ID) == 1, "the document's value replaces a stored one"
    assert MAX_STEP_ID not in [e.name for e in _record("notes.txt", 42).metadata_entries()][1:]
    unknown = [e.model_dump() for e in _record("model.ifc", None).metadata_entries()]
    assert unknown[0]["value"] == ["3"], "unknown, the version keeps what it had"


# -- the safety net of an upload (UL-48, F98 a) ---------------------------------------------------------------------


def _model(*lines: bytes, schema: bytes = b"IFC4") -> bytes:
    head = b"ISO-10303-21;\nHEADER;\nFILE_SCHEMA(('" + schema + b"'));\nENDSEC;\nDATA;\n"
    return head + b"".join(line + b"\n" for line in lines) + b"ENDSEC;\nEND-ISO-10303-21;\n"


def _protect(
    tmp_path: Path, upload: bytes, parent: bytes | None, known: int | None, parent_name: str = "model.ifc"
) -> tuple[bytes, Protection]:
    root = tmp_path / new_id()
    storage = LocalStorage(root)
    key, parent_key = make_key("documents", new_id()), make_key("documents", new_id())
    storage.write_bytes(key, upload)
    if parent is not None:
        storage.write_bytes(parent_key, parent)
    done = protect_stored(storage, key, parent_key if parent is not None else None, parent_name, known)
    assert list(storage.iter_keys("documents")) == sorted([key] + ([parent_key] if parent is not None else []))
    assert not list((root / "documents").glob("*.tmp")), "the rewrite leaves nothing beside the file"
    return storage.path(key).read_bytes(), done


def test_an_id_a_version_deleted_is_moved_above_the_history_everywhere_but_in_strings(tmp_path: Path) -> None:
    """LD-61: version 2 deleted #6, and an upload on it gives a new wall #6 again: it becomes #9, above the history
    (the document had 8), in its own line and in every reference, while '#6' in a string or a comment stays, also in
    a string that runs over a line break."""
    parent = _model(b"#1=IFCPROJECT($);", b"#5=IFCWALL($,$,'kept');")
    upload = _model(
        b"#1=IFCPROJECT($);",
        b"#5=IFCWALL($,$,'kept');",
        b"#6=IFCWALL($,$,'#6 it''s new,",
        b"#6 in the same string');",
        b"#7=IFCRELX((#5,#6)); /* #6 isn't moved here */",
    )
    written, done = _protect(tmp_path, upload, parent, known=8)
    assert done.renumbered == [(6, 9), (7, 10)] and done.renumbered_count == 2 and done.guard is None
    assert b"#9=IFCWALL($,$,'#6 it''s new,\n#6 in the same string');\n" in written
    assert b"#10=IFCRELX((#5,#9)); /* #6 isn't moved here */\n" in written
    assert done.size == len(written) and done.sha256 is not None


def test_a_reference_at_the_edge_of_a_piece_is_read_whole(tmp_path: Path) -> None:
    """A line longer than MAX_LINE is rewritten in pieces: #6 cut after its # moves, #60 cut after #6 stays, and a
    comment cut after its / is still a comment."""
    parent = _model(b"#1=IFCPROJECT($);", b"#5=IFCWALL($);")

    def line(own: int, tail: bytes, at: int) -> bytes:
        head = b"#%d=IFCRELX((" % own
        return head + b" " * (at - len(head)) + tail

    upload = _model(
        b"#1=IFCPROJECT($);",
        b"#5=IFCWALL($);",
        b"#6=IFCWALL($);",
        line(20, b"#6,#5));", MAX_LINE - 1),
        line(21, b"#60,#5));", MAX_LINE - 2),
        line(22, b"/* #6 */ #6));", MAX_LINE - 1),
    )
    written, done = _protect(tmp_path, upload, parent, known=8)
    assert done.renumbered == [(6, 23)], "#6 was deleted (the document had 8); #20 to #22 are new"
    lines = written.split(b"\n")
    assert lines[7] == b"#23=IFCWALL($);"
    assert lines[8].endswith(b" #23,#5));") and lines[9].endswith(b" #60,#5));")
    assert lines[10].endswith(b" /* #6 */ #23));")
    assert [len(x) for x in lines[8:11]] == [MAX_LINE + 8, MAX_LINE + 7, MAX_LINE + 14]


def test_a_file_below_the_history_gets_the_guard_once(tmp_path: Path) -> None:
    """F98 a: an upload whose highest instance is below what the document has had gets the guard of the history at
    the end of its data, in the schema's form; one that keeps the guard needs none, and one that leaves it out gets
    the same guard back rather than one higher each time."""
    upload = _model(b"#1=IFCPROJECT($);", b"#4=IFCWALL($);")
    written, done = _protect(tmp_path, upload, upload, known=12)
    assert done.guard == 13 and done.renumbered == []
    assert written.endswith(b"#13=IFCDOCUMENTREFERENCE($,'12','StepIdGuard',$,$);\nENDSEC;\nEND-ISO-10303-21;\n")
    old = _model(b"#1=IFCPROJECT($);", schema=b"IFC2X3")
    assert _protect(tmp_path, old, old, known=5)[0].count(b"#6=IFCDOCUMENTREFERENCE($,'5','StepIdGuard');") == 1
    assert _protect(tmp_path, written, written, known=13) == (written, Protection()), "the guard kept"
    again, done = _protect(tmp_path, upload, written, known=13)
    assert again == written and done.guard == 13, "the guard left out comes back as it was"


def test_an_instance_at_the_parents_guard_is_moved(tmp_path: Path) -> None:
    parent = _model(b"#1=IFCPROJECT($);", b"#4=IFCWALL($);", b"#13=IFCDOCUMENTREFERENCE($,'12','StepIdGuard',$,$);")
    upload = _model(b"#1=IFCPROJECT($);", b"#4=IFCWALL($);", b"#13=IFCWALL($);")
    written, done = _protect(tmp_path, upload, parent, known=13)
    assert done.renumbered == [(13, 14)] and b"#14=IFCWALL($);" in written


def test_a_document_from_before_its_history_was_kept_uses_the_parents_highest(tmp_path: Path) -> None:
    parent = _model(b"#1=IFCPROJECT($);", b"#5=IFCWALL($);", b"#6=IFCWALL($);")
    written, done = _protect(tmp_path, _model(b"#1=IFCPROJECT($);", b"#5=IFCWALL($);"), parent, known=None)
    assert done.guard == 7 and b"#7=IFCDOCUMENTREFERENCE($,'6','StepIdGuard',$,$);" in written


def test_an_upload_that_reuses_nothing_is_kept_as_it_came(tmp_path: Path) -> None:
    parent = _model(b"#1=IFCPROJECT($);", b"#2=IFCWALL($);")
    upload = _model(b"#1=IFCPROJECT($);", b"#2=IFCWALL($);", b"#3=IFCWALL($);")
    assert _protect(tmp_path, upload, parent, known=2) == (upload, Protection())
    assert _protect(tmp_path, upload, None, known=None) == (upload, Protection()), "a new document's file"
    assert _protect(tmp_path, upload, parent, known=9, parent_name="m.ifczip") == (upload, Protection())


@pytest.mark.parametrize("line", [b"#%d=IFCWALL($);" % (MAX_ID + 1), b"#" + b"9" * 5000 + b"=IFCWALL($);"])
def test_ids_no_tool_gives_are_kept_as_they_came(tmp_path: Path, line: bytes) -> None:
    upload = _model(b"#1=IFCPROJECT($);", line)
    assert _protect(tmp_path, upload, _model(b"#1=IFCPROJECT($);"), known=90) == (upload, Protection())


# -- the instances statement by statement, for the diff (UL-48, fas 5.1) ---------------------------------------------


def _prints(data: bytes) -> dict[int, int]:
    found = fingerprints(io.BytesIO(data))
    return {step_id: value for step_id, value in enumerate(found.prints) if value}


def test_an_instance_written_another_way_has_the_same_fingerprint() -> None:
    """Blanks and comments outside strings, a real as 1. or 1.0 or 1.0E0, and a statement over several lines read
    the same, as ifcmerge reads them; a blank inside a string does not, and the guard is no instance."""
    one = _prints(_model(b"#1=IFCCARTESIANPOINT((1.,2.5,0.));", b"#2=IFCWALL('a b',$);"))
    other = _prints(
        _model(
            b"#1 = IFCCARTESIANPOINT ( ( 1.0E0, 2.50, /* x */ 0.000 ) ) ;",
            b"#2=IFCWALL(",
            b"'a b',$);",
            b"#3=IFCDOCUMENTREFERENCE($,'2','StepIdGuard',$,$);",
        )
    )
    assert one == other and set(one) == {1, 2}
    assert _prints(_model(b"#1=IFCCARTESIANPOINT((1.,2.5,0.));", b"#2=IFCWALL('ab',$);"))[2] != one[2]


def test_a_statement_cut_between_pieces_reads_as_a_whole() -> None:
    """A line longer than MAX_LINE is read in pieces; a real or a comment mark cut at a piece's end reads whole."""

    def line(tail: bytes, at: int) -> bytes:
        head = b"#7=IFCPOLYLINE(("
        return head + b" " * (at - len(head)) + tail

    cut = _prints(_model(line(b"1.50,#1));", MAX_LINE - 2), line(b"/* x */ #1));", MAX_LINE - 1).replace(b"#7", b"#8")))
    whole = _prints(_model(b"#7=IFCPOLYLINE((1.5,#1));", b"#8=IFCPOLYLINE((#1));"))
    assert cut == whole


def test_a_file_with_ids_above_the_fingerprints_is_left_to_the_globalids() -> None:
    from opencde.documents.step import MAX_PRINTED_ID

    found = fingerprints(io.BytesIO(_model(b"#%d=IFCWALL($);" % (MAX_PRINTED_ID + 1))))
    assert found.over and len(found.prints) == 0
