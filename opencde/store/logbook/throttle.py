# This file was generated with the assistance of an AI coding tool.
"""The sign-in throttle's failures in the graph: one ``SignInThrottle`` node per user name, which the cleaner
deletes once it expires.

A node is keyed by the hash of the name, never the name (``opencde.auth.throttle`` makes the key and decides the
wait), and holds the failures in a row, until when the name waits, and when it is forgotten.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from opencde.graph.graph import Graph, Tx

UNTIL = "MATCH (t:SignInThrottle {key: $key}) RETURN t.blocked_until AS until"
FAIL = """
MERGE (t:SignInThrottle {key: $key})
SET t.failures = coalesce(t.failures, 0) + 1, t.expires_at = $expires
RETURN t.failures AS failures
"""
BLOCK = "MATCH (t:SignInThrottle {key: $key}) SET t.blocked_until = $until"
FORGET = "MATCH (t:SignInThrottle {key: $key}) DETACH DELETE t"


class SignInThrottleRepository:
    def __init__(self, graph: Graph) -> None:
        self.graph = graph

    async def blocked_until(self, key: str) -> str | None:
        """Until when the name waits, or None when it never had to."""
        row = await self.graph.one(UNTIL, key=key)
        return row["until"] if row else None

    async def fail(self, key: str, *, forget_at: str, until: Callable[[int], str | None]) -> None:
        """One more failure in a row, forgotten at ``forget_at``; the name then waits until what ``until`` makes of
        the failures, when it makes a moment. One transaction, so that failures at once each count."""

        async def work(tx: Tx) -> None:
            row = await tx.one(FAIL, key=key, expires=forget_at)
            moment = until(row["failures"] if row else 1)
            if moment:
                await tx.run(BLOCK, key=key, until=moment)

        await self.graph.write(work)

    async def forget(self, key: str) -> None:
        await self.graph.run(FORGET, key=key)
