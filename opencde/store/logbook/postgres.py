# This file was generated with the assistance of an AI coding tool.
"""The logbook area in PostgreSQL (ADR 0029): the audit log, the request log and the sign-in throttle's failures.

The same protocols as the graph's repositories (``opencde.store.protocols``), over the tables of
``opencde/postgres/migrations/0006_logs.sql``, with the same rules and answers: what is written is
``opencde.audit``'s, ``opencde.requestlog``'s and ``opencde.auth.throttle``'s, an event's details the JSON string
it made with the secrets masked, an entry never a body, a header or a token, a throttle keyed by the hash of the
name. They name users and clients by value only, as the graph does, so that a log outlives what it names. A row comes
back as the graph gives the node, without what is null. Every statement is one string literal with numbered
parameters; text is compared and ordered by code point (``COLLATE "C"``), as the graphs do, and a prefix is
``left(…)``, so that no character of it is a wildcard. The request log and the throttle expire in the cleaner's round
(``opencde.postgres.cleanup``); the audit log is kept.

``audit_log_store``, ``request_log_store`` and ``sign_in_throttle_store`` choose between the two: PostgreSQL when
``OPENCDE_STORE_URL`` names it, the graph otherwise.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from opencde.store.logbook.audit import AuditLogRepository
from opencde.store.logbook.requests import RequestLogRepository
from opencde.store.logbook.throttle import SignInThrottleRepository

if TYPE_CHECKING:
    from opencde.graph.graph import Graph
    from opencde.postgres.database import Postgres, Tx
    from opencde.store.protocols import AuditLogStore, RequestLogStore, SignInThrottleStore


def _row(row: dict[str, Any]) -> dict[str, Any]:
    """A row as the graph gives the node: nothing that is null."""
    return {name: value for name, value in row.items() if value is not None}


# -- the audit log -----------------------------------------------------------------------------------------------

AUDIT_INSERT = """
INSERT INTO audit_events (id, type, at, actor_user_id, client_id, ip, details, request_id)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
"""
AUDIT_RECENT = """
SELECT e.id, e.type, e.at, e.actor_user_id, e.client_id, e.ip, e.details, e.request_id
FROM audit_events e
WHERE ($1::text IS NULL OR left(e.type, length($1)) = $1) AND ($2::text IS NULL OR e.at >= $2)
ORDER BY e.at COLLATE "C" DESC
LIMIT $3
"""


def _event(event: dict[str, Any]) -> tuple[Any, ...]:
    return (
        event["id"],
        event["type"],
        event["at"],
        event["actor"],
        event["client_id"],
        event["ip"],
        event["details"],
        event["request_id"],
    )


class PostgresAuditLogRepository:
    def __init__(self, postgres: Postgres) -> None:
        self.postgres = postgres

    async def add(self, event: dict[str, Any]) -> None:
        await self.postgres.execute(AUDIT_INSERT, *_event(event))

    async def recent(
        self, *, limit: int, type_prefix: str | None = None, since: str | None = None
    ) -> list[dict[str, Any]]:
        """The newest events, of one type prefix and at or after ``since`` when given, as the graph gives them."""
        return [_row(row) for row in await self.postgres.run(AUDIT_RECENT, type_prefix, since, limit)]


async def add_in(tx: Tx, event: dict[str, Any]) -> None:
    """The event inside PostgreSQL's transaction of a change, so that the two commit together."""
    await tx.execute(AUDIT_INSERT, *_event(event))


# -- the request log ---------------------------------------------------------------------------------------------

REQUEST_INSERT = """
INSERT INTO request_log_entries (
    id, at, method, path, operation, status, duration_ms, request_id, bytes_in, bytes_out, username, client_id, ip,
    expires_at
)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
"""
REQUESTS = """
SELECT r.id, r.at, r.method, r.path, r.operation, r.status, r.duration_ms, r.request_id, r.bytes_in, r.bytes_out,
       r.username, r.client_id, r.ip, r.expires_at
FROM request_log_entries r
WHERE ($1::text IS NULL OR r.client_id = $1)
  AND ($2::text IS NULL OR r.username = $2)
  AND ($3::text IS NULL OR left(r.path, length($3)) = $3)
  AND ($4::integer IS NULL OR r.status = $4)
  AND ($5::text IS NULL OR r.at >= $5)
  AND ($6::text IS NULL OR r.request_id = $6)
ORDER BY r.at COLLATE "C" DESC
LIMIT $7
"""
REQUESTS_COUNT = "SELECT count(*) AS n FROM request_log_entries"


