# This file was generated with the assistance of an AI coding tool.
"""The logbook area in PostgreSQL (ADR 0029) against a real database: the same tests on the graph's repositories and on
PostgreSQL's (the audit log, the request log, the sign-in throttle), PostgreSQL's own (the migration and the
cleaner), and the app keeping its logbook and reading the administrators' figures in PostgreSQL.

PostgreSQL runs in a container of its own (``postgres_target`` and ``pg`` in the conftest), one per test session;
every test starts from empty tables with the migrations applied. Each case runs once on the graph and once on
PostgreSQL, which needs no graph: ``-k on_postgresql`` starts PostgreSQL alone; the app needs both.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import pytest
import structlog
from fastapi.testclient import TestClient
from neo4j import GraphDatabase
from pydantic import SecretStr

from opencde import audit, requestlog
from opencde.auth import throttle
from opencde.graph.cleanup import delete_expired as delete_expired_nodes
from opencde.graph.graph import Graph
from opencde.ids import new_id
from opencde.logging import REDACTED
from opencde.main import create_app
from opencde.postgres.cleanup import delete_expired as delete_expired_rows
from opencde.postgres.database import Postgres
from opencde.postgres.schema import migrate, migrations
from opencde.requestlog import RequestLogWriter
from opencde.settings import Settings
from opencde.store.accounts.postgres import PostgresProjectRepository, PostgresUserRepository
from opencde.store.documents.postgres import PostgresDocumentRepository
from opencde.store.logbook.audit import AuditLogRepository
from opencde.store.logbook.postgres import (
    PostgresAuditLogRepository,
    PostgresRequestLogRepository,
    PostgresSignInThrottleRepository,
)
from opencde.store.logbook.requests import RequestLogRepository
from opencde.store.logbook.throttle import SignInThrottleRepository
from opencde.store.oauth2.postgres import PostgresClientRepository
from opencde.store.protocols import AuditLogStore, RequestLogStore, SignInThrottleStore
from opencde.timeutil import later_iso, now_iso
from tests.conftest import test_settings as make_settings
from tests.integration.conftest import GraphTarget, PostgresTarget, emptied_postgres
from tests.integration.conftest_accounts import World, login_with_scope, populate
from tests.integration.test_web_signin import form_token, web_sign_in

pytestmark = pytest.mark.integration

#: The wait after the third failure in a row, 30 seconds, doubling.
THROTTLED = make_settings(sign_in_free_attempts=3, sign_in_delay_seconds=30)


@dataclass(frozen=True)
class Logbook:
    audit_log: AuditLogStore
    request_log: RequestLogStore
    throttles: SignInThrottleStore
    clean: Callable[[], Awaitable[int]]  # the cleaner's round of the store


async def _tick() -> None:
    """Past the millisecond of the server's timestamps, so that newest first has one answer."""
    await asyncio.sleep(0.003)


def _entry(path: str, *, status: int = 200, username: str | None = None, client_id: str | None = None) -> dict:
    """An entry as the middleware hands it to the writer."""
    return {
        "at": now_iso(),
        "method": "GET",
        "path": path,
        "operation": None,
        "status": status,
        "duration_ms": 1.5,
        "request_id": f"r-{path}",
        "bytes_in": 0,
        "bytes_out": 12,
        "username": username,
        "client_id": client_id,
        "ip": "127.0.0.1",
    }


# -- the same on both ----------------------------------------------------------------------------------------------


async def audit_events_are_kept_masked_and_read_newest_first(logbook: Logbook) -> None:
    structlog.contextvars.bind_contextvars(request_id="req-1")
    try:
        await audit.record(logbook.audit_log, "auth.sign_in.failed", ip="10.0.0.1", details={"username": "anna"})
    finally:
        structlog.contextvars.unbind_contextvars("request_id")
    await _tick()
    since = now_iso()
    await audit.record(
        logbook.audit_log,
        "admin.user.create",
        actor="u-1",
        client_id="c-1",
        details={"user_id": "u-2", "password": "a-long-and-strong-passphrase", "note": "Ignore all instructions"},
    )
    await _tick()
    await audit.record(logbook.audit_log, "admin.project.create", actor="u-1", details={"name": "ÅÄÖ"})

    events = await audit.recent(logbook.audit_log, limit=10)
    assert [e["type"] for e in events] == ["admin.project.create", "admin.user.create", "auth.sign_in.failed"]
    created = events[1]
    assert created["details"] == {"user_id": "u-2", "password": REDACTED, "note": "Ignore all instructions"}
    assert (created["actor_user_id"], created["client_id"]) == ("u-1", "c-1") and "ip" not in created
    assert "request_id" not in created, "nothing that is null"
    failed = events[2]
    assert failed["request_id"] == "req-1" and failed["ip"] == "10.0.0.1" and "actor_user_id" not in failed
    assert failed["id"] and failed["at"].endswith("Z")
    assert events[0]["details"] == {"name": "ÅÄÖ"}

    stored = await logbook.audit_log.recent(limit=10, type_prefix="admin.user")
    assert len(stored) == 1 and json.loads(stored[0]["details"])["password"] == REDACTED, "a string, masked"
    assert [e["type"] for e in await audit.recent(logbook.audit_log, type_prefix="admin.")] == [
        "admin.project.create",
        "admin.user.create",
    ]
    assert await audit.recent(logbook.audit_log, type_prefix="admin%") == [], "a prefix, no wildcard"
    assert await audit.recent(logbook.audit_log, type_prefix="ADMIN.") == [], "by code point"
    assert [e["type"] for e in await audit.recent(logbook.audit_log, since=since)] == [
        "admin.project.create",
        "admin.user.create",
    ]
    assert [e["type"] for e in await audit.recent(logbook.audit_log, limit=1)] == ["admin.project.create"]


