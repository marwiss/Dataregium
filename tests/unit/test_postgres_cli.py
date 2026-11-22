# This file was generated with the assistance of an AI coding tool.
"""The command line with the server's own data in PostgreSQL (ADR 0029), without a database: which database each
command opens and asks, over a fake PostgreSQL and a recording IFC graph."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from opencde import bootstrap
from opencde import seed as seeding
from opencde.admin import doctor
from opencde.cli import app
from opencde.postgres import cleanup, schema, tables
from opencde.store.accounts import postgres as accounts
from opencde.store.accounts.postgres import PostgresProjectRepository, PostgresUserRepository
from opencde.store.jobs import postgres as jobs
from opencde.store.jobs.postgres import PostgresConversionRepository
from opencde.store.oauth2 import postgres as oauth2
from opencde.store.oauth2.postgres import PostgresClientRepository, PostgresTokenRepository
from tests.conftest import test_settings as make_settings
from tests.unit.test_postgres_backup import SHIPPED, a_database
from tests.unit.test_postgres_store import FakePostgres
from tests.unit.test_store_split_cli import Graph

URL = "postgresql://cde@127.0.0.1:9/cde"


class Database(FakePostgres):
    """A fake PostgreSQL with what the command line asks of a connection besides its statements."""

    url = URL

    def __init__(self, rows: dict[str, list[dict[str, Any]]] | None = None) -> None:
        super().__init__(a_database(rows or {}).answer)
        self.closed = False

    def holding(self, rows: dict[str, list[dict[str, Any]]]) -> None:
        self.answer = a_database(rows).answer

    def ran(self, statement: str) -> bool:
        return any(query == statement for query, _ in self.statements)

    async def ping(self, seconds: float) -> bool:
        return True

    async def close(self) -> None:
        self.closed = True


@dataclass
class World:
    ifc: Graph
    database: Database
    enabled: bool  # IFC-DB
    waited: list[str] = field(default_factory=list)
    migrated: int = 0


@pytest.fixture(params=[False], ids=["without IFC-DB"])
def world(request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    """The settings name PostgreSQL; ``ifcgraph_enabled`` is the parameter."""
    world = World(Graph("ifc", [("RETURN count(n) AS n", [{"n": 0}])]), Database(), request.param)
    settings = make_settings(tmp_path, store_url=URL, ifcgraph_enabled=request.param)

    async def wait_for_graph(graph: Graph, seconds: float) -> None:
        world.waited.append(graph.name)

    async def nothing(*args: Any, **kwargs: Any) -> None:
        return None

    async def migrate(postgres: Any) -> int:
        world.migrated += 1
        return 0

    monkeypatch.setattr("opencde.cli._settings", lambda: settings)
    monkeypatch.setattr(bootstrap, "make_graph", lambda settings: world.ifc)
    monkeypatch.setattr(bootstrap, "make_store", lambda settings: None)
    monkeypatch.setattr(bootstrap, "make_postgres", lambda settings: world.database)
    monkeypatch.setattr(bootstrap, "wait_for_graph", wait_for_graph)
    monkeypatch.setattr(bootstrap, "apply_schema", nothing)
    monkeypatch.setattr(bootstrap, "wait_for_postgres", nothing)
    monkeypatch.setattr(bootstrap, "migrate", migrate)
    return world


@pytest.mark.parametrize(
    ("command", "statement", "said"),
    [
        (["user", "list"], accounts.USER_SEARCH, ""),
        (["project", "list"], accounts.PROJECT_ALL, ""),
        (["client", "list"], oauth2.CLIENT_ALL, ""),
        (["client", "prune"], oauth2.CLIENT_PRUNE, "deleted 0 self-registered"),
        (["cleanup"], cleanup.EXPIRED_WEB_SESSIONS, "deleted 0 expired rows"),
        (["revoke", "--all"], oauth2.REVOKE_ALL, "revoked 0 tokens and sessions"),
        (["ifc-db", "list"], jobs.CONVERSIONS_ALL, ""),
    ],
    ids=lambda value: " ".join(value) if isinstance(value, list) else "",
)
def test_a_command_over_the_servers_own_data_asks_postgresql_alone(
    world: World, command: list[str], statement: str, said: str
) -> None:
    done = CliRunner().invoke(app, command)
    assert done.exit_code == 0, done.output
    assert said in done.output
    assert world.database.ran(statement), [query for query, _ in world.database.statements]
    assert world.migrated == 1 and world.database.closed, "migrated as the server would, and closed"
    assert world.waited == [] and world.ifc.statements == [], "the graph is not waited for, nor asked"


@pytest.mark.parametrize("world", [True], ids=["with IFC-DB"], indirect=True)
def test_the_commands_of_the_models_connect_the_ifc_graph_beside_postgresql(world: World) -> None:
    done = CliRunner().invoke(app, ["ifc-db", "prune", "--project", "p1", "--all"])
    assert done.exit_code == 0, done.output
    assert "deleted 0 model(s)" in done.output
    assert world.waited == ["ifc"] and world.database.ran(jobs.CONVERSIONS_ALL)


@pytest.mark.parametrize("command", [["prune", "--project", "p1", "--all"], ["export", "m1", "out.ifc"]])
def test_the_commands_of_the_models_are_refused_at_once_without_a_graph(world: World, command: list[str]) -> None:
    """Tiers 1 and 2 (F58 a): PostgreSQL holds the server's own data and IFC-DB is off, so there are no models in
    a graph to prune or write back, and nothing waits for one."""
    done = CliRunner().invoke(app, ["ifc-db", *command])
    assert done.exit_code == 1 and "this server uses no graph" in done.output
    assert world.waited == [] and world.ifc.statements == [] and world.database.statements == []


@pytest.mark.parametrize("world", [False, True], ids=["without IFC-DB", "with IFC-DB"], indirect=True)
def test_the_seed_goes_to_postgresql_and_its_model_to_the_ifc_graph(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    given: list[tuple[str, Any, Any]] = []

    async def seed(graph: Any, *, postgres: Any = None, **kwargs: Any) -> seeding.SeedResult:
        given.append(("seed", graph, postgres))
        return seeding.SeedResult()

    async def build_sample_graph(graph: Any, storage: Any, settings: Any, result: Any, **where: Any) -> dict:
        given.append(("model", graph, where))
        return {"state": "done", "entities": 1}

    monkeypatch.setattr(seeding, "seed", seed)
    monkeypatch.setattr(seeding, "build_sample_graph", build_sample_graph)
    done = CliRunner().invoke(app, ["seed", "--reset", "--yes"])
    assert done.exit_code == 0, done.output
    assert given[0] == ("seed", world.ifc, world.database), "the stores' choosers are given PostgreSQL"
    assert world.database.ran(tables.WIPE), "a reset empties PostgreSQL"
    wiped = any("DETACH DELETE n" in statement for statement in world.ifc.statements)
    if world.enabled:
        assert given[1] == ("model", world.ifc, {"store": world.ifc, "postgres": world.database})
        assert wiped and world.waited == ["ifc"]
    else:
        assert given[1:] == [] and world.ifc.statements == [] and world.waited == [], "without IFC-DB no graph"


@pytest.mark.parametrize("world", [True], ids=["with IFC-DB"], indirect=True)
def test_the_seed_wants_the_ifc_graph_empty_too(world: World) -> None:
    world.ifc.answers = [("RETURN count(n) AS n", [{"n": 3}])]
    done = CliRunner().invoke(app, ["seed"])
    assert done.exit_code == 1 and "the IFC graph is not empty" in done.output


def test_backup_and_restore_of_postgresql_without_a_graph(world: World, tmp_path: Path) -> None:
    world.database.holding({"users": [{"id": "u1", "username": "anna"}]})
    done = CliRunner().invoke(app, ["backup", str(tmp_path / "backups")])
    assert done.exit_code == 0, done.output
    assert "1 rows of PostgreSQL, migration 0006" in done.output
    (folder,) = (tmp_path / "backups").iterdir()
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    assert (manifest["store_dialect"], manifest["rows"], manifest["nodes"], manifest["dialect"]) == (
        "postgres",
        1,
        0,
        "none",
    )
    assert world.ifc.statements == [] and world.migrated == 1

    done = CliRunner().invoke(app, ["restore", str(folder)])
    assert done.exit_code == 1 and "not empty; use --force" in done.output

    world.database.holding({})
    world.database.statements.clear()
    done = CliRunner().invoke(app, ["restore", str(folder)])
    assert done.exit_code == 0, done.output
    assert world.migrated == 1, "the session leaves the migrations to the restore"
    puts = [args for query, args in world.database.statements if query == tables.USERS_IN]
    assert puts == [([{"id": "u1", "username": "anna"}],)]
    assert world.ifc.statements == []

    world.database.holding({"users": [{"id": "u2"}]})
    done = CliRunner().invoke(app, ["restore", str(folder), "--force"])
    assert done.exit_code == 0, done.output
    assert world.database.ran(tables.WIPE)


def test_the_doctor_reports_postgresql_and_its_migrations(world: World) -> None:
    done = CliRunner().invoke(app, ["doctor"])
    assert done.exit_code == 0, done.output
    for line in (
        "OK      Graph database: not used: PostgreSQL holds the server's own data and IFC-DB is off",
        f"OK      The server's own data: PostgreSQL at {URL}",
        "OK      Schema of the server's own data: 6 of 6 migrations applied, the last 0006_logs",
    ):
        assert line in done.output, done.output
    assert world.ifc.statements == [] and world.database.closed


async def test_the_doctor_tells_a_silent_a_behind_and_a_newer_postgresql(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, store_url=URL)

    class Silent(Database):
        async def ping(self, seconds: float) -> bool:
            return False

    silent = await doctor.postgres_checks(settings, Silent())  # ty: ignore[invalid-argument-type]
    assert [(c.ok, c.detail_key) for c in silent] == [(False, "postgres_silent"), (False, "schema_no_database")]
    for ledger, ok, detail, words in (
        (SHIPPED[:4], False, "migrations_short", "4 of 6 migrations applied, the last 0004_bcf; start the server"),
        ([*SHIPPED, {"version": 7, "name": "0007_later"}], False, "migrations_newer", "know: 0007"),
    ):
        database = Database()
        database.answer = lambda query, args, ledger=ledger: (
            ledger if query == schema.APPLIED else {"present": True} if query == schema.LEDGER_PRESENT else None
        )
        checks = await doctor.postgres_checks(settings, database)  # ty: ignore[invalid-argument-type]
        assert (checks[1].ok, checks[1].detail_key) == (ok, detail) and words in checks[1].detail


async def test_the_store_session_chooses_the_repositories_of_postgresql(world: World, tmp_path: Path) -> None:
    async with bootstrap.store_session(make_settings(tmp_path, store_url=URL)) as store:
        assert store.postgres is world.database and store.graph is world.ifc
        assert isinstance(store.users, PostgresUserRepository)
        assert isinstance(store.projects, PostgresProjectRepository)
        assert isinstance(store.clients, PostgresClientRepository)
        assert isinstance(store.tokens, PostgresTokenRepository)
        assert isinstance(store.conversions, PostgresConversionRepository)
    assert world.database.closed and world.ifc.closed and world.waited == []
