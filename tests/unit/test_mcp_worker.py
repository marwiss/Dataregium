# This file was generated with the assistance of an AI coding tool.
"""IfcMCP's tools in their worker (ADR 0031): what an AI may run, and that an edit stays on the working copy with
the document's STEP ids held."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ifcopenshell = pytest.importorskip("ifcopenshell", reason="the ifc-db extra is not installed")
pytest.importorskip("ifcmcp", reason="IfcMCP is not installed")

from opencde.ifcgraph import mcp_worker  # noqa: E402
from tests.conftest import FIXTURES_DIR  # noqa: E402

MODEL = FIXTURES_DIR / "three_walls_gtin.ifc"


def test_only_allowed_functions_that_take_no_file_are_run() -> None:
    allowed = mcp_worker.allowed_functions()
    assert "attribute.edit_attributes" in allowed and "root.create_entity" in allowed
    assert set(allowed) <= mcp_worker.ALLOWED and "project.append_asset" not in allowed
    import importlib

    append = importlib.import_module("ifcopenshell.api.project.append_asset")
    assert mcp_worker._takes_a_file(append.append_asset), "a library file beside the model"
    with pytest.raises(mcp_worker.Refused, match="not one an AI may run"):
        mcp_worker.run(MODEL, "ifc_edit", {"function_path": "project.append_asset", "params": {}}, write=True,
                       max_step_id=0, proposer="anna")  # fmt: skip
    with pytest.raises(mcp_worker.Refused, match="not offered"):
        mcp_worker.run(MODEL, "ifc_save", {}, write=True, max_step_id=0, proposer="anna")
    assert not any(entry["name"].startswith("_") for entry in mcp_worker.run(None, "ifc_shape_list", {}, write=False,
                                                                              max_step_id=0, proposer=""))  # fmt: skip


def test_an_edit_needs_a_copy_and_numbers_above_the_documents_history(tmp_path: Path) -> None:
    copy = tmp_path / "copy.ifc"
    shutil.copyfile(MODEL, copy)
    wall = ifcopenshell.open(str(MODEL)).by_type("IfcWall")[0].id()
    edit = {"function_path": "attribute.edit_attributes", "params": {"product": wall, "attributes": {"Name": "AI"}}}
    with pytest.raises(mcp_worker.Refused, match="working copy"):
        mcp_worker.run(MODEL, "ifc_edit", edit, write=False, max_step_id=0, proposer="anna")
    mcp_worker.run(copy, "ifc_edit", edit, write=True, max_step_id=5000, proposer="anna")
    model = ifcopenshell.open(str(copy))
    assert model.by_id(wall).Name == "AI" and ifcopenshell.open(str(MODEL)).by_id(wall).Name != "AI"
    made = {"function_path": "root.create_entity", "params": {"ifc_class": "IfcWall", "name": "New"}}
    mcp_worker.run(copy, "ifc_edit", made, write=True, max_step_id=5000, proposer="anna")
    (new,) = [element for element in ifcopenshell.open(str(copy)).by_type("IfcWall") if element.Name == "New"]
    assert new.id() > 5000, "no id the document's history has had"


def test_the_worker_answers_one_line_and_cuts_a_long_list(tmp_path: Path) -> None:
    arguments = tmp_path / "arguments.json"
    arguments.write_text(json.dumps({"query": "IfcWall"}), encoding="utf-8")
    run = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "opencde.ifcgraph.mcp_worker", str(MODEL), "ifc_select", str(arguments), "--budget=400"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 0, run.stderr
    answer = json.loads(run.stdout.strip().splitlines()[-1])
    assert answer["truncated"] is True and answer["count"] == 3 and len(answer["result"]) < 3
    arguments.write_text(json.dumps({"element_id": 999999}), encoding="utf-8")
    missing = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "opencde.ifcgraph.mcp_worker", str(MODEL), "ifc_info", str(arguments)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert "error" in json.loads(missing.stdout.strip().splitlines()[-1]), "the tool's own failure, as data"