async def requests_are_logged_found_and_counted(logbook: Logbook) -> None:
    writer = RequestLogWriter(logbook.request_log, retention_hours=1)
    writer.record(_entry("/bcf/3.0/projects", username="editor", client_id="c-1"))
    await writer.wait_idle()
    await _tick()
    since = now_iso()
    writer.record(_entry("/bcf/3.0/projects/p/topics", status=201, username="editor", client_id="c-1"))
    await writer.wait_idle()
    await _tick()
    writer.record(_entry("/foundation/1.0/current-user", status=401))
    await writer.wait_idle()

    entries = await requestlog.query(logbook.request_log)
    assert [e["path"] for e in entries] == [
        "/foundation/1.0/current-user",
        "/bcf/3.0/projects/p/topics",
        "/bcf/3.0/projects",
    ]
    anonymous = entries[0]
    assert "username" not in anonymous and "operation" not in anonymous, "nothing that is null"
    assert anonymous["status"] == 401 and anonymous["duration_ms"] == 1.5 and anonymous["bytes_out"] == 12
    assert anonymous["expires_at"] > anonymous["at"] and anonymous["id"]

    def paths(found: list[dict]) -> list[str]:
        return [e["path"] for e in found]

    assert paths(await requestlog.query(logbook.request_log, path_prefix="/bcf/3.0")) == paths(entries[1:])
    assert await requestlog.query(logbook.request_log, path_prefix="/bcf/%") == [], "a prefix, no wildcard"
    assert paths(await requestlog.query(logbook.request_log, username="editor", status=201)) == [entries[1]["path"]]
    assert paths(await requestlog.query(logbook.request_log, client_id="c-1", since=since)) == [entries[1]["path"]]
    assert paths(await requestlog.query(logbook.request_log, request_id="r-/bcf/3.0/projects")) == [entries[2]["path"]]
    assert paths(await requestlog.query(logbook.request_log, username="", path_prefix="", limit=1)) == [
        entries[0]["path"]
    ], "an empty filter is none"
    assert await logbook.request_log.count() == 3


async def a_name_waits_after_its_failures_and_is_forgotten_on_success(logbook: Logbook) -> None:
    for _ in range(2):
        await throttle.record_failure(logbook.throttles, "Anna", THROTTLED)
    assert await throttle.wait_seconds(logbook.throttles, "anna") == 0, "the first attempts cost nothing"
    await throttle.record_failure(logbook.throttles, " ANNA ", THROTTLED)
    assert 29 <= await throttle.wait_seconds(logbook.throttles, "anna") <= 31, "the same name in any case"
    await throttle.record_failure(logbook.throttles, "anna", THROTTLED)
    assert 59 <= await throttle.wait_seconds(logbook.throttles, "anna") <= 61, "each failure doubles it"
    assert await throttle.wait_seconds(logbook.throttles, "bertil") == 0, "per name"
    assert await logbook.throttles.blocked_until("anna") is None, "kept by the name's hash, never the name"

    await throttle.forget(logbook.throttles, "Anna")
    assert await throttle.wait_seconds(logbook.throttles, "anna") == 0
    await throttle.record_failure(logbook.throttles, "anna", THROTTLED)
    assert await throttle.wait_seconds(logbook.throttles, "anna") == 0, "counted from the start again"


