# This file was generated with the assistance of an AI coding tool.
"""Create the shared application objects from settings.

Used by the ASGI application and by the command line, so both see exactly the
same database, schema and storage.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, NamedTuple

from opencde.graph.dialect import get_dialect
from opencde.graph.driver import create_driver, create_store_driver, create_workcopy_driver, ping_within
from opencde.graph.graph import Graph
from opencde.graph.schema import apply_schema
from opencde.logging import get_logger
from opencde.postgres.database import Postgres, wait_for_postgres
from opencde.postgres.schema import migrate
from opencde.settings import Settings
from opencde.storage import LocalStorage

if TYPE_CHECKING:
    from opencde.store.protocols import ClientStore, ConversionStore, ProjectStore, TokenStore, UserStore

log = get_logger(__name__)


class GraphUnavailable(RuntimeError):
    pass


async def wait_for_graph(graph: Graph, timeout_seconds: float, step_seconds: float = 1.0) -> None:
    """Wait until the database answers, or raise. Containers start in any order."""
    waited = 0.0
    while True:
        if await ping_within(graph.driver, graph.database, min(step_seconds, 2.0) + 2.0):
            return
        if waited >= timeout_seconds:
            raise GraphUnavailable(f"graph database at {graph.driver} did not answer within {timeout_seconds}s")
        await asyncio.sleep(step_seconds)
        waited += step_seconds
        step_seconds = min(step_seconds * 1.5, 5.0)


def make_graph(settings: Settings) -> Graph:
    return Graph(create_driver(settings), settings.database_name, get_dialect(settings.graph_dialect))


def make_workcopy(settings: Settings) -> Graph | None:
    """The working copy beside the archive (ADR 0024), when one is configured and IFC-DB, its only user, is on."""
    if not settings.has_workcopy or not settings.ifcgraph_enabled:
        return None
    return Graph(
        create_workcopy_driver(settings), settings.workcopy_database_name, get_dialect(settings.workcopy_graph_dialect)
    )


def make_store(settings: Settings) -> Graph | None:
    """The graph of the server's own data apart from the IFC graph (F24 a), when one is configured."""
    if not settings.has_store:
        return None
    return Graph(create_store_driver(settings), settings.store_database_name, get_dialect(settings.store_graph_dialect))


def make_postgres(settings: Settings) -> Postgres | None:
    """The server's own data in PostgreSQL (ADR 0029), when ``OPENCDE_STORE_URL`` names it; nothing connects yet."""
    if settings.store_url is None:
        return None
    return Postgres(
        settings.store_url,
        settings.store_password.get_secret_value(),
        connect_timeout_seconds=settings.graph_connect_timeout_seconds,
    )


def make_storage(settings: Settings) -> LocalStorage:
    return LocalStorage(settings.data_dir)


def uses_graph(settings: Settings) -> bool:
    """Whether the server keeps anything in a graph (``Settings.uses_graph``): not in tiers 1 and 2, where
    PostgreSQL holds its own data and IFC-DB is off (F58 a)."""
    return settings.uses_graph


class Store(NamedTuple):
    """The server's own data for a command that touches nothing else, its stores chosen as the routes' are: in
    PostgreSQL when ``OPENCDE_STORE_URL`` names it (ADR 0029), else in ``graph``, the store's graph or the IFC graph's
    connection (ADR 0027). With PostgreSQL ``graph`` is the IFC graph's connection, made but never connected (a driver
    sends nothing until a statement runs), as the application hands it to the choosers, which leave it aside then."""

    graph: Graph
    postgres: Postgres | None
    settings: Settings

    @property
    def users(self) -> UserStore:
        from opencde.store.accounts.postgres import user_store

        return user_store(self.graph, self.postgres)

    @property
    def projects(self) -> ProjectStore:
        from opencde.store.accounts.postgres import project_store

        return project_store(self.graph, self.postgres)

    @property
    def clients(self) -> ClientStore:
        from opencde.store.oauth2.postgres import client_store

        return client_store(self.graph, self.postgres, self.settings)

    @property
    def tokens(self) -> TokenStore:
        from opencde.store.oauth2.postgres import token_store

        return token_store(self.graph, self.postgres, self.settings)

    @property
    def conversions(self) -> ConversionStore:
        from opencde.store.jobs.postgres import conversion_store

        return conversion_store(self.graph, self.postgres)


