# This file was generated with the assistance of an AI coding tool.
"""ifcmerge as the server runs it (ADR 0030, UL-48, fas 5.3): what a file must be for it, and how its answers are read.

ifcmerge itself is GPL-3.0-or-later and needs Perl with DateTime, so these tests run a stand-in written here that
answers as ifcmerge does: ``Success!`` and the merged file, or a JSON report over several lines on stdout and exit 1.
The real one is tried where ``OPENCDE_TEST_IFCMERGE`` names it (CI, the image)."""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
from pathlib import Path

import pytest

from opencde.repos.merge import Merged, MergeError, environment, merge, re_exported, shape, why_not

STAND_IN = r"""
import json, shutil, sys, time
flag, base, theirs, ours, merged = sys.argv[1:6]
assert flag == "--prioritise-local"
text = open(ours, encoding="utf-8").read()
if "CONFLICT" in text:
    report = {"status": "failed", "message": "Merge failed due to conflicts", "conflicts": [
        {"type": "attribute_conflict", "entity_id": 5, "attribute_index": 3, "entity_class": "IfcWall",
         "base_value": "'a'", "local_value": "'b\x07'", "remote_value": "'c'"}]}
    print(json.dumps(report, indent=2).replace("\\u0007", "\x07"))
    sys.exit(1)
if "BROKEN" in text:
    sys.exit(1)
if "SLOW" in text:
    time.sleep(30)
print("Success!")
shutil.copyfile(theirs, merged)
"""


def model(*lines: str, schema: str = "IFC4") -> bytes:
    head = f"ISO-10303-21;\nHEADER;\nFILE_SCHEMA(('{schema}'));\nENDSEC;\nDATA;\n"
    return (head + "".join(line + "\n" for line in lines) + "ENDSEC;\nEND-ISO-10303-21;\n").encode()


WALL = "#5=IFCWALL('2O2Fr$t4X7Zf8NOew3FLOH',$,'W',$,$,$,$,$,$);"


def _shape(tmp_path: Path, data: bytes):
    path = tmp_path / f"{len(list(tmp_path.iterdir()))}.ifc"
    path.write_bytes(data)
    return shape(path)


def test_a_file_is_merged_only_when_every_line_of_its_data_is_one_instance(tmp_path: Path) -> None:
    one = _shape(tmp_path, model("#1=IFCPROJECT('0YvctVUKr0kugbFTf53O9L',$,'P',$,$,$,$,$,$);", WALL))
    assert (
        one.whole
        and one.schema == "IFC4"
        and one.global_ids == {"0YvctVUKr0kugbFTf53O9L": 1, "2O2Fr$t4X7Zf8NOew3FLOH": 5}
    )
    split = _shape(tmp_path, model("#5=IFCWALL('2O2Fr$t4X7Zf8NOew3FLOH',$,", "'W',$,$,$,$,$,$);"))
    spaced = _shape(tmp_path, model("#5 = IFCWALL('2O2Fr$t4X7Zf8NOew3FLOH',$,'W',$,$,$,$,$,$);"))
    assert not split.whole and not spaced.whole, "ifcmerge reads #id=...; at the start of a line, nothing else"
    assert why_not(one, one, one) is None
    assert "whole instance" in str(why_not(one, split, one))
    assert "schema" in str(why_not(one, _shape(tmp_path, model(WALL, schema="IFC2X3")), one))


def test_a_re_export_is_never_merged(tmp_path: Path) -> None:
    base = _shape(tmp_path, model(WALL, "#6=IFCWALL('3eT96F_cnDsOufCEsotwtp',$,'V',$,$,$,$,$,$);"))
    renumbered = _shape(
        tmp_path,
        model(
            "#15=IFCWALL('2O2Fr$t4X7Zf8NOew3FLOH',$,'W',$,$,$,$,$,$);",
            "#16=IFCWALL('3eT96F_cnDsOufCEsotwtp',$,'V',$,$,$,$,$,$);",
        ),
    )
    assert re_exported(base, renumbered) and not re_exported(base, base)
    assert "re-export" in str(why_not(base, base, renumbered))


