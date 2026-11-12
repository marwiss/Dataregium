# This file was generated with the assistance of an AI coding tool.
"""A graph database without memory left (PLAN Fas 6.3a, Hemsidors D4): Memgraph at its ``--memory-limit`` answers a
change with a transient error, which the driver would try again for half a minute. The server refuses it at once
with 503, and ``/readyz`` says that the graph is full."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from neo4j.exceptions import Neo4jError

from opencde import health
from opencde.graph import driver as driver_module
from opencde.graph.dialect import MEMGRAPH, NEO4J
from opencde.graph.graph import DatabaseFull, Graph
from opencde.ifcgraph import maintenance
from tests.conftest import test_settings as make_settings  # aliased so pytest does not collect it as a test

MEMGRAPH_FULL = "Memgraph.TransientError.MemgraphError.MemgraphError"
FULL_MESSAGE = "Memory limit exceeded! Current use is 200.01MiB, while the maximum allowed size is set to 200.00MiB."


def refusal(code: str, message: str) -> Neo4jError:
    return Neo4jError._hydrate_neo4j(code=code, message=message)


class Tx:
    def __init__(self, error: Exception | None) -> None:
        self.error = error

    async def run(self, query: str, params: dict[str, Any]) -> Any:
        if self.error is not None:
            raise self.error
        return self

    def __aiter__(self) -> Any:
        return self

    async def __anext__(self) -> Any:
        raise StopAsyncIteration


class Session:
    """Calls the transaction function as the driver does, tries it again as the driver would, and raises the last
    error when the tries are spent; ``at_commit`` is an error the commit raises after the function."""

    def __init__(self, driver: Driver) -> None:
        self.driver = driver

    async def __aenter__(self) -> Session:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def execute_write(self, work: Any) -> Any:
        for attempt in range(3):
            self.driver.attempts += 1
            try:
                done = await work(Tx(self.driver.error))
                if self.driver.at_commit is not None:
                    raise self.driver.at_commit
                return done
            except Neo4jError as exc:
                if not exc.is_retryable() or attempt == 2:
                    raise

    execute_read = execute_write

    async def run(self, statement: str) -> Session:
        self.driver.statements.append(statement)
        return self

    async def consume(self) -> None:
        return None


class Driver:
    def __init__(self, error: Exception | None, at_commit: Exception | None = None) -> None:
        self.error = error
        self.at_commit = at_commit
        self.attempts = 0
        self.execute_query_bookmark_manager = object()
        self.sessions: list[dict[str, Any]] = []
        self.statements: list[str] = []

    def session(self, **options: Any) -> Session:
        self.sessions.append(options)
        return Session(self)


NEO4J_FULL = "Neo.TransientError.General.MemoryPoolOutOfMemoryError"


@pytest.mark.parametrize(
    ("dialect", "code", "message", "attempts"),
    [(MEMGRAPH, MEMGRAPH_FULL, FULL_MESSAGE, 1), (NEO4J, NEO4J_FULL, "The allocation of an extra 2.0 MiB ...", 3)],
)
async def test_a_full_database_is_refused_with_its_own_error(
    dialect: Any, code: str, message: str, attempts: int
) -> None:
    """Memgraph at once; Neo4j after the driver's tries, since its pool frees as other transactions end."""
    for call in ("run", "write", "read"):
        driver = Driver(refusal(code, message))
        graph = Graph(driver, "db", dialect)  # type: ignore[arg-type]
        with pytest.raises(DatabaseFull) as raised:
            if call == "run":
                await graph.run("CREATE (n:Probe)")
            else:
                await getattr(graph, call)(lambda tx: tx.run("CREATE (n:Probe)"))
        assert driver.attempts == attempts, call
        assert str(raised.value) == "The database is full and takes no changes"
        assert driver.statements == (["FREE MEMORY"] if dialect is MEMGRAPH else []), "the memory is given back"


async def test_a_refusal_at_the_commit_is_the_same_refusal() -> None:
    driver = Driver(None, at_commit=refusal(MEMGRAPH_FULL, FULL_MESSAGE))
    with pytest.raises(DatabaseFull):
        await Graph(driver, "db", MEMGRAPH).write(lambda tx: tx.run("CREATE (n:Probe)"))  # type: ignore[arg-type]
    assert driver.statements == ["FREE MEMORY"]


@pytest.mark.parametrize(
    ("code", "message"),
    [
        ("Neo.TransientError.Transaction.DeadlockDetected", "deadlock"),
        ("Neo.ClientError.Schema.ConstraintValidationFailed", "Node already exists with name = 'OutOfMemoryError'"),
        ("Memgraph.ClientError.MemgraphError.MemgraphError", "Memory limit exceeded! quoted from a value"),
        ("Memgraph.TransientError.MemgraphError.MemgraphError", "Cannot get unique access: Memory limit exceeded!"),
    ],
)
async def test_other_errors_stay_what_they_are(code: str, message: str) -> None:
    """Retried as before when transient, and never full because a value or a message quotes the words."""
    for dialect in (NEO4J, MEMGRAPH):
        driver = Driver(refusal(code, message))
        with pytest.raises(Neo4jError):
            await Graph(driver, "db", dialect).run("MATCH (n) RETURN n")  # type: ignore[arg-type]
        assert driver.attempts == (3 if "TransientError" in code else 1)
        assert driver.statements == []


