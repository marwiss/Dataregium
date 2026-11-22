# This file was generated with the assistance of an AI coding tool.
"""What Dataregium relies on from IFC-DB, the library its IFC graph comes from (ADR 0014).

IFC-DB's own tests cover the round trip. These cover how Dataregium uses it: the graph side in the server's process
without IfcOpenShell, the workers with the extra, and buildingSMART's rules without the server's settings.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _project() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]


def test_the_graph_side_is_always_installed_and_the_workers_come_with_the_extra() -> None:
    project = _project()
    assert any(requirement.startswith("ifc-db>=") for requirement in project["dependencies"])
    assert project["optional-dependencies"]["ifc-db"] == [
        "ifc-db[ifcopenshell]>=1.0.0",
        "ifcclash>=0.9.0",
        "shapely>=2.0",
    ], "the rooms need shapely 2, which IfcOpenShell brings without a floor"
    for old in ("ifc-lpg", "ifcgraph"):
        assert project["optional-dependencies"][old] == ["opencde-server[ifc-db]"], "builds that name it keep working"
    assert 'ARG OPENCDE_EXTRAS="--extra postgres --extra ifc-db"' in (ROOT / "docker" / "Dockerfile").read_text(
        encoding="utf-8"
    ), "the image has the workers, and asyncpg for the server's own data in PostgreSQL (ADR 0029)"


def test_the_server_process_needs_no_ifcopenshell() -> None:
    """The modules Dataregium imports in its own process; the slim image has no IfcOpenShell."""
    import ifc_db.exporter
    import ifc_db.importer
    import ifc_db.validation

    assert callable(ifc_db.importer.delete_model) and callable(ifc_db.exporter.export_records)
    assert ifc_db.validation.unpack_report(ifc_db.validation.pack_report([{"kind": "x"}])) == [{"kind": "x"}]


def test_the_rules_see_none_of_the_servers_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    from ifc_db import gherkin

    monkeypatch.setenv("OPENCDE_SECRET_KEY", "not for the rules")
    monkeypatch.setenv("OPENCDE_GRAPH_PASSWORD", "nor this")
    assert not [key for key in gherkin._clean_environment() if key.startswith("OPENCDE_")]


def test_the_lock_holds_the_rev_the_project_names() -> None:
    """CI and the image sync with uv.lock as it is (UV_FROZEN, uv sync --frozen), so a rev moved in pyproject.toml
    alone would keep the old IFC-DB without a word."""
    sources = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["uv"]["sources"]
    rev = sources["ifc-db"]["rev"]
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))["package"]
    (library,) = [package for package in lock if package["name"] == "ifc-db"]
    named, commit = library["source"]["git"].split("#", 1)
    assert named.endswith(f"?rev={rev}") and commit.startswith(rev), "uv lock after moving the rev"
    (server,) = [package for package in lock if package["name"] == "opencde-server"]
    wanted = [entry for entry in server["metadata"]["requires-dist"] if entry["name"] == "ifc-db"]
    assert wanted and all(entry["git"].endswith(f"?rev={rev}") for entry in wanted)
