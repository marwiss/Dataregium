# This file was generated with the assistance of an AI coding tool.
"""Liveness and readiness endpoints for containers, load balancers and ``opencde doctor``."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from opencde import __version__
from opencde.graph.driver import full_within, ping_within

router = APIRouter(tags=["operations"])


@router.get("/healthz", summary="Liveness: the process is up", include_in_schema=False)
async def healthz() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


async def _state(graph: Any, seconds: float) -> str:
    """``ok``, ``unavailable``, or ``full`` for a graph that answers but refuses changes (Hemsidor's D4)."""
    if not await ping_within(graph.driver, graph.database, seconds):
        return "unavailable"
    return "full" if await full_within(graph, seconds) else "ok"


async def _answers(graph: Any, seconds: float) -> str:
    return "ok" if await ping_within(graph.driver, graph.database, seconds) else "unavailable"


async def _postgres(postgres: Any, seconds: float) -> str:
    return "ok" if await postgres.ping(seconds) else "unavailable"


async def _constant(value: str) -> str:
    return value


@router.get("/readyz", summary="Readiness: the databases answer and take changes", include_in_schema=False)
async def readyz(request: Request) -> JSONResponse:
    settings = request.app.state.settings
    graph = request.app.state.graph
    seconds = settings.graph_connect_timeout_seconds
    store = getattr(request.app.state, "store", graph)
    postgres = getattr(request.app.state, "postgres", None)
    workcopy = getattr(request.app.state, "workcopy", None)
    # The server's own data is in the IFC graph's connection ("shared") unless it has its own (ADR 0027) or is in
    # PostgreSQL (ADR 0029); the working copy's state is reported, never a reason not to be ready (ADR 0024). A
    # server that uses no graph (tiers 1 and 2, F58 a) says "none" for it and is ready when PostgreSQL is.
    in_use = getattr(request.app.state, "uses_graph", True)
    if postgres is not None:
        store_check = _postgres(postgres, seconds)
    else:
        store_check = _state(store, seconds) if store is not graph else _constant("shared")
    copy_check = _answers(workcopy, seconds) if workcopy is not None else _constant("none")
    graph_check = _state(graph, seconds) if in_use else _constant("none")
    graph_state, store_state, copy_state = await asyncio.gather(graph_check, store_check, copy_check)
    if graph_state in ("ok", "none") and store_state not in ("unavailable", "full"):
        return JSONResponse(
            {"status": "ready", "graph": graph_state, "store": store_state}
            | ({"dialect": graph.dialect.name.value} if in_use else {})
            | {"workcopy": copy_state}
            | ({"store_dialect": "postgres"} if postgres is not None else {})
        )
    return JSONResponse({"status": "not_ready", "graph": graph_state, "store": store_state}, status_code=503)
