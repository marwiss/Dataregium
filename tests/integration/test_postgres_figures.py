# This file was generated with the assistance of an AI coding tool.
"""The administrators' figures (ADR 0029) against a real database: the same case on the graph's repositories and on
PostgreSQL's, each area answering for its own (every membership, the live browser sessions and refresh-token
families, the topics, documents, BCF documents and models of each project, the request log's entries), and
``Figures`` putting them together as the admin pages and ``/admin/api`` show them.

PostgreSQL runs in a container of its own (``postgres_target`` and ``pg`` in the conftest), one per test session;
every test starts from empty tables with the migrations applied. The case runs once on the graph and once on
PostgreSQL, which needs no graph: ``-k on_postgresql`` starts PostgreSQL alone.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest

from opencde.accounts.roles import Role
from opencde.admin.queries import Figures
from opencde.auth import pkce
from opencde.bcf.schemas import TopicPOST
from opencde.graph.graph import Graph
from opencde.ids import new_id
from opencde.postgres.database import Postgres
from opencde.store.accounts.postgres import (
    PostgresProjectRepository,
    PostgresUserRepository,
    PostgresWebSessionRepository,
)
from opencde.store.accounts.projects import ProjectRepository
from opencde.store.accounts.sessions import WebSessionRepository
from opencde.store.accounts.users import UserRepository
from opencde.store.bcf.documents import BcfDocumentRepository
from opencde.store.bcf.postgres import PostgresBcfDocumentRepository, PostgresTopicRepository
from opencde.store.bcf.topics import TopicRepository
from opencde.store.documents.postgres import PostgresDocumentRepository
from opencde.store.documents.versions import DocumentRepository
from opencde.store.jobs.conversions import ConversionRepository
from opencde.store.jobs.postgres import PostgresConversionRepository
from opencde.store.logbook.postgres import PostgresRequestLogRepository
from opencde.store.logbook.requests import RequestLogRepository
from opencde.store.oauth2.clients import ClientRepository
from opencde.store.oauth2.postgres import PostgresClientRepository, PostgresTokenRepository
from opencde.store.oauth2.tokens import TokenRepository
from opencde.store.protocols import WebSessionStore
from opencde.timeutil import later_iso, now_iso

pytestmark = pytest.mark.integration

PASSWORD = "a-long-and-strong-passphrase"
REDIRECT = "http://127.0.0.1/callback"
VERIFIER = "v" * 50
TTLS = {"code_ttl": 60, "access_ttl": 3600, "refresh_ttl": 30 * 86400, "refresh_idle": 7 * 86400}
SESSION_KEYS = {"id", "user_id", "username", "created_at", "expires_at", "idle_expires_at", "ip"}
FAMILY_KEYS = {"family_id", "user_id", "username", "client_id", "client_name", "scope", "created_at", "expires_at"}


async def _tick() -> None:
    """Past the millisecond of the server's timestamps, so that newest first has one answer."""
    await asyncio.sleep(0.003)


