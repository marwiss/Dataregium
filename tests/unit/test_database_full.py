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
    """Calls the transaction function as the driver does, and tries it again as the driver would."""

    def __init__(self, driver: Driver) -> None:
        self.driver = driver

    async def __aenter__(self) -> Session:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def execute_write(self, work: Any) -> Any:
        for _ in range(3):
            self.driver.attempts += 1
            try:
                return await work(Tx(self.driver.error))
            except Neo4jError as exc:
                if not exc.is_retryable():
                    raise
        raise RuntimeError("retried in vain")

    execute_read = execute_write

    async def run(self, statement: str) -> Session:
        self.driver.statements.append(statement)
        return self

    async def consume(self) -> None:
        return None


class Driver:
    def __init__(self, error: Exception | None) -> None:
        self.error = error
        self.attempts = 0
        self.execute_query_bookmark_manager = object()
        self.sessions: list[dict[str, Any]] = []
        self.statements: list[str] = []

    def session(self, **options: Any) -> Session:
        self.sessions.append(options)
        return Session(self)


@pytest.mark.parametrize(
    ("dialect", "code", "message"),
    [
        (MEMGRAPH, MEMGRAPH_FULL, FULL_MESSAGE),
        (NEO4J, "Neo.TransientError.General.MemoryPoolOutOfMemoryError", "The allocation of an extra 2.0 MiB ..."),
    ],
)
async def test_a_full_database_is_refused_at_once_and_not_tried_again(dialect: Any, code: str, message: str) -> None:
    for call in ("run", "write", "read"):
        driver = Driver(refusal(code, message))
        graph = Graph(driver, "db", dialect)  # type: ignore[arg-type]
        with pytest.raises(DatabaseFull) as raised:
            if call == "run":
                await graph.run("CREATE (n:Probe)")
            else:
                await getattr(graph, call)(lambda tx: tx.run("CREATE (n:Probe)"))
        assert driver.attempts == 1, f"{call} was tried again"
        assert str(raised.value) == "The database is full and takes no changes"
        assert driver.statements == (["FREE MEMORY"] if dialect is MEMGRAPH else []), "the memory is given back"


async def test_other_transient_errors_are_still_tried_again() -> None:
    driver = Driver(refusal("Neo.TransientError.Transaction.DeadlockDetected", "deadlock"))
    with pytest.raises(RuntimeError, match="in vain"):
        await Graph(driver, "db", NEO4J).run("MATCH (n) RETURN n")  # type: ignore[arg-type]
    assert driver.attempts == 3


async def test_a_statement_keeps_the_bookmarks_execute_query_had() -> None:
    driver = Driver(None)
    assert await Graph(driver, "db", NEO4J).run("RETURN 1") == []  # type: ignore[arg-type]
    assert driver.sessions == [{"database": "db", "bookmark_manager": driver.execute_query_bookmark_manager}]


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
