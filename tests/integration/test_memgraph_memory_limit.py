# This file was generated with the assistance of an AI coding tool.
"""A Memgraph with a memory limit, as on a small server (PLAN Fas 6.3a, Hemsidor's D4): a model too large for the
memory left fails its import with a readable error and leaves nothing of itself, a statement over the limit is
refused at once instead of tried for half a minute, and ``/readyz`` says when the graph is full.

Its own Memgraph container, capped; run with the Memgraph suite (``OPENCDE_TEST_MEMGRAPH=1``)."""

from __future__ import annotations

import os
import re
import time
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from neo4j import Driver, GraphDatabase
from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import LogMessageWaitStrategy

from opencde.graph.driver import FULL_SHARE, size_in_bytes
from opencde.graph.graph import DatabaseFull
from opencde.main import create_app
from opencde.settings import GraphDialect, Settings
from tests.conftest import test_settings as make_settings  # aliased so pytest does not collect it as a test
from tests.integration.conftest import MEMGRAPH_IMAGE
from tests.integration.conftest_accounts import World, build_world, login_as
from tests.integration.helpers import Session
from tests.integration.test_ifc_queries import queries_url
from tests.integration.test_ifc_validation import MODEL, upload
from tests.integration.test_web_signin import web_sign_in

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("OPENCDE_TEST_MEMGRAPH") != "1", reason="with the Memgraph suite only"),
]
ifcopenshell_guid = pytest.importorskip("ifcopenshell.guid", reason="the ifc-db extra is not installed")

LIMIT_MIB = 320
HEADROOM_MIB = 60  # left for the upload, which needs about 3 KiB a product, so 30,000 products do not fit
PRODUCTS = 30_000


@pytest.fixture(scope="module")
def capped_uri() -> Iterator[str]:
    container = (
        DockerContainer(MEMGRAPH_IMAGE)
        .with_command(f"--memory-limit={LIMIT_MIB} --telemetry-enabled=false")
        .with_exposed_ports(7687)
        .waiting_for(LogMessageWaitStrategy(re.compile(r"You are running Memgraph")).with_startup_timeout(120))
    )
    with container:
        yield f"bolt://{container.get_container_host_ip()}:{container.get_exposed_port(7687)}"


@pytest.fixture
def capped(capped_uri: str, tmp_path: Any) -> Iterator[tuple[TestClient, World, Driver]]:
    with GraphDatabase.driver(capped_uri, auth=("", "")) as driver:
        driver.execute_query("MATCH (n) DETACH DELETE n")
        settings: Settings = make_settings(
            tmp_path,
            graph_dialect=GraphDialect.MEMGRAPH,
            graph_uri=capped_uri,
            graph_user="",
            graph_password="",
            graph_connect_timeout_seconds=10.0,
            cleanup_interval_seconds=3600,
            upload_part_bytes=8 * 1024 * 1024,
            ifcgraph_enabled=True,
            ifcgraph_timeout_seconds=300,
            ifcgraph_validate_uploads=False,
        )
        world = build_world(settings)
        with TestClient(create_app(settings, startup_wait_seconds=30)) as client:
            yield client, world, driver


def memory(driver: Driver) -> tuple[float, float]:
    """Memgraph's tracked memory and its limit, in MiB."""
    with driver.session() as session:
        figures = {row["storage info"]: row["value"] for row in session.run("SHOW STORAGE INFO")}
    used, limit = size_in_bytes(figures["memory_tracked"]), size_in_bytes(figures["memory_limit"])
    assert used is not None and limit is not None
    return used / 2**20, limit / 2**20


def fill_until(driver: Driver, free_mib: float, batch: int = 1_000) -> None:
    """Filler nodes of about a kilobyte each, auto-committed so that a refusal is not tried again, until less than
    ``free_mib`` is left under the limit."""
    while True:
        used, limit = memory(driver)
        if limit - used <= free_mib:
            return
        with driver.session() as session:
            try:
                session.run("UNWIND range(1, $n) AS i CREATE (:Filler {i: i, p: $p})", n=batch, p="x" * 1000).consume()
            except Exception:  # the limit came first; that is full enough
                return


def finished(client: TestClient, session: Session, version: dict[str, Any]) -> str:
    """The model's state once its import is over. While the graph is at its limit, reads are refused too (503)."""
    deadline = time.monotonic() + 240
    while True:
        response = client.get(queries_url(version), headers=session.headers)
        if response.status_code == 503:
            assert response.json()["code"] == "unavailable"
        else:
            assert response.status_code == 200, response.text
            if response.json()["model_state"] in ("done", "failed"):
                return response.json()["model_state"]
        assert time.monotonic() < deadline, "the import did not end in time"
        time.sleep(0.5)


def products(n: int) -> bytes:
    """The three walls and ``n`` building element proxies besides, each its own entity in the graph."""
    text = MODEL.read_text(encoding="utf-8")
    top = max(int(i) for i in re.findall(r"^#(\d+)\s*=", text, re.MULTILINE))
    extra = "".join(
        f"#{top + k}=IFCBUILDINGELEMENTPROXY('{ifcopenshell_guid.compress(uuid.uuid4().hex)}',#32,'P{k}',$,$,$,$,$,$);\n"
        for k in range(1, n + 1)
    )
    at = text.rindex("ENDSEC;")
    return (text[:at] + extra + text[at:]).encode()


def test_a_model_too_large_for_the_memory_left_fails_whole_and_readiness_sees_a_full_graph(
    capped: tuple[TestClient, World, Driver],
) -> None:
    client, world, driver = capped
    editor = login_as(client, world, "editor")
    web_sign_in(client, world, "editor")
    assert client.get("/readyz").json()["graph"] == "ok"

    fill_until(driver, HEADROOM_MIB)
    version = upload(client, world, editor, "proxies.ifc", products(PRODUCTS))
    assert finished(client, editor, version) == "failed"
    records, _, _ = driver.execute_query("MATCH (c:Conversion) RETURN c.error AS error, c.model_id AS model")
    (conversion,) = records
    assert conversion["error"] == "DatabaseFull: The database is full and takes no changes"
    left, _, _ = driver.execute_query("MATCH (e:Entity {model_id: $m}) RETURN count(e) AS n", m=conversion["model"])
    assert left[0]["n"] == 0, "the import left a part of the model behind"

    # A statement over the limit is refused at once: the driver would try a transient error again for 30 seconds.
    graph = client.app.state.graph  # type: ignore[attr-defined]
    started = time.monotonic()
    with pytest.raises(DatabaseFull):
        client.portal.call(
            lambda: graph.run("UNWIND range(1, 400000) AS i CREATE (:Filler {i: i, p: $p})", p="x" * 1000)
        )
    assert time.monotonic() - started < 15

    fill_until(driver, LIMIT_MIB * (1 - FULL_SHARE) / 2, batch=200)
    full = client.get("/readyz")
    assert (full.status_code, full.json()["graph"]) == (503, "full")
    with driver.session() as session:  # a full Memgraph deletes in small steps, each after a collection
        while True:
            session.run("FREE MEMORY").consume()
            deleted = session.run("MATCH (n:Filler) WITH n LIMIT 5000 DETACH DELETE n RETURN count(*) AS n").single()
            if deleted is None or deleted["n"] == 0:
                break
        session.run("FREE MEMORY").consume()
    assert client.get("/readyz").json() | {"dialect": "memgraph"} == {
        "status": "ready",
        "graph": "ok",
        "store": "shared",
        "dialect": "memgraph",
        "workcopy": "none",
    }