async def test_a_burst_of_refusals_asks_for_one_collection() -> None:
    driver = Driver(refusal(MEMGRAPH_FULL, FULL_MESSAGE))
    graph = Graph(driver, "db", MEMGRAPH)  # type: ignore[arg-type]
    for _ in range(5):
        with pytest.raises(DatabaseFull):
            await graph.run("CREATE (n:Probe)")
    assert driver.statements == ["FREE MEMORY"], "at most one per interval"


async def test_a_statement_keeps_the_bookmarks_execute_query_had() -> None:
    driver = Driver(None)
    assert await Graph(driver, "db", NEO4J).run("RETURN 1") == []  # type: ignore[arg-type]
    assert driver.sessions == [{"database": "db", "bookmark_manager": driver.execute_query_bookmark_manager}]


async def test_a_partial_model_is_removed_in_rounds(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def delete_model(graph: object, model_id: str, *, keep_model: bool = False) -> None:
        calls.append(model_id)
        if len(calls) < 3:
            raise DatabaseFull

    async def pause(seconds: float) -> None:
        return None

    monkeypatch.setattr(maintenance, "delete_model", delete_model)
    monkeypatch.setattr(maintenance.asyncio, "sleep", pause)
    assert await maintenance.drop_partial(object(), "m1") is True  # type: ignore[arg-type]
    assert calls == ["m1"] * 3
    calls.clear()

    async def broken(graph: object, model_id: str, *, keep_model: bool = False) -> None:
        calls.append(model_id)
        raise RuntimeError("no")

    monkeypatch.setattr(maintenance, "delete_model", broken)
    assert await maintenance.drop_partial(object(), "m2") is False, "another failure is left, once"  # type: ignore[arg-type]
    assert calls == ["m2"]


class PingDriver:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def session(self, **options: Any) -> PingDriver:
        return self

    async def __aenter__(self) -> PingDriver:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def run(self, statement: str) -> Any:
        raise self.error


async def test_a_graph_that_refuses_for_want_of_memory_still_answers_the_ping() -> None:
    assert await driver_module.ping(PingDriver(refusal(MEMGRAPH_FULL, FULL_MESSAGE)), "db") is True  # type: ignore[arg-type]
    assert await driver_module.ping(PingDriver(OSError("no route")), "db") is False  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("text", "size"),
    [("195.68MiB", 195.68 * 2**20), ("0B", 0.0), ("2.00GiB", 2.0 * 2**30), ("20KiB", 20.0 * 2**10)],
)
def test_memgraph_sizes_are_read_in_bytes(text: str, size: float) -> None:
    assert driver_module.size_in_bytes(text) == pytest.approx(size)


@pytest.mark.parametrize("text", ["", "unlimited", "-1MiB", "12 MB", None, 5])
def test_anything_but_a_size_is_no_size(text: object) -> None:
    assert driver_module.size_in_bytes(text) is None


class InfoGraph:
    def __init__(self, dialect: Any, rows: list[dict[str, Any]] | Exception) -> None:
        self.dialect = dialect
        self.rows = rows

    async def schema_rows(self, statement: str) -> list[dict[str, Any]]:
        assert statement == "SHOW STORAGE INFO"
        if isinstance(self.rows, Exception):
            raise self.rows
        return self.rows


def info(tracked: str, limit: str) -> list[dict[str, Any]]:
    return [
        {"storage info": "memory_res", "value": "270.60MiB"},
        {"storage info": "memory_tracked", "value": tracked},
        {"storage info": "memory_limit", "value": limit},
    ]


@pytest.mark.parametrize(
    ("graph", "full"),
    [
        (InfoGraph(MEMGRAPH, info("195.68MiB", "200.00MiB")), True),
        (InfoGraph(MEMGRAPH, info("100.00MiB", "200.00MiB")), False),
        (InfoGraph(MEMGRAPH, info("1.90GiB", "2.00GiB")), True),
        (InfoGraph(MEMGRAPH, info("100.00MiB", "0B")), False),
        (InfoGraph(MEMGRAPH, [{"storage info": "memory_tracked", "value": "1.00GiB"}]), False),
        (InfoGraph(MEMGRAPH, RuntimeError("no answer")), False),
        (InfoGraph(MEMGRAPH, refusal(MEMGRAPH_FULL, FULL_MESSAGE)), True),
        (InfoGraph(NEO4J, RuntimeError("never asked")), False),
    ],
)
async def test_a_memgraph_is_full_from_95_percent_of_its_limit(graph: InfoGraph, full: bool) -> None:
    assert await driver_module.full_within(graph, 1.0) is full  # type: ignore[arg-type]


def test_readiness_says_that_a_graph_is_full(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def answers(driver: object, database: str, seconds: float) -> bool:
        return True

    async def full(graph: Any, seconds: float) -> bool:
        return graph.driver == "ifc"

    monkeypatch.setattr(health, "ping_within", answers)
    monkeypatch.setattr(health, "full_within", full)
    ready = FastAPI()
    ready.include_router(health.router)
    ready.state.settings = make_settings(tmp_path)
    ready.state.graph = SimpleNamespace(driver="ifc", database="memgraph", dialect=MEMGRAPH)
    ready.state.workcopy = None
    response = TestClient(ready).get("/readyz")
    assert (response.status_code, response.json()) == (503, {"status": "not_ready", "graph": "full", "store": "shared"})
