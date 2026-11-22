# This file was generated with the assistance of an AI coding tool.
"""`opencde init`: the two direct ways, the guide that asks, and what it says comes next; PostgreSQL for the
server's own data, and the graph only in tier 3 (F58 a, F73 a, OB-46 b)."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
from typer.testing import CliRunner

from opencde import cli
from opencde.cli import app


@pytest.fixture
def answers(monkeypatch: pytest.MonkeyPatch) -> None:
    """A terminal someone answers in, which the test runner's input is not."""
    monkeypatch.setattr(cli, "_interactive", lambda: True)


def _env(path: Path) -> dict[str, str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return dict(line.split("=", 1) for line in lines if "=" in line and not line.startswith("#"))


def test_the_direct_ways_ask_nothing(tmp_path: Path) -> None:
    development = tmp_path / "development.env"
    assert CliRunner().invoke(app, ["init", "--dev", "--path", str(development)]).exit_code == 0
    assert _env(development)["OPENCDE_ENVIRONMENT"] == "development"
    production = tmp_path / "production.env"
    done = CliRunner().invoke(app, ["init", "--public-url", "https://cde.example.org", "--path", str(production)])
    assert done.exit_code == 0
    assert _env(production)["OPENCDE_DOMAIN"] == "cde.example.org"


def test_postgresql_holds_the_servers_own_data_and_the_graph_comes_with_tier_3(tmp_path: Path) -> None:
    """Tier 2 unless asked: PostgreSQL alone, on the stack's port for the host in development, with a password of its
    own and no graph; tier 3 adds the graph for IFC-DB's models and its Compose profile."""
    from opencde.settings import Settings, Tier

    development = tmp_path / "development.env"
    assert CliRunner().invoke(app, ["init", "--dev", "--path", str(development)]).exit_code == 0
    env = _env(development)
    assert (
        env["OPENCDE_TIER"] == "bim-gis" and env["OPENCDE_STORE_URL"] == "postgresql://opencde@127.0.0.1:5442/opencde"
    )
    assert len(env["OPENCDE_STORE_PASSWORD"]) >= 24 and env["OPENCDE_DEV_POSTGRES_PORT"] == "5442"
    assert not [name for name in env if name.startswith(("OPENCDE_GRAPH_", "NEO4J_", "COMPOSE_PROFILES"))]
    settings = Settings(_env_file=development)  # type: ignore[call-arg]
    assert settings.tier is Tier.BIM_GIS and not settings.uses_graph
    assert env["OPENCDE_BIM_ENABLED"] == "true" and settings.bim_on, "the BIM functions on the files (F59)"
    production = tmp_path / "production.env"
    command = ["init", "--public-url", "https://cde.example.org", "--tier", "experimental", "--path", str(production)]
    assert CliRunner().invoke(app, command).exit_code == 0
    env = _env(production)
    assert env["OPENCDE_STORE_URL"] == "postgresql://opencde@store:5432/opencde" and env["COMPOSE_PROFILES"] == "graph"
    assert env["OPENCDE_IFC_DB_ENABLED"] == "true" and env["OPENCDE_GRAPH_URI"] == "bolt://graph:7687"
    assert len(env["OPENCDE_GRAPH_PASSWORD"]) >= 24 and "OPENCDE_DEV_PORT" not in env
    settings = Settings(_env_file=production)  # type: ignore[call-arg]
    assert settings.is_production and settings.tier is Tier.EXPERIMENTAL and settings.uses_graph
    documents = tmp_path / "documents.env"
    assert CliRunner().invoke(app, ["init", "--dev", "--tier", "documents", "--path", str(documents)]).exit_code == 0
    assert Settings(_env_file=documents).apis == ["documents"]  # type: ignore[call-arg]
    refused = CliRunner().invoke(app, ["init", "--local", "--tier", "bim-gis", "--path", str(tmp_path / "l.env")])
    assert refused.exit_code != 0 and "tier 3" in refused.output


def test_the_local_engine_gets_random_passwords_and_named_origins(tmp_path: Path) -> None:
    local = tmp_path / "local.env"
    done = CliRunner().invoke(app, ["init", "--local", "--path", str(local)])
    assert done.exit_code == 0, done.output
    env = _env(local)
    assert env["OPENCDE_ENVIRONMENT"] == "local" and env["OPENCDE_PUBLIC_URL"] == "http://127.0.0.1:8000"
    assert len(env["OPENCDE_GRAPH_PASSWORD"]) >= 24 and len(env["OPENCDE_SECRET_KEY"]) >= 48
    assert "*" not in env["OPENCDE_CORS_ORIGINS"] and "null" in env["OPENCDE_CORS_ORIGINS"]
    assert env["COMPOSE_FILE"] == "docker/compose.yaml:docker/compose.local.yaml"
    assert env["OPENCDE_MCP_ENABLED"] == "true", "AI programs on the computer reach the models (ADR 0028)"
    assert env["OPENCDE_TIER"] == "experimental" and env["COMPOSE_PROFILES"] == "graph", "the models in the graph"
    assert env["OPENCDE_STORE_URL"] == "postgresql://opencde@127.0.0.1:5442/opencde", "Compose gives its own inside"
    assert env["OPENCDE_GRAPH_URI"] == "bolt://127.0.0.1:7697"
    assert len(env["OPENCDE_STORE_PASSWORD"]) >= 24 and env["OPENCDE_STORE_PASSWORD"] != env["OPENCDE_GRAPH_PASSWORD"]
    assert "Dataspatium" in done.output


def test_the_local_engine_runs_on_the_computer_with_its_databases_in_compose(tmp_path: Path) -> None:
    """F78 a: without a container the engine reaches its databases on the loopback, where the overlay publishes
    them, and the API's container stays off."""
    from opencde.settings import Settings

    local = tmp_path / "local.env"
    done = CliRunner().invoke(app, ["init", "--local", "--no-container", "--path", str(local)])
    assert done.exit_code == 0, done.output
    env = _env(local)
    assert env["COMPOSE_FILE"] == "docker/compose.yaml:docker/compose.local-databases.yaml"
    assert (env["OPENCDE_LOCAL_POSTGRES_PORT"], env["OPENCDE_LOCAL_BOLT_PORT"]) == ("5442", "7697")
    assert env["OPENCDE_STORE_URL"] == "postgresql://opencde@127.0.0.1:5442/opencde"
    assert env["OPENCDE_ENVIRONMENT"] == "local" and "uv run opencde serve" in done.output
    assert "uv sync --extra postgres --extra ifc-db" in done.output, "the engine on the computer needs both"
    assert Settings(_env_file=local).is_local  # type: ignore[call-arg]
    refused = CliRunner().invoke(app, ["init", "--dev", "--no-container", "--path", str(tmp_path / "d.env")])
    assert refused.exit_code != 0 and "--local" in refused.output


@pytest.mark.skipif(os.name == "nt", reason="Windows keeps a profile's files private by its ACL, not by modes")
def test_the_env_with_the_passwords_is_its_owners_alone(tmp_path: Path) -> None:
    """The .env holds the databases' passwords and the secret key, and the local engine without a container
    publishes its databases on the loopback (F78 a): init writes the file readable by its owner alone, also when
    --force overwrites one that others could read."""
    local = tmp_path / "local.env"
    local.write_text("OLD=1\n", encoding="utf-8")
    local.chmod(0o644)
    done = CliRunner().invoke(app, ["init", "--local", "--no-container", "--force", "--path", str(local)])
    assert done.exit_code == 0, done.output
    assert stat.S_IMODE(local.stat().st_mode) == 0o600
    development = tmp_path / "development.env"
    assert CliRunner().invoke(app, ["init", "--dev", "--path", str(development)]).exit_code == 0
    assert stat.S_IMODE(development.stat().st_mode) == 0o600


def test_without_a_terminal_it_says_what_to_give(tmp_path: Path) -> None:
    done = CliRunner().invoke(app, ["init", "--path", str(tmp_path / ".env")])
    assert done.exit_code != 0 and "--public-url" in done.output and not (tmp_path / ".env").exists()


def test_an_existing_file_stays(tmp_path: Path, answers: None) -> None:
    path = tmp_path / ".env"
    path.write_text("OPENCDE_SECRET_KEY=keep-this\n", encoding="utf-8")
    done = CliRunner().invoke(app, ["init", "--path", str(path)], input="n\ny\n")
    assert done.exit_code != 0 and "--force" in done.output
    assert path.read_text(encoding="utf-8") == "OPENCDE_SECRET_KEY=keep-this\n"


def test_the_guide_for_this_machine(tmp_path: Path, answers: None) -> None:
    path = tmp_path / ".env"
    done = CliRunner().invoke(app, ["init", "--path", str(path)], input="n\ny\n")
    assert done.exit_code == 0, done.output
    env = _env(path)
    assert env["OPENCDE_ENVIRONMENT"] == "development"
    assert env["OPENCDE_IFC_DB_ENABLED"] == "true" and env["OPENCDE_REQUEST_LOG_ENABLED"] == "true"
    assert env["OPENCDE_DYNAMIC_CLIENT_REGISTRATION"] == "true"
    assert len([line for line in path.read_text().splitlines() if "DYNAMIC_CLIENT_REGISTRATION" in line]) == 1
    assert env["COMPOSE_FILE"] == "docker/compose.yaml:docker/compose.dev.yaml"
    assert env["OPENCDE_TIER"] == "experimental" and env["COMPOSE_PROFILES"] == "graph", "IFC-DB is tier 3"
    assert len([line for line in path.read_text().splitlines() if "IFC_DB_ENABLED" in line]) == 1
    assert "docker compose up -d --build --wait" in done.output and "opencde seed" in done.output


def test_the_guide_for_a_server_others_use(tmp_path: Path, answers: None) -> None:
    path = tmp_path / ".env"
    typed = [
        "y",
        "http://cde.example.org",
        "https://cde.example.org/path",
        "https://cde.example.org/",
        "me@example.org",
        "n",
        "y",
    ]
    done = CliRunner().invoke(app, ["init", "--path", str(path)], input="\n".join(typed) + "\n")
    assert done.exit_code == 0, done.output
    assert done.output.count("It has to be https://") == 2
    env = _env(path)
    assert env["OPENCDE_ENVIRONMENT"] == "production"
    assert env["OPENCDE_PUBLIC_URL"] == "https://cde.example.org" and env["OPENCDE_DOMAIN"] == "cde.example.org"
    assert env["OPENCDE_ACME_EMAIL"] == "me@example.org"
    assert env["OPENCDE_DYNAMIC_CLIENT_REGISTRATION"] == "false" and env["OPENCDE_IFC_DB_ENABLED"] == "true"
    assert env["COMPOSE_FILE"] == "docker/compose.yaml:docker/compose.prod.yaml"
    for step in (
        "cde.example.org points at this machine",
        "docker compose up -d",
        "user create admin --admin",
        "client create",
    ):
        assert step in done.output
    assert env["OPENCDE_SECRET_KEY"] not in done.output and env["OPENCDE_GRAPH_PASSWORD"] not in done.output
    assert env["OPENCDE_STORE_PASSWORD"] not in done.output


def test_the_settings_accept_what_the_guide_wrote(
    tmp_path: Path, answers: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from opencde.settings import Settings

    path = tmp_path / ".env"
    typed = ["y", "https://cde.example.org", "", "n", "n"]
    assert CliRunner().invoke(app, ["init", "--path", str(path)], input="\n".join(typed) + "\n").exit_code == 0
    settings = Settings(_env_file=path)  # type: ignore[call-arg]
    assert settings.is_production and settings.public_url == "https://cde.example.org"
    assert settings.store_url is not None and not settings.uses_graph, "no graph without IFC-DB"
