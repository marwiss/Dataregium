# This file was generated with the assistance of an AI coding tool.
"""The logbook area in PostgreSQL (ADR 0029), without a database: what an event, an entry and a throttle keep, a
value only ever an argument, a failure and its wait in one transaction, the migration's tables, and the choice of
store. ``test_postgres_store.py`` reads the module's syntax tree for statements built from strings."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from opencde import audit, requestlog
from opencde.auth import throttle
from opencde.deps import get_audit_log, get_request_log, get_sign_in_throttle
from opencde.logging import REDACTED
from opencde.main import create_app
from opencde.postgres import schema
from opencde.postgres.database import Postgres
from opencde.store.logbook import postgres as logbook
from opencde.store.logbook.audit import AuditLogRepository
from opencde.store.logbook.postgres import (
    PostgresAuditLogRepository,
    PostgresRequestLogRepository,
    PostgresSignInThrottleRepository,
    audit_log_store,
    request_log_store,
    sign_in_throttle_store,
)
from opencde.store.logbook.requests import RequestLogRepository
from opencde.store.logbook.throttle import SignInThrottleRepository
from tests.conftest import test_settings as make_settings
from tests.unit.test_postgres_store import URL, WEAK, FakePostgres

ENTRY = {
    "id": "e-1",
    "at": "2026-11-12T10:00:00.000Z",
    "method": "POST",
    "path": "/bcf/3.0/projects",
    "operation": None,
    "status": 201,
    "duration_ms": 2.5,
    "request_id": "r-1",
    "bytes_in": 3,
    "bytes_out": 4,
    "username": None,
    "client_id": "c-1",
    "ip": "127.0.0.1",
    "expires_at": "2026-11-15T10:00:00.000Z",
}


def _migration() -> str:
    return (schema.MIGRATIONS_DIR / "0006_logs.sql").read_text(encoding="utf-8")


async def test_an_event_keeps_the_details_masked_as_one_string_and_a_value_only_as_an_argument() -> None:
    fake = FakePostgres()
    audit_log = PostgresAuditLogRepository(fake)  # ty: ignore[invalid-argument-type]
    await audit.record(audit_log, WEAK, actor=WEAK, ip=WEAK, details={"username": WEAK, "secret": WEAK})
    ((statement, args),) = fake.statements
    assert statement == logbook.AUDIT_INSERT and WEAK not in statement
    assert args[1:6] == (WEAK, args[2], WEAK, None, WEAK) and args[2].endswith("Z")
    assert json.loads(args[6]) == {"username": WEAK, "secret": REDACTED}, "the details as the graph keeps them"
    await audit_log.recent(limit=5, type_prefix=WEAK, since=WEAK)
    assert fake.statements[1] == (logbook.AUDIT_RECENT, (WEAK, WEAK, 5))


async def test_a_row_comes_back_as_the_graph_gives_the_node() -> None:
    stored = {"id": "a-1", "type": "web.sign_in", "at": "x", "actor_user_id": None, "details": "{}", "ip": None}
    audit_log = PostgresAuditLogRepository(FakePostgres(lambda query, args: [stored]))  # ty: ignore[invalid-argument-type]
    assert await audit.recent(audit_log) == [{"id": "a-1", "type": "web.sign_in", "at": "x", "details": {}}]
    request_log = PostgresRequestLogRepository(FakePostgres(lambda query, args: [ENTRY]))  # ty: ignore[invalid-argument-type]
    (entry,) = await requestlog.query(request_log)
    assert "operation" not in entry and "username" not in entry and entry["status"] == 201


async def test_an_entry_keeps_what_the_middleware_measured_and_its_filters_are_arguments() -> None:
    fake = FakePostgres(lambda query, args: {"n": 7} if query == logbook.REQUESTS_COUNT else None)
    request_log = PostgresRequestLogRepository(fake)  # ty: ignore[invalid-argument-type]
    await request_log.add(dict(ENTRY))
    assert fake.statements[0] == (logbook.REQUEST_INSERT, tuple(ENTRY.values())), "the columns in the entry's order"
    await requestlog.query(request_log, client_id=WEAK, username="", path_prefix=WEAK, status=404, limit=5000)
    assert fake.statements[1] == (logbook.REQUESTS, (WEAK, None, WEAK, 404, None, None, requestlog.MAX_LIMIT))
    assert await request_log.count() == 7
    for column in ("body", "header", "token", "query"):
        assert column not in _migration().split("CREATE TABLE request_log_entries")[1].split(");")[0]


async def test_a_failure_and_its_wait_are_one_transaction_keyed_by_the_names_hash() -> None:
    fake = FakePostgres(lambda query, args: {"failures": 3} if query == logbook.THROTTLE_FAIL else None)
    throttles = PostgresSignInThrottleRepository(fake)  # ty: ignore[invalid-argument-type]
    settings = make_settings(sign_in_free_attempts=3, sign_in_delay_seconds=30)
    await throttle.record_failure(throttles, " Anna ", settings)
    assert fake.transactions == 1
    (fail, fail_args), (block, block_args) = fake.statements
    assert (fail, block) == (logbook.THROTTLE_FAIL, logbook.THROTTLE_BLOCK)
    key = fail_args[0]
    assert "anna" not in key.lower() and key == throttle._key("anna") and block_args[0] == key
    assert block_args[1] < fail_args[1], "the wait ends long before the failures are forgotten"

    early = FakePostgres(lambda query, args: {"failures": 1} if query == logbook.THROTTLE_FAIL else None)
    await throttle.record_failure(PostgresSignInThrottleRepository(early), "anna", settings)  # ty: ignore[invalid-argument-type]
    assert [query for query, _ in early.statements] == [logbook.THROTTLE_FAIL], "the first attempts cost nothing"
    await throttle.forget(PostgresSignInThrottleRepository(early), "anna")  # ty: ignore[invalid-argument-type]
    assert early.statements[-1] == (logbook.THROTTLE_FORGET, (key,))


def test_the_logbook_migration_names_by_value_and_expires_what_expires() -> None:
    text = _migration()
    assert text.startswith("-- This file was generated with the assistance of an AI coding tool.")
    assert "REFERENCES" not in text, "a log outlives the users and the clients it names"
    assert "timestamptz" not in text and "CREATE DOMAIN" not in text, "the accounts' domain serves"
    events = text.split("CREATE TABLE audit_events")[1].split(");")[0]
    assert "expires_at" not in events and "details text NOT NULL" in events, "the audit log is kept"
    for table in ("request_log_entries", "sign_in_throttles"):
        body = text.split(f"CREATE TABLE {table}")[1].split(");")[0]
        assert "expires_at iso_utc NOT NULL" in body and f"{table}_expires_at" in text, table
    assert "key text PRIMARY KEY" in text and "username" not in text.split("CREATE TABLE sign_in_throttles")[1]


def test_the_graph_holds_the_logbook_unless_postgresql_is_named() -> None:
    graph: Any = object()
    assert isinstance(audit_log_store(graph, None), AuditLogRepository)
    assert isinstance(request_log_store(graph, None), RequestLogRepository)
    assert isinstance(sign_in_throttle_store(graph, None), SignInThrottleRepository)
    postgres = Postgres(URL, "", connect_timeout_seconds=1.0)
    assert isinstance(audit_log_store(graph, postgres), PostgresAuditLogRepository)
    assert isinstance(request_log_store(graph, postgres), PostgresRequestLogRepository)
    assert isinstance(sign_in_throttle_store(graph, postgres), PostgresSignInThrottleRepository)


@pytest.mark.parametrize("statement", [logbook.AUDIT_RECENT, logbook.REQUESTS])
def test_a_prefix_is_compared_by_code_point_and_never_a_pattern(statement: str) -> None:
    assert "LIKE" not in statement and "left(" in statement and 'COLLATE "C" DESC' in statement


def test_the_routes_the_writer_and_the_mcp_endpoint_keep_the_logbook_where_the_store_is(tmp_path: Path) -> None:
    graph: Any = object()
    postgres = Postgres(URL, "", connect_timeout_seconds=1.0)
    for choose, on_graph, on_postgres in (
        (get_audit_log, AuditLogRepository, PostgresAuditLogRepository),
        (get_request_log, RequestLogRepository, PostgresRequestLogRepository),
        (get_sign_in_throttle, SignInThrottleRepository, PostgresSignInThrottleRepository),
    ):
        assert isinstance(choose(graph, None), on_graph)
        assert isinstance(choose(graph, postgres), on_postgres)
    settings = make_settings(tmp_path, store_url=URL, request_log_enabled=True)
    with TestClient(create_app(settings, startup_wait_seconds=0)) as client:
        assert isinstance(client.app.state.requestlog.store, PostgresRequestLogRepository)  # ty: ignore[unresolved-attribute]
    with TestClient(create_app(make_settings(tmp_path, request_log_enabled=True), startup_wait_seconds=0)) as client:
        assert isinstance(client.app.state.requestlog.store, RequestLogRepository)  # ty: ignore[unresolved-attribute]


async def test_an_mcp_event_goes_to_the_logbook_where_the_store_is(tmp_path: Path) -> None:
    from opencde.mcp.registry import ToolContext
    from tests.mcp_support import principal

    fake = FakePostgres()
    context = ToolContext(
        principal=principal(), settings=make_settings(tmp_path), graph=object(), tool="list_models", postgres=fake
    )  # ty: ignore[invalid-argument-type]
    await context.record("mcp.denied", reason="policy")
    ((statement, args),) = fake.statements
    assert statement == logbook.AUDIT_INSERT and args[1] == "mcp.denied" and args[3] == "u-anna"
    assert json.loads(args[6]) == {"reason": "policy", "tool": "list_models"}
