# This file was generated with the assistance of an AI coding tool.
"""Liveness and readiness endpoints for containers, load balancers and ``opencde doctor``."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from opencde import __version__
from opencde.graph.driver import full_within, ping_within

router = APIRouter(tags=["operations"])


@router.get("/healthz", summary="Liveness: the process is up", include_in_schema=False)
async def healthz() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@router.get("/readyz", summary="Readiness: the graph databases answer and take changes", include_in_schema=False)
async def readyz(request: Request) -> JSONResponse:
    settings = request.app.state.settings
    graph = request.app.state.graph
    seconds = settings.graph_connect_timeout_seconds
    reachable = await ping_within(graph.driver, graph.database, seconds)
    # A Memgraph at its memory limit answers but refuses changes (Hemsidor's D4): not ready, and said so.
    graph_state = ("full" if await full_within(graph, seconds) else "ok") if reachable else "unavailable"
    store = getattr(request.app.state, "store", graph)
    postgres = getattr(request.app.state, "postgres", None)
    store_state = "shared"  # the server's own data in the IFC graph's connection, unless it has its own (ADR 0027)
    if postgres is not None:  # or PostgreSQL (ADR 0029)
        store_state = "ok" if await postgres.ping(settings.graph_connect_timeout_seconds) else "unavailable"
    elif store is not graph:
        answers = await ping_within(store.driver, store.database, seconds)
        store_state = ("full" if await full_within(store, seconds) else "ok") if answers else "unavailable"
    workcopy = getattr(request.app.state, "workcopy", None)
    copy_state = "none"
    if workcopy is not None:  # the working copy's state is reported, never a reason not to be ready (ADR 0024)
        copy_state = (
            "ok"
            if await ping_within(workcopy.driver, workcopy.database, settings.graph_connect_timeout_seconds)
            else "unavailable"
        )
    if graph_state == "ok" and store_state not in ("unavailable", "full"):
        return JSONResponse(
            {
                "status": "ready",
                "graph": "ok",
                "store": store_state,
                "dialect": graph.dialect.name.value,
                "workcopy": copy_state,
            }
            | ({"store_dialect": "postgres"} if postgres is not None else {})
        )
    return JSONResponse({"status": "not_ready", "graph": graph_state, "store": store_state}, status_code=503)