def test_perl_gets_no_variable_that_loads_a_module(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PERL5LIB", "/tmp/evil")
    monkeypatch.setenv("PERL5OPT", "-Mevil")
    env = environment()
    assert env["TZ"] == "UTC" and env["LC_ALL"] == "C" and "PERL5LIB" not in env and "PERL5OPT" not in env


def _merge(tmp_path: Path, ours: bytes, seconds: float = 20) -> Merged:
    stand_in = tmp_path / "stand_in.py"
    stand_in.write_text(STAND_IN, encoding="utf-8")
    files = {}
    for name, data in (("b", model(WALL)), ("t", model(WALL)), ("o", ours)):
        files[name] = tmp_path / f"{name}.ifc"
        files[name].write_bytes(data)
    folder = tmp_path / "merging"
    folder.mkdir(exist_ok=True)
    return asyncio.run(
        merge(
            perl=sys.executable,
            ifcmerge=str(stand_in),
            base=files["b"],
            theirs=files["t"],
            ours=files["o"],
            folder=folder,
            seconds=seconds,
            memory_mb=1024,
        )
    )


def test_a_merge_gives_the_merged_file(tmp_path: Path) -> None:
    done = _merge(tmp_path, model(WALL, "#6=IFCWALL($);"))
    assert done.path == tmp_path / "merging" / "merged.ifc" and done.conflicts == []
    assert done.path.read_bytes() == model(WALL)


def test_the_conflicts_are_read_from_the_report_over_several_lines(tmp_path: Path) -> None:
    done = _merge(tmp_path, model(WALL, "/* CONFLICT */"))
    assert done.path is None and done.conflicts[0]["type"] == "attribute_conflict"
    assert done.conflicts[0]["local_value"] == "'b\x07'", "a raw control character in a value is read"


def test_an_answer_that_is_neither_is_an_error_without_the_files_text(tmp_path: Path) -> None:
    with pytest.raises(MergeError, match="exit code 1") as raised:
        _merge(tmp_path, model(WALL, "/* BROKEN */"))
    assert "BROKEN" not in str(raised.value)
    with pytest.raises(MergeError, match="exceeded"):
        _merge(tmp_path, model(WALL, "/* SLOW */"), seconds=2)


REAL = os.environ.get("OPENCDE_TEST_IFCMERGE")


@pytest.mark.skipif(not REAL or shutil.which("perl") is None, reason="OPENCDE_TEST_IFCMERGE names no ifcmerge")
def test_the_real_ifcmerge_merges_changes_to_two_attributes_and_reports_one_changed_twice(tmp_path: Path) -> None:
    """Our own fixtures: the base has a wall; main renames it and the upload changes its description, which merge;
    then both rename it, which is a conflict on the attribute, named by its index."""
    base = tmp_path / "base.ifc"
    base.write_bytes(model(WALL))
    theirs = tmp_path / "theirs.ifc"
    theirs.write_bytes(model("#5=IFCWALL('2O2Fr$t4X7Zf8NOew3FLOH',$,'Main',$,$,$,$,$,$);"))
    ours = tmp_path / "ours.ifc"
    ours.write_bytes(model("#5=IFCWALL('2O2Fr$t4X7Zf8NOew3FLOH',$,'W','Described',$,$,$,$,$);"))

    def run(upload: Path, name: str) -> Merged:
        folder = tmp_path / name
        folder.mkdir()
        return asyncio.run(
            merge(
                perl="perl",
                ifcmerge=str(REAL),
                base=base,
                theirs=theirs,
                ours=upload,
                folder=folder,
                seconds=60,
                memory_mb=2048,
            )
        )

    done = run(ours, "one")
    assert done.path is not None
    assert b"#5=IFCWALL('2O2Fr$t4X7Zf8NOew3FLOH',$,'Main','Described',$,$,$,$,$);" in done.path.read_bytes()
    both = tmp_path / "both.ifc"
    both.write_bytes(model("#5=IFCWALL('2O2Fr$t4X7Zf8NOew3FLOH',$,'Upload',$,$,$,$,$,$);"))
    conflict = run(both, "two")
    assert conflict.path is None
    assert [(c["type"], c["entity_id"], c["attribute_index"]) for c in conflict.conflicts] == [
        ("attribute_conflict", 5, 3)
    ]
