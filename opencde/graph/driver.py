# This file was generated with the assistance of an AI coding tool.
"""Create and probe the Bolt connection to the graph database."""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING

from neo4j import AsyncDriver, AsyncGraphDatabase

from opencde.logging import get_logger
from opencde.settings import Settings

if TYPE_CHECKING:
    from opencde.graph.graph import Graph

log = get_logger(__name__)

FULL_SHARE = 0.95  # a Memgraph counts as full from this share of its memory limit, where a change of any size fails
_SIZE = re.compile(r"^([0-9]+(?:\.[0-9]+)?)([KMGTP]i)?B$")
_UNITS = {None: 1, "Ki": 1024, "Mi": 1024**2, "Gi": 1024**3, "Ti": 1024**4, "Pi": 1024**5}


def create_driver(settings: Settings) -> AsyncDriver:
    """Build the driver. No network traffic happens until the first query."""
    return make_driver(
        settings.graph_uri,
        settings.graph_user,
        settings.graph_password.get_secret_value(),
        settings.graph_connect_timeout_seconds,
    )


def create_workcopy_driver(settings: Settings) -> AsyncDriver:
    """The driver of the working copy (ADR 0024); the caller checks that one is configured."""
    return make_driver(
        settings.workcopy_graph_uri or "",
        settings.workcopy_graph_user,
        settings.workcopy_graph_password.get_secret_value(),
        settings.graph_connect_timeout_seconds,
    )


def create_store_driver(settings: Settings) -> AsyncDriver:
    """The driver of the server's own graph (F24 a); the caller checks that one is configured."""
    return make_driver(
        settings.store_graph_uri or "",
        settings.store_graph_user,
        settings.store_graph_password.get_secret_value(),
        settings.graph_connect_timeout_seconds,
    )


def make_driver(uri: str, user: str, password: str, connect_timeout_seconds: float) -> AsyncDriver:
    return AsyncGraphDatabase.driver(
        uri,
        auth=(user, password),
        connection_timeout=connect_timeout_seconds,
        max_connection_lifetime=30 * 60,
        user_agent="dataregium-opencde",
        # Neo4j sends advisory notifications (for example "property does not
        # exist yet" on an empty database); they would only clutter the logs.
        notifications_min_severity="OFF",
    )


async def ping(driver: AsyncDriver, database: str) -> bool:
    """True when the database answers a trivial query.

    Callers bound the wait with ``asyncio.timeout(...)``; a timeout counts as
    "not reachable" like any other failure.
    """
    try:
        records, _summary, _keys = await driver.execute_query("RETURN 1 AS ok", database_=database)
        return bool(records) and records[0]["ok"] == 1
    except Exception as exc:  # the driver raises several unrelated types; all of them mean "unreachable"
        log.warning("graph_ping_failed", error_type=type(exc).__name__)
        return False


async def ping_within(driver: AsyncDriver, database: str, seconds: float) -> bool:
    """``ping`` with an upper bound on the wait."""
    try:
        async with asyncio.timeout(seconds):
            return await ping(driver, database)
    except TimeoutError:
        log.warning("graph_ping_timeout", seconds=seconds)
        return False


def size_in_bytes(text: object) -> float | None:
    """A size as Memgraph writes it (``195.68MiB``), in bytes; None for anything else."""
    match = _SIZE.match(str(text).strip())
    return float(match.group(1)) * _UNITS[match.group(2)] if match else None


async def full_within(graph: Graph, seconds: float) -> bool:
    """Whether the graph's memory in use has reached ``FULL_SHARE`` of its limit, as far as the product tells it:
    Memgraph does, Neo4j not without procedures. False when it is not told in time; the ping says whether it answers."""
    statement = graph.dialect.storage_info
    if statement is None:
        return False
    try:
        async with asyncio.timeout(seconds):
            rows = await graph.schema_rows(statement)
    except Exception as exc:  # as with the ping, every failure only means that nothing is known
        log.warning("graph_storage_info_failed", error_type=type(exc).__name__)
        return False
    figures = {row.get("storage info"): row.get("value") for row in rows}
    used, limit = size_in_bytes(figures.get("memory_tracked")), size_in_bytes(figures.get("memory_limit"))
    return used is not None and bool(limit) and used >= FULL_SHARE * (limit or 0)
