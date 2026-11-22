# This file was generated with the assistance of an AI coding tool.
"""Browser tests: the real server in a thread, a real database in a container, Chromium through Playwright."""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi import FastAPI

from opencde.main import create_app
from opencde.settings import Settings
from tests.conftest import test_settings
from tests.integration import conftest as integration
from tests.integration.conftest import GraphTarget
from tests.integration.conftest_accounts import World, build_world

pytestmark = pytest.mark.ui

# The database container fixtures of the integration tests, re-registered here.
graph_target = integration.graph_target
settings = integration.settings
clean_database = integration.clean_database
store_kind = integration.store_kind
postgres_target = integration.postgres_target
pytest_generate_tests = integration.pytest_generate_tests  # both_stores


@dataclass(frozen=True)
class LiveServer:
    url: str
    world: World
    app: FastAPI


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextmanager
def running_server(
    graph_target: GraphTarget, tmp_path: Path, port: int | None = None, *, base: Settings | None = None
) -> Iterator[LiveServer]:
    """The server in a thread of its own on a free port. ``base`` is the integration tests' settings, with the store
    they chose (``both_stores``: the graph, or PostgreSQL without one); without it the graph holds it all."""
    port = port or _free_port()
    # several parts even for the small fixture model
    here = {"public_url": f"http://127.0.0.1:{port}", "upload_part_bytes": 4096}
    live_settings = (
        base.model_copy(update=here)
        if base is not None
        else test_settings(
            tmp_path,
            graph_dialect=graph_target.dialect,
            graph_uri=graph_target.uri,
            graph_user=graph_target.user,
            graph_password=graph_target.password,
            graph_connect_timeout_seconds=10.0,
            cleanup_interval_seconds=3600,
            **here,
        )
    )
    world = build_world(live_settings)
    app = create_app(live_settings, startup_wait_seconds=30)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, name="live-server", daemon=True)
    thread.start()
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"http://127.0.0.1:{port}/readyz", timeout=2.0).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    else:
        server.should_exit = True
        raise RuntimeError("the test server did not become ready")
    try:
        yield LiveServer(url=f"http://127.0.0.1:{port}", world=world, app=app)
    finally:
        server.should_exit = True
        thread.join(timeout=15)


@pytest.fixture
def live_server(
    graph_target: GraphTarget, settings: Settings, clean_database: None, tmp_path: Path
) -> Iterator[LiveServer]:
    with running_server(graph_target, tmp_path, base=settings) as server:
        yield server
