# This file was generated with the assistance of an AI coding tool.
"""The STEP ids of an IFC file read as text (opencde.documents.step), and the highest id a document has had in a
version's metadata (ifc_max_step_id, SA-103, UL-48)."""

from __future__ import annotations

import io

from opencde.documents.step import MAX_LINE, StepIds, is_step, scan
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