class Graphs(NamedTuple):
    """The databases of a command that touches the models: IFC-DB's ``graph``, and the server's own data, in
    ``store``, the same graph unless ``OPENCDE_STORE_GRAPH_URI`` names another (F24 a, ADR 0027), or in ``postgres``
    when ``OPENCDE_STORE_URL`` names it (ADR 0029), ``store`` then being the IFC graph as for the application.
    ``models`` is False when the server uses no graph (``uses_graph``): ``graph`` is then made but never connected."""

    graph: Graph
    store: Graph
    postgres: Postgres | None = None
    models: bool = True

    def distinct(self) -> tuple[Graph, ...]:
        """Each graph in use once: one when the store shares the IFC graph's connection, none without a graph."""
        if not self.models:
            return ()
        return (self.graph,) if self.store is self.graph else (self.graph, self.store)


@asynccontextmanager
async def _connected(graph: Graph, wait_seconds: float, schema: bool) -> AsyncGenerator[Graph, None]:
    try:
        await wait_for_graph(graph, wait_seconds)
        if schema:
            await apply_schema(graph)
        yield graph
    finally:
        await graph.close()


@asynccontextmanager
async def _migrated(postgres: Postgres, wait_seconds: float, migrations: bool) -> AsyncGenerator[Postgres, None]:
    try:
        await wait_for_postgres(postgres, wait_seconds)
        if migrations:
            await migrate(postgres)
        yield postgres
    finally:
        await postgres.close()


@asynccontextmanager
async def _unconnected(graph: Graph) -> AsyncGenerator[Graph, None]:
    try:
        yield graph
    finally:
        await graph.close()


@asynccontextmanager
async def store_session(
    settings: Settings, *, wait_seconds: float = 30.0, schema: bool = True
) -> AsyncGenerator[Store, None]:
    """The server's own data for command-line use, where it is: PostgreSQL, migrated, when ``OPENCDE_STORE_URL``
    names it, and no graph is connected; else the store's graph or the IFC graph's connection, with the schema
    applied. Waits for the database."""
    postgres = make_postgres(settings)
    if postgres is None:
        async with _connected(make_store(settings) or make_graph(settings), wait_seconds, schema) as store:
            yield Store(store, None, settings)
        return
    async with _unconnected(make_graph(settings)) as graph, _migrated(postgres, wait_seconds, schema) as own:
        yield Store(graph, own, settings)


@asynccontextmanager
async def graph_session(
    settings: Settings,
    *,
    wait_seconds: float = 30.0,
    schema: bool = True,
    models: bool | None = None,
    migrations: bool = True,
) -> AsyncGenerator[Graphs, None]:
    """The IFC graph and the server's own data for command-line use, for what touches the models: each connected,
    with the schema applied and PostgreSQL migrated (not with ``migrations=False``, for a restore, which migrates as
    it goes). With PostgreSQL the IFC graph is connected when ``models`` says so, by default when the server uses a
    graph."""
    postgres = make_postgres(settings)
    if postgres is None:
        async with _connected(make_graph(settings), wait_seconds, schema) as graph:
            store = make_store(settings)
            if store is None:
                yield Graphs(graph, graph)
                return
            async with _connected(store, wait_seconds, schema) as own:
                yield Graphs(graph, own)
        return
    in_use = uses_graph(settings) if models is None else models
    made = make_graph(settings)
    opened = _connected(made, wait_seconds, schema) if in_use else _unconnected(made)
    async with opened as graph, _migrated(postgres, wait_seconds, migrations) as own:
        yield Graphs(graph, graph, own, models=in_use)