async def figures_of_every_area(figures: Figures, make_sessions: Callable[..., WebSessionStore]) -> None:
    sessions = make_sessions(ttl_seconds=3600, idle_seconds=600)
    ids = {name: (await figures.users.create(name, PASSWORD)).id for name in ("admin", "editor", "viewer", "outsider")}
    building = (await figures.projects.create("Building permit")).project_id
    demolition = (await figures.projects.create("Demolition permit")).project_id
    await figures.projects.set_member(building, ids["viewer"], Role.VIEWER)
    await figures.projects.set_member(building, ids["editor"], Role.EDITOR)
    await figures.projects.set_member(demolition, ids["outsider"], Role.MANAGER)
    await figures.projects.set_member(demolition, ids["editor"], Role.VIEWER)

    client, _ = await figures.clients.create("Native app", [REDIRECT], public=True)
    code = await figures.tokens.issue_code(
        user_id=ids["editor"],
        client_id=client.client_id,
        redirect_uri=REDIRECT,
        scope="profile bcf",
        code_challenge=pkce.challenge_for(VERIFIER),
    )
    await figures.tokens.redeem_code(code=code, client_id=client.client_id, redirect_uri=None, code_verifier=VERIFIER)
    await sessions.create(ids["viewer"], None)
    await _tick()
    await sessions.create(ids["editor"], "10.0.0.1")

    for title in ("Wall", "Slab"):
        await figures.topics.create(project_id=building, author="editor", body=TopicPOST(title=title))
    await figures.documents.create_version(
        project_id=building,
        document_id=None,
        title="Plan",
        version_number="1",
        file_name="plan.ifc",
        size_in_bytes=3,
        sha256="0" * 64,
        storage_key=f"documents/{new_id()}",
        author="editor",
        metadata=[],
    )
    await figures.bcf_documents.create(
        project_id=building, guid=new_id(), filename="plan.pdf", author="editor", storage_key="bcf/plan", size=3
    )
    await figures.conversions.create(
        project_id=building, model_id=new_id(), file_name="plan.ifc", storage_key="documents/plan"
    )
    for path in ("/bcf/3.0/projects", "/foundation/1.0/current-user"):
        await figures.request_log.add(
            {
                "id": new_id(),
                "at": now_iso(),
                "method": "GET",
                "path": path,
                "operation": None,
                "status": 200,
                "duration_ms": 1.0,
                "request_id": None,
                "bytes_in": 0,
                "bytes_out": 2,
                "username": None,
                "client_id": None,
                "ip": None,
                "expires_at": later_iso(3600),
            }
        )

    assert await figures.counts() == {
        "users": 4,
        "projects": 2,
        "clients": 1,
        "topics": 2,
        "documents": 1,
        "bcf_documents": 1,
        "sessions": 1,
    }
    assert await figures.project_stats() == {
        building: {"members": 2, "topics": 2, "documents": 2},
        demolition: {"members": 2, "topics": 0, "documents": 0},
    }, "the documents of both APIs"
    assert (await figures.members_by_project())[building] == [
        {"user_id": ids["editor"], "username": "editor", "role": "editor"},
        {"user_id": ids["viewer"], "username": "viewer", "role": "viewer"},
    ], "by user name"
    assert (await figures.memberships_by_user())[ids["editor"]] == [
        {"project_id": building, "role": "editor"},
        {"project_id": demolition, "role": "viewer"},
    ], "by project name"
    assert await figures.membership_counts() == {ids["editor"]: 2, ids["viewer"]: 1, ids["outsider"]: 1}
    assert await figures.model_counts() == {building: 1, "": 1}
    assert await figures.request_log.count() == 2

    (family,) = await figures.tokens.live_families()
    assert set(family) == FAMILY_KEYS and family["user_id"] == ids["editor"] and family["client_name"] == "Native app"
    assert family["scope"] == "profile bcf" and family["expires_at"] > family["created_at"]
    assert await figures.tokens.live_count() == 1
    assert await figures.tokens.family_user(family["family_id"]) == ids["editor"]
    assert await figures.tokens.family_user("no-such-family") is None

    live = await sessions.live()
    assert [row["username"] for row in live] == ["editor", "viewer"], "newest first"
    assert all(set(row) == SESSION_KEYS for row in live) and live[0]["ip"] == "10.0.0.1" and live[1]["ip"] is None
    assert [row["user_id"] for row in await sessions.live(user_id=ids["viewer"])] == [ids["viewer"]]
    assert [row["username"] for row in await sessions.live(limit=1)] == ["editor"]
    assert await sessions.delete_by_id(live[0]["id"]) and not await sessions.delete_by_id(live[0]["id"])
    assert [row["username"] for row in await sessions.live()] == ["viewer"]
    await make_sessions(ttl_seconds=3600, idle_seconds=-1).create(ids["admin"], None)
    await make_sessions(ttl_seconds=-1, idle_seconds=600).create(ids["admin"], None)
    assert len(await sessions.live()) == 1, "a session that has ended, or been idle too long, is no live one"


def _on_the_graph(graph: Graph) -> tuple[Figures, Callable[..., WebSessionStore]]:
    figures = Figures(
        users=UserRepository(graph),
        projects=ProjectRepository(graph),
        clients=ClientRepository(graph),
        tokens=TokenRepository(graph, **TTLS),
        topics=TopicRepository(graph),
        documents=DocumentRepository(graph),
        bcf_documents=BcfDocumentRepository(graph),
        conversions=ConversionRepository(graph),
        request_log=RequestLogRepository(graph),
    )
    return figures, lambda **ttls: WebSessionRepository(graph, **ttls)


def _on_postgresql(pg: Postgres) -> tuple[Figures, Callable[..., WebSessionStore]]:
    figures = Figures(
        users=PostgresUserRepository(pg),
        projects=PostgresProjectRepository(pg),
        clients=PostgresClientRepository(pg),
        tokens=PostgresTokenRepository(pg, **TTLS),
        topics=PostgresTopicRepository(pg),
        documents=PostgresDocumentRepository(pg),
        bcf_documents=PostgresBcfDocumentRepository(pg),
        conversions=PostgresConversionRepository(pg),
        request_log=PostgresRequestLogRepository(pg),
    )
    return figures, lambda **ttls: PostgresWebSessionRepository(pg, **ttls)


async def test_on_the_graph(graph: Graph) -> None:
    await figures_of_every_area(*_on_the_graph(graph))


async def test_on_postgresql(pg: Postgres) -> None:
    await figures_of_every_area(*_on_postgresql(pg))
