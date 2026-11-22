# This file was generated with the assistance of an AI coding tool.
"""Where `opencde serve` lets the local engine listen (ADR 0023, F77 a): on the loopback, and on its container's
own interfaces only when compose.local.yaml says it runs in one and it does, so that the engine is never open to
the network by a setting on a person's own computer."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from opencde import cli
from opencde.cli import app
from tests.conftest import test_settings as make_settings

LOCAL = {"environment": "local", "public_url": "http://127.0.0.1:8000", "cors_origins": ["null"]}
EVERY = "0.0.0.0"  # noqa: S104  # every interface: the address these tests are about


@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """The arguments uvicorn would have started with, instead of a server."""
    import uvicorn

    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: calls.append(kwargs))
    return calls


def serve(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *args: str, contained: bool = False, **values: Any) -> Any:
    settings = make_settings(tmp_path, **{**LOCAL, **values})
    monkeypatch.setattr(cli, "_settings", lambda: settings)
    monkeypatch.setattr(cli, "_in_a_container", lambda: contained)
    return CliRunner().invoke(app, ["serve", *args])


def test_the_local_engine_listens_on_the_loopback_in_and_out_of_a_container(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, started: list[dict[str, Any]]
) -> None:
    assert serve(monkeypatch, tmp_path).exit_code == 0
    assert serve(monkeypatch, tmp_path, contained=True, local_in_container=True).exit_code == 0
    assert [call["host"] for call in started] == ["127.0.0.1", "127.0.0.1"]


def test_it_listens_on_every_interface_only_in_a_container_that_says_so(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, started: list[dict[str, Any]]
) -> None:
    refused = serve(monkeypatch, tmp_path, "--host", EVERY)
    assert refused.exit_code != 0 and "loopback only" in refused.output
    assert "OPENCDE_LOCAL_IN_CONTAINER" in refused.output, "the refusal names the way a container takes"
    outside = serve(monkeypatch, tmp_path, "--host", EVERY, local_in_container=True)
    assert outside.exit_code != 0 and "runs in no container" in outside.output, "the setting opens nothing outside"
    assert not started
    inside = serve(monkeypatch, tmp_path, "--host", EVERY, contained=True, local_in_container=True)
    assert inside.exit_code == 0 and started[0]["host"] == EVERY
    assert "inside its container" in inside.output


def test_a_server_that_is_not_the_local_engine_listens_where_it_is_told(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, started: list[dict[str, Any]]
) -> None:
    settings = make_settings(tmp_path, environment="development")
    monkeypatch.setattr(cli, "_settings", lambda: settings)
    assert CliRunner().invoke(app, ["serve", "--host", EVERY]).exit_code == 0
    assert started[0]["host"] == EVERY
