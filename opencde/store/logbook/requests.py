# This file was generated with the assistance of an AI coding tool.
"""The request log in the graph: one ``RequestLog`` node per request, which the cleaner deletes once it expires.

An entry is what :mod:`opencde.requestlog` measured: when, the method, the path without its query string, the
operation, the status, the duration, the request id, the bytes in and out, the caller and the address. Never a
body, a header or a token.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from opencde.graph.graph import Graph

CREATE = """
CREATE (:RequestLog {
    id: $id, at: $at, method: $method, path: $path, operation: $operation, status: $status,
    duration_ms: $duration_ms, request_id: $request_id, bytes_in: $bytes_in, bytes_out: $bytes_out,
    username: $username, client_id: $client_id, ip: $ip, expires_at: $expires_at
})
"""
QUERY = """
MATCH (r:RequestLog)
WHERE ($client_id IS NULL OR r.client_id = $client_id)
  AND ($username IS NULL OR r.username = $username)
  AND ($path IS NULL OR r.path STARTS WITH $path)
  AND ($status IS NULL OR r.status = $status)
  AND ($since IS NULL OR r.at >= $since)
  AND ($request_id IS NULL OR r.request_id = $request_id)
RETURN properties(r) AS entry
ORDER BY r.at DESC
LIMIT $limit
"""
COUNT = "MATCH (r:RequestLog) RETURN count(r) AS n"


class RequestLogRepository:
    def __init__(self, graph: Graph) -> None:
        self.graph = graph

    async def add(self, entry: dict[str, Any]) -> None:
        await self.graph.run(CREATE, **entry)

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
        """The newest matching entries, newest first; a filter that is None matches every entry. Nothing that is
        null is in an entry."""
        rows = await self.graph.run(
            QUERY,
            client_id=client_id,
            username=username,
            path=path_prefix,
            status=status,
            since=since,
            request_id=request_id,
            limit=limit,
        )
        return [dict(row["entry"]) for row in rows]

    async def count(self) -> int:
        """Every entry kept, the expired ones the cleaner has not reached among them."""
        row = await self.graph.one(COUNT)
        return int(row["n"]) if row else 0
