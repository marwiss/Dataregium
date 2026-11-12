# This file was generated with the assistance of an AI coding tool.
"""Audit log of security-relevant events.

Sign-ins, issued codes and tokens, revocations, client registrations and
every administrative change are recorded: who, what, when, from which
address. Details are stored as a JSON string so the record is flat in every
store. Nothing secret is ever written here. The store the server's own data
is in keeps them (``opencde.store.logbook``): ``AuditEvent`` nodes in the graph,
rows of ``audit_events`` in PostgreSQL.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import structlog

from opencde.ids import new_id
from opencde.logging import get_logger, redact_secrets
from opencde.store.logbook.audit import add_in
from opencde.timeutil import now_iso

if TYPE_CHECKING:
    from opencde.graph.graph import Tx
    from opencde.store.protocols import AuditLogStore

log = get_logger(__name__)


def event(
    type: str,
    *,
    actor: str | None = None,
    client_id: str | None = None,
    ip: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The event as every store keeps it: the secrets among the details masked, the details one JSON string."""
    safe = redact_secrets(None, "audit", dict(details or {}))
    # The request id the middleware bound to the log context, so an event can be matched with the request log
    # and with a client's own log (the administration API returns it).
    request_id = structlog.contextvars.get_contextvars().get("request_id")
    return {
        "id": new_id(),
        "type": type,
        "at": now_iso(),
        "actor": actor,
        "client_id": client_id,
        "ip": ip,
        "details": json.dumps(safe, sort_keys=True, default=str),
        "request_id": request_id if isinstance(request_id, str) else None,
    }


async def record(
    audit_log: AuditLogStore,
    type: str,
    *,
    actor: str | None = None,
    client_id: str | None = None,
    ip: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    await audit_log.add(event(type, actor=actor, client_id=client_id, ip=ip, details=details))
    log.info("audit", audit_type=type, actor=actor, client_id=client_id)


async def record_in(
    tx: Tx,
    type: str,
    *,
    actor: str | None = None,
    client_id: str | None = None,
    ip: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Record inside an existing graph transaction so the event and the change commit together."""
    await add_in(tx, event(type, actor=actor, client_id=client_id, ip=ip, details=details))


async def recent(
    audit_log: AuditLogStore, limit: int = 100, type_prefix: str | None = None, since: str | None = None
) -> list[dict[str, Any]]:
    """The newest events, optionally of one type prefix and at or after ``since`` (ISO 8601 UTC)."""
    events = []
    for stored in await audit_log.recent(limit=limit, type_prefix=type_prefix, since=since):
        found = dict(stored)
        found["details"] = json.loads(found.get("details") or "{}")
        events.append(found)
    return events