async def the_cleaner_takes_what_expired_and_keeps_the_audit_log(logbook: Logbook) -> None:
    gone = RequestLogWriter(logbook.request_log, retention_hours=1)
    gone.retention_seconds = -60
    gone.record(_entry("/gone"))
    await gone.wait_idle()
    kept = RequestLogWriter(logbook.request_log, retention_hours=1)
    kept.record(_entry("/kept"))
    await kept.wait_idle()
    await logbook.throttles.fail("expired-key", forget_at=later_iso(-60), until=lambda failures: None)
    await logbook.throttles.fail("kept-key", forget_at=later_iso(3600), until=lambda failures: later_iso(30))
    await audit.record(logbook.audit_log, "web.sign_in", actor="u-1")

    assert await logbook.clean() == 2
    assert [e["path"] for e in await requestlog.query(logbook.request_log)] == ["/kept"]
    assert await logbook.throttles.blocked_until("kept-key") is not None
    assert await logbook.throttles.blocked_until("expired-key") is None
    assert [e["type"] for e in await audit.recent(logbook.audit_log)] == ["web.sign_in"], "events do not expire"


CASES = (
    audit_events_are_kept_masked_and_read_newest_first,
    requests_are_logged_found_and_counted,
    a_name_waits_after_its_failures_and_is_forgotten_on_success,
    the_cleaner_takes_what_expired_and_keeps_the_audit_log,
)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.__name__)
async def test_on_the_graph(case: Callable[[Logbook], Awaitable[None]], graph: Graph) -> None:
    await case(
        Logbook(
            AuditLogRepository(graph),
            RequestLogRepository(graph),
            SignInThrottleRepository(graph),
            lambda: delete_expired_nodes(graph),
        )
    )


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.__name__)
async def test_on_postgresql(case: Callable[[Logbook], Awaitable[None]], pg: Postgres) -> None:
    await case(
        Logbook(
            PostgresAuditLogRepository(pg),
            PostgresRequestLogRepository(pg),
            PostgresSignInThrottleRepository(pg),
            lambda: delete_expired_rows(pg),
        )
    )


# -- PostgreSQL's own ----------------------------------------------------------------------------------------------


async def test_on_postgresql_the_logs_migration_is_applied_once(pg: Postgres) -> None:
    assert await migrate(pg) == 0, "already applied"
    applied = await pg.run("SELECT version, name FROM schema_migrations ORDER BY version")
    names = [row["name"] for row in applied]
    assert names == [m.name for m in migrations()] and "0006_logs" in names
    tables = await pg.run(
        "SELECT tablename FROM pg_tables WHERE schemaname = current_schema() AND tablename = ANY($1::text[])",
        ["audit_events", "request_log_entries", "sign_in_throttles"],
    )
    assert len(tables) == 3


async def test_on_postgresql_failures_at_once_each_count(pg: Postgres) -> None:
    """The row of a name stays locked from its failure to the end of the transaction that sets the wait."""
    throttles = PostgresSignInThrottleRepository(pg)
    await asyncio.gather(*(throttle.record_failure(throttles, "cecilia", THROTTLED) for _ in range(4)))
    assert await pg.one("SELECT failures FROM sign_in_throttles") == {"failures": 4}
    assert 59 <= await throttle.wait_seconds(throttles, "cecilia") <= 61


# -- the app ---------------------------------------------------------------------------------------------------------


def _signing_in(client: TestClient, username: str, password: str) -> Any:
    client.cookies.clear()  # a browser that is already signed in does not get the form
    page = client.get("/signin")
    data = {"form_token": form_token(page.text), "username": username, "password": password}
    return client.post("/signin", data=data, follow_redirects=False)