class PostgresRequestLogRepository:
    def __init__(self, postgres: Postgres) -> None:
        self.postgres = postgres

    async def add(self, entry: dict[str, Any]) -> None:
        await self.postgres.execute(
            REQUEST_INSERT,
            entry["id"],
            entry["at"],
            entry["method"],
            entry["path"],
            entry["operation"],
            entry["status"],
            entry["duration_ms"],
            entry["request_id"],
            entry["bytes_in"],
            entry["bytes_out"],
            entry["username"],
            entry["client_id"],
            entry["ip"],
            entry["expires_at"],
        )

    async def query(
        self,
        *,
        client_id: str | None,
        username: str | None,
        path_prefix: str | None,
        status: int | None,
        since: str | None,
        request_id: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        """The newest matching entries, newest first, as the graph gives them; a filter that is None matches every
        entry."""
        rows = await self.postgres.run(REQUESTS, client_id, username, path_prefix, status, since, request_id, limit)
        return [_row(row) for row in rows]

    async def count(self) -> int:
        """Every entry kept, the expired ones the cleaner has not reached among them."""
        row = await self.postgres.one(REQUESTS_COUNT)
        return int(row["n"]) if row else 0


# -- the sign-in throttle ----------------------------------------------------------------------------------------

THROTTLE_UNTIL = "SELECT blocked_until FROM sign_in_throttles WHERE key = $1"
#: One more failure in a row; the row stays locked until the transaction ends, so that failures at once each count.
THROTTLE_FAIL = """
INSERT INTO sign_in_throttles AS t (key, failures, expires_at) VALUES ($1, 1, $2)
ON CONFLICT (key) DO UPDATE SET failures = t.failures + 1, expires_at = EXCLUDED.expires_at
RETURNING t.failures
"""
THROTTLE_BLOCK = "UPDATE sign_in_throttles SET blocked_until = $2 WHERE key = $1"
THROTTLE_FORGET = "DELETE FROM sign_in_throttles WHERE key = $1"


class PostgresSignInThrottleRepository:
    def __init__(self, postgres: Postgres) -> None:
        self.postgres = postgres

    async def blocked_until(self, key: str) -> str | None:
        """Until when the name waits, or None when it never had to."""
        row = await self.postgres.one(THROTTLE_UNTIL, key)
        return row["blocked_until"] if row else None

    async def fail(self, key: str, *, forget_at: str, until: Callable[[int], str | None]) -> None:
        """One more failure in a row, forgotten at ``forget_at``; the name then waits until what ``until`` makes of
        the failures, when it makes a moment. One transaction, as the graph's."""

        async def work(tx: Tx) -> None:
            row = await tx.one(THROTTLE_FAIL, key, forget_at)
            moment = until(int(row["failures"]) if row else 1)
            if moment:
                await tx.execute(THROTTLE_BLOCK, key, moment)

        await self.postgres.write(work)

    async def forget(self, key: str) -> None:
        await self.postgres.execute(THROTTLE_FORGET, key)


# -- where the logbook is ---------------------------------------------------------------------------------------


def audit_log_store(graph: Graph, postgres: Postgres | None) -> AuditLogStore:
    return PostgresAuditLogRepository(postgres) if postgres is not None else AuditLogRepository(graph)


def request_log_store(graph: Graph, postgres: Postgres | None) -> RequestLogStore:
    return PostgresRequestLogRepository(postgres) if postgres is not None else RequestLogRepository(graph)


def sign_in_throttle_store(graph: Graph, postgres: Postgres | None) -> SignInThrottleStore:
    return PostgresSignInThrottleRepository(postgres) if postgres is not None else SignInThrottleRepository(graph)
