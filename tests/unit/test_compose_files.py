# This file was generated with the assistance of an AI coding tool.
"""What the Compose files add up to, as Docker reads them; nothing is started."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOCKER = shutil.which("docker")

pytestmark = pytest.mark.skipif(DOCKER is None, reason="needs Docker's command line")

ENV = """\
OPENCDE_STORE_PASSWORD=a-password-for-this-test
OPENCDE_GRAPH_PASSWORD=another-password-for-this-test
OPENCDE_DOMAIN=cde.example.org
"""
GRAPH = "COMPOSE_PROFILES=graph\n"  # tier 3: IFC-DB's models in a graph beside PostgreSQL
STORE = "postgresql://opencde@store:5432/opencde"


def _config(tmp_path: Path, *overlays: str, extra_env: str = "") -> dict:
    # A copy of docker/ with a .env of its own beside it, as in a checkout: .env names the files.
    shutil.copytree(ROOT / "docker", tmp_path / "docker")
    names = ":".join(f"docker/{name}" for name in ("compose.yaml", *overlays))
    env = f"COMPOSE_PATH_SEPARATOR=:\nCOMPOSE_FILE={names}\n" + ENV + extra_env
    (tmp_path / ".env").write_text(env, encoding="utf-8")
    done = subprocess.run(  # noqa: S603  # the arguments are this test's own
        [str(DOCKER), "compose", "config", "--format", "json"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)["services"]


def test_the_stack_has_postgresql_and_the_graph_comes_with_its_profile(tmp_path: Path) -> None:
    """F73 a: PostgreSQL holds the server's own data in every stack, unpublished; the graph of tier 3 starts only with
    COMPOSE_PROFILES=graph, which a stack of tiers 1 and 2 never names."""
    services = _config(tmp_path / "postgres", "compose.prod.yaml")
    assert {"store", "api", "proxy", "backup"} <= set(services) and "graph" not in services
    assert services["store"]["image"].startswith("docker.io/library/postgres:") and "ports" not in services["store"]
    for name in ("api", "backup"):
        assert services[name]["environment"]["OPENCDE_STORE_URL"] == STORE
        assert services[name]["depends_on"]["store"]["condition"] == "service_healthy"
    services = _config(tmp_path / "graph", "compose.prod.yaml", extra_env=GRAPH)
    assert services["api"]["environment"]["OPENCDE_GRAPH_URI"] == "bolt://graph:7687"
    assert services["backup"]["environment"]["OPENCDE_GRAPH_URI"] == "bolt://graph:7687"
    assert services["api"]["depends_on"]["graph"]["condition"] == "service_healthy"
    assert "ports" not in services["graph"] and services["api"]["environment"]["OPENCDE_STORE_URL"] == STORE


def test_development_publishes_the_databases_on_the_loopback_only(tmp_path: Path) -> None:
    services = _config(tmp_path / "postgres", "compose.dev.yaml")
    (published,) = services["store"]["ports"]
    assert published["host_ip"] == "127.0.0.1" and published["published"] == "5442" and "graph" not in services
    services = _config(tmp_path / "graph", "compose.dev.yaml", extra_env=GRAPH)
    assert {port["host_ip"] for port in services["graph"]["ports"]} == {"127.0.0.1"}


def test_the_local_engine_publishes_the_api_on_the_loopback_only(tmp_path: Path) -> None:
    services = _config(tmp_path, "compose.local.yaml", extra_env=GRAPH)  # opencde init --local names the graph
    assert services["api"]["environment"]["OPENCDE_ENVIRONMENT"] == "local"
    assert services["api"]["environment"]["OPENCDE_PUBLIC_URL"] == "http://127.0.0.1:8000"
    (published,) = services["api"]["ports"]
    assert published["host_ip"] == "127.0.0.1" and published["published"] == "8000"
    assert "ports" not in services["graph"] and "ports" not in services["store"], "the databases are never published"


def test_the_engine_in_its_container_gets_the_stacks_own_database_addresses(tmp_path: Path) -> None:
    """F78 a: .env names the databases on the loopback, for the engine on the computer; inside Compose the stack's
    own addresses replace them."""
    loopback = (
        "OPENCDE_STORE_URL=postgresql://opencde@127.0.0.1:5442/opencde\nOPENCDE_GRAPH_URI=bolt://127.0.0.1:7697\n"
    )
    services = _config(tmp_path, "compose.local.yaml", extra_env=GRAPH + loopback)
    assert services["api"]["environment"]["OPENCDE_STORE_URL"] == STORE
    assert services["api"]["environment"]["OPENCDE_GRAPH_URI"] == "bolt://graph:7687"


def test_without_a_container_the_engine_finds_its_databases_on_the_loopback(tmp_path: Path) -> None:
    """F78 a: compose.local-databases.yaml runs PostgreSQL and the graph, published on the loopback only, and no
    API container, since the engine runs on the computer itself."""
    services = _config(tmp_path, "compose.local-databases.yaml", extra_env=GRAPH)
    assert "api" not in services and {"store", "graph"} <= set(services)
    (store,) = services["store"]["ports"]
    (bolt,) = services["graph"]["ports"]
    assert (store["host_ip"], store["published"], bolt["host_ip"], bolt["published"]) == (
        "127.0.0.1",
        "5442",
        "127.0.0.1",
        "7697",
    )


def test_the_working_copy_joins_the_private_network_unpublished(tmp_path: Path) -> None:
    services = _config(tmp_path, "compose.local.yaml", "compose.workcopy.yaml")
    assert "ports" not in services["workcopy"] and services["workcopy"]["image"].startswith("docker.io/memgraph/")
    assert services["api"]["environment"]["OPENCDE_WORKCOPY_GRAPH_URI"] == "bolt://workcopy:7687"
    assert services["api"]["depends_on"]["workcopy"]["condition"] == "service_healthy"


def test_a_small_server_caps_memgraph_and_limits_the_containers(tmp_path: Path) -> None:
    stack = ("compose.prod.yaml", "compose.memgraph.yaml")
    flags = _config(tmp_path / "memgraph", *stack, extra_env=GRAPH)["graph"]["command"]
    services = _config(tmp_path / "small", *stack, "compose.small.yaml", extra_env=GRAPH)
    assert services["graph"]["command"] == [
        "--memory-limit=1536" if flag == "--memory-limit=0" else flag for flag in flags
    ], "compose.memgraph.yaml's flags, with a cap"
    mib = 1024 * 1024
    assert int(services["graph"]["mem_limit"]) == 2048 * mib and int(services["api"]["mem_limit"]) == 1024 * mib


def test_an_external_database_leaves_the_container_out(tmp_path: Path) -> None:
    address = "neo4j+s://xxxx.databases.neo4j.io"
    services = _config(
        tmp_path,
        "compose.prod.yaml",
        "compose.external-db.yaml",
        extra_env=f"OPENCDE_STACK_GRAPH_URI={address}\n" + GRAPH,
    )
    assert "graph" not in services
    assert services["api"]["environment"]["OPENCDE_GRAPH_URI"] == address
    assert services["backup"]["environment"]["OPENCDE_GRAPH_URI"] == address