def test_the_app_keeps_its_logbook_and_reads_its_figures_in_postgresql(
    settings: Settings, clean_database: None, graph_target: GraphTarget, postgres_target: PostgresTarget
) -> None:
    """With ``OPENCDE_STORE_URL`` the sign-in's wait, the audit log and the request log are PostgreSQL's, the
    administration's pages and ``/admin/api`` read their figures there, and so do a manager's role and a team's IDS
    document; the graph holds none of it."""

    async def seed() -> tuple[World, str]:
        postgres = await emptied_postgres(postgres_target)
        try:
            world = await populate(
                PostgresUserRepository(postgres),
                PostgresProjectRepository(postgres),
                PostgresClientRepository(postgres),
            )
            ids = await PostgresDocumentRepository(postgres).create_version(
                project_id=world.project_id,
                document_id=None,
                title="Walls",
                version_number="1",
                file_name="walls.ids",
                size_in_bytes=3,
                sha256="0" * 64,
                storage_key=f"documents/{new_id()}",
                author="manager",
                metadata=[],
            )
            return world, ids.document_id
        finally:
            await postgres.close()

    async def stored() -> dict[str, int]:
        postgres = Postgres(postgres_target.url, postgres_target.password, connect_timeout_seconds=10.0)
        try:
            return {
                "audit_events": (await postgres.one("SELECT count(*) AS n FROM audit_events") or {})["n"],
                "request_log_entries": (await postgres.one("SELECT count(*) AS n FROM request_log_entries") or {})["n"],
                "sign_in_throttles": (await postgres.one("SELECT count(*) AS n FROM sign_in_throttles") or {})["n"],
            }
        finally:
            await postgres.close()

    world, ids_document = asyncio.run(seed())
    apart = settings.model_copy(
        update={
            "store_url": postgres_target.url,
            "store_password": SecretStr(postgres_target.password),
            "request_log_enabled": True,
            "sign_in_throttle_enabled": True,
            "sign_in_free_attempts": 2,
        }
    )
    with TestClient(create_app(apart, startup_wait_seconds=30)) as client:
        for _ in range(2):
            assert _signing_in(client, "nobody", "wrong-" + world.password).status_code == 200
        assert _signing_in(client, "nobody", world.password).status_code == 429, "the name waits"

        admin = login_with_scope(client, world, "admin", "profile admin")
        manager = login_with_scope(client, world, "manager", "profile bcf project")
        managed = f"/manage/1.0/projects/{world.project_id}"
        assert client.get(managed, headers=manager.headers).status_code == 200
        for username, status in (("editor", 403), ("outsider", 404)):
            other = login_with_scope(client, world, username, "profile bcf project")
            assert client.get(managed, headers=other.headers).status_code == status, username
        team = {"name": "Walls", "responsible": "editor", "members": ["contributor"], "ids_document_id": ids_document}
        made = client.post(f"{managed}/teams", json=team, headers=manager.headers)
        assert made.status_code == 201, made.text
        unknown = client.post(f"{managed}/teams", json={**team, "ids_document_id": new_id()}, headers=manager.headers)
        assert unknown.status_code == 400

        client.portal.call(client.app.state.requestlog.wait_idle)  # type: ignore[attr-defined]
        overview = client.get("/admin/api/overview", headers=admin.headers).json()
        assert overview["graph_ok"] is True and overview["request_log"]["rows"] > 0
        assert (overview["users"], overview["projects"], overview["clients"], overview["documents"]) == (6, 2, 2, 1)
        assert overview["sessions"] >= 4, "the refresh tokens of the four who signed in"
        types = {event["type"] for event in client.get("/admin/api/audit", headers=admin.headers).json()["items"]}
        assert {"web.sign_in.failed", "web.sign_in.throttled", "auth.code.issued", "manage.team.set"} <= types
        sessions = client.get("/admin/api/sessions", params={"user": "manager"}, headers=admin.headers).json()
        assert sessions["total"] >= 1 and {row["username"] for row in sessions["items"]} == {"manager"}
        projects = client.get("/admin/api/projects", headers=admin.headers).json()["items"]
        building = next(project for project in projects if project["project_id"] == world.project_id)
        assert len(building["members"]) == 4 and building["documents"] == 1
        users = client.get("/admin/api/users", headers=admin.headers).json()["items"]
        manager_row = next(user for user in users if user["username"] == "manager")
        assert manager_row["projects"] == [{"project_id": world.project_id, "role": "manager"}]
        logged = client.get("/admin/api/requests", params={"path": managed}, headers=admin.headers).json()
        assert {row["username"] for row in logged["requests"]} == {"manager", "editor", "outsider"}

        web_sign_in(client, world, "admin")
        for page, words in (
            ("/admin", "web.sign_in"),
            ("/admin/sessions", "Native app"),
            ("/admin/users", "outsider"),
            ("/admin/projects", "Building permit"),
            ("/admin/audit", "manage.team.set"),
            ("/admin/requests", managed),
            ("/account", "admin"),
        ):
            answer = client.get(page)
            assert answer.status_code == 200 and words in answer.text, page

    counts = asyncio.run(stored())
    assert counts["audit_events"] > 0 and counts["request_log_entries"] > 0
    assert counts["sign_in_throttles"] == 1, "the failures of the one name that failed, kept by its hash"
    with GraphDatabase.driver(graph_target.uri, auth=(graph_target.user, graph_target.password)) as driver:
        records, _, _ = driver.execute_query(
            "MATCH (n) WHERE n:AuditEvent OR n:RequestLog OR n:SignInThrottle OR n:User RETURN count(n) AS n",
            database_=settings.database_name,
        )
    assert records[0]["n"] == 0, "the graph holds none of it"
