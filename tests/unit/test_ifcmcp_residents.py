# This file was generated with the assistance of an AI coding tool.
"""A working copy's model held by a worker between an AI's calls (F210 c): read once, the edits written when the calls
pause, before a proposal and when the copy is let go, none beyond the most, and a worker that ends started again from
the file with an answer that says what may be lost."""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

ifcopenshell = pytest.importorskip("ifcopenshell", reason="the ifc-db extra is not installed")
pytest.importorskip("ifcmcp", reason="IfcMCP is not installed")

from opencde.ifcgraph import mcp_worker, residents  # noqa: E402
from opencde.ifcgraph.residents import CopyLost, Residents  # noqa: E402
from tests.conftest import FIXTURES_DIR  # noqa: E402

MODEL = FIXTURES_DIR / "three_walls_gtin.ifc"
WALL = ifcopenshell.open(str(MODEL)).by_type("IfcWall")[0].id()


def _copy(tmp_path: Path, name: str = "copy.ifc") -> Path:
    copy = tmp_path / name
    shutil.copyfile(MODEL, copy)
    return copy


def _asked(tool: str, arguments: dict[str, Any], *, write: bool = False) -> dict[str, Any]:
    return {"tool": tool, "arguments": arguments, "write": write, "max_step_id": 5000, "proposer": "anna",
            "budget": 200_000}  # fmt: skip


def _rename(name: str, product: int = WALL) -> dict[str, Any]:
    return {"function_path": "attribute.edit_attributes", "params": {"product": product, "attributes": {"Name": name}}}


def _name(path: Path) -> str:
    return str(ifcopenshell.open(str(path)).by_id(WALL).Name)


def test_the_held_model_writes_an_edit_after_its_answer_and_forgets_one_that_failed(tmp_path: Path) -> None:
    copy = _copy(tmp_path)
    held = mcp_worker.Held(copy)
    mcp_worker.run(copy, "ifc_edit", _rename("Held"), write=True, max_step_id=5000, proposer="anna", held=held)
    assert held.unwritten and _name(copy) != "Held", "written after the answer, not before"
    held.write_back()
    assert not held.unwritten and _name(copy) == "Held"
    model = held.model
    info = mcp_worker.run(copy, "ifc_info", {"element_id": WALL}, write=False, max_step_id=0, proposer="", held=held)
    assert "Held" in json.dumps(info, default=str) and held.model is model, "read from the model held"
    failed = mcp_worker.run(copy, "ifc_edit", _rename("Lost", 999_999), write=True, max_step_id=5000,
                            proposer="anna", held=held)  # fmt: skip
    assert isinstance(failed, dict) and failed.get("ok") is False, failed
    assert held.model is None and not held.unwritten, "the next call opens the file again"


def test_only_a_large_copy_is_held_and_none_when_off(tmp_path: Path) -> None:
    copy = _copy(tmp_path)
    assert Residents(most=1, least_bytes=0, memory_mb=4096, timeout=60).wants(copy)
    assert not Residents(most=1, least_bytes=copy.stat().st_size + 1, memory_mb=4096, timeout=60).wants(copy)
    assert not Residents(most=0, least_bytes=0, memory_mb=4096, timeout=60).wants(copy)
    assert not Residents(most=1, least_bytes=0, memory_mb=4096, timeout=60).wants(tmp_path / "gone.ifc")


async def test_a_copy_keeps_its_worker_and_one_that_ends_starts_again(tmp_path: Path) -> None:
    copy, other = _copy(tmp_path), _copy(tmp_path, "other.ifc")
    held, starting = Residents(most=1, least_bytes=0, memory_mb=4096, timeout=120), asyncio.Semaphore(1)
    try:
        walls = await held.call(copy, _asked("ifc_select", {"query": "IfcWall"}), starting)
        assert walls is not None and len(walls["result"]) == 3 and held.holds(copy)
        process = held._held[str(copy)].process
        edited = await held.call(copy, _asked("ifc_edit", _rename("Held"), write=True), starting)
        assert edited is not None and "error" not in edited, edited
        info = await held.call(copy, _asked("ifc_info", {"element_id": WALL}), starting)
        assert "Held" in json.dumps(info) and held._held[str(copy)].process is process, "the same worker"
        assert _name(copy) != "Held", "in memory until the calls pause"
        async with held.settled(copy):
            assert _name(copy) == "Held", "on the file once settled"
        assert await held.call(other, _asked("ifc_summary", {}), starting) is None, "no room: a worker per call"
        refused = await held.call(copy, _asked("ifc_edit", _rename("No")), starting)
        assert refused is not None and refused.get("refused") is True, "an edit needs write, as per call"

        await held.call(copy, _asked("ifc_edit", _rename("Unwritten"), write=True), starting)
        process.kill()
        await process.wait()
        with pytest.raises(CopyLost, match="may be lost"):
            await held.call(copy, _asked("ifc_summary", {}), starting)
        assert not held.holds(copy)
        again = await held.call(copy, _asked("ifc_info", {"element_id": WALL}), starting)
        assert again is not None and "Held" in json.dumps(again), "opened again from the file, as last written"
        assert "Unwritten" not in json.dumps(again)
        process = held._held[str(copy)].process
        process.kill()
        await process.wait()
        with pytest.raises(CopyLost, match="opens the copy again"):
            await held.call(copy, _asked("ifc_summary", {}), starting)
    finally:
        await held.close()
    assert not held.holds(copy)


async def test_the_edits_are_written_when_the_calls_pause_and_when_a_copy_is_let_go(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    copy, other, third = _copy(tmp_path), _copy(tmp_path, "other.ifc"), _copy(tmp_path, "third.ifc")
    held, starting = Residents(most=3, least_bytes=0, memory_mb=4096, timeout=120), asyncio.Semaphore(1)
    monkeypatch.setattr(residents, "QUIET_SECONDS", 0.5)
    try:
        await held.call(third, _asked("ifc_edit", _rename("Quiet"), write=True), starting)
        for _ in range(120):
            if _name(third) == "Quiet":
                break
            await asyncio.sleep(0.5)
        assert _name(third) == "Quiet", "written once the calls paused"
        await held.call(third, _asked("ifc_edit", _rename("Never"), write=True), starting)
        ended = held._held[str(third)].process
        ended.kill()
        await ended.wait()
        for _ in range(120):
            if not held.holds(third):
                break
            await asyncio.sleep(0.5)
        with pytest.raises(CopyLost, match="may be lost"):
            await held.call(third, _asked("ifc_summary", {}), starting)  # found by the write in the background
        assert _name(third) == "Quiet"
        await held.call(copy, _asked("ifc_edit", _rename("Last"), write=True), starting)
        await held.call(other, _asked("ifc_summary", {}), starting)
        process = held._held[str(copy)].process
        await held.drop(other)
        assert not held.holds(other) and held.holds(copy)
        monkeypatch.setattr(residents, "IDLE_SECONDS", 0)
        await held.reap()
        assert not held.holds(copy) and not held.holds(third)
        assert await asyncio.wait_for(process.wait(), timeout=60) == 0, "it ended as stdin closed"
        assert _name(copy) == "Last"
    finally:
        await held.close()
