# This file was generated with the assistance of an AI coding tool.
"""The audit log in the graph: one ``AuditEvent`` node per event, a flat node on every graph product.

An event is what :func:`opencde.audit.event` made of it: its id, type and moment, the actor, the client, the address,
the request id, and the details as one JSON string with the secrets masked. Events do not expire.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from opencde.graph.graph import Graph, Tx

CREATE = """
CREATE (e:AuditEvent {
    id: $id, type: $type, at: $at, actor_user_id: $actor, client_id: $client_id, ip: $ip, details: $details,
    request_id: $request_id
})
"""
RECENT = """
MATCH (e:AuditEvent)
WHERE ($prefix IS NULL OR e.type STARTS WITH $prefix) AND ($since IS NULL OR e.at >= $since)
RETURN properties(e) AS event
ORDER BY e.at DESC
LIMIT $limit
"""


class AuditLogRepository:
    def __init__(self, graph: Graph) -> None:
        self.graph = graph

    async def add(self, event: dict[str, Any]) -> None:
        await self.graph.run(CREATE, **event)

    async def recent(
        self, *, limit: int, type_prefix: str | None = None, since: str | None = None
    ) -> list[dict[str, Any]]:
        """The newest events, of one type prefix and at or after ``since`` when given, as stored: the actor as
        ``actor_user_id``, the details still their JSON string, and nothing that is null."""
        rows = await self.graph.run(RECENT, prefix=type_prefix, since=since, limit=limit)
        return [dict(row["event"]) for row in rows]


async def add_in(tx: Tx, event: dict[str, Any]) -> None:
    """The event inside the graph's transaction of a change, so that the two commit together."""
    await tx.run(CREATE, **event)
