# This file was generated with the assistance of an AI coding tool.
"""A project's connection to a forge its repository is mirrored to (ADR 0032, F206 c-F209 c), in PostgreSQL.

One row per project: the address, the token as ``opencde.repos.remote.seal`` made it, until ``expires_at``, when the
cleaner deletes the row; what was pushed last, by the forge's refs, and the server's refs it was made from; how the
last attempt went, and when the next may be made.
"""

from __future__ import annotations

from typing import Any

from opencde.postgres.database import Postgres
from opencde.timeutil import now_iso

MAX_LEFT_OUT = 100  # the files a status names as left out

REMOTE_GET = """
SELECT project_id, url, max_file_bytes, connected_by, connected_at, expires_at, pushed, sources, pushed_at,
       attempt_at, status, error, left_out
FROM project_remotes WHERE project_id = $1 AND expires_at > $2
"""
#: A new address starts the mirror over; the same address with a new token goes on from what was pushed.
REMOTE_SET = """
INSERT INTO project_remotes (project_id, url, token, max_file_bytes, connected_by, connected_at, expires_at,
                             attempt_at, status)
SELECT p.project_id, $2, $3, $4, $5, $6, $7, $6, 'waiting' FROM projects p WHERE p.project_id = $1
ON CONFLICT (project_id) DO UPDATE SET
    token = EXCLUDED.token, max_file_bytes = EXCLUDED.max_file_bytes, connected_by = EXCLUDED.connected_by,
    connected_at = EXCLUDED.connected_at, expires_at = EXCLUDED.expires_at, attempt_at = EXCLUDED.attempt_at,
    status = 'waiting', error = NULL, sources = '{}',
    pushed = CASE WHEN project_remotes.url = EXCLUDED.url THEN project_remotes.pushed ELSE '{}' END,
    left_out = CASE WHEN project_remotes.url = EXCLUDED.url THEN project_remotes.left_out ELSE '[]' END,
    url = EXCLUDED.url
RETURNING project_id
"""
REMOTE_DELETE = "DELETE FROM project_remotes WHERE project_id = $1 RETURNING url"
#: The connections whose next attempt is due, the longest waiting first, with their sealed tokens.
REMOTE_DUE = """
SELECT project_id, url, token, max_file_bytes, pushed, sources, status, left_out
FROM project_remotes WHERE attempt_at <= $1 AND expires_at > $1
ORDER BY attempt_at LIMIT $2
"""
REMOTE_DONE = """
UPDATE project_remotes SET status = $3, error = $4, attempt_at = $5,
    pushed = COALESCE($6::jsonb, pushed), sources = COALESCE($7::jsonb, sources),
    left_out = COALESCE($8::jsonb, left_out), pushed_at = COALESCE($9, pushed_at)
WHERE project_id = $1 AND url = $2
"""


class PostgresRemotes:
    """The projects' connections to forges; the caller checks the address and seals the token."""

    def __init__(self, postgres: Postgres) -> None:
        self.postgres = postgres

    async def get(self, project_id: str) -> dict[str, Any] | None:
        """The connection as a manager sees it, without the token."""
        return await self.postgres.one(REMOTE_GET, project_id, now_iso())

    async def set(
        self, project_id: str, *, url: str, sealed: str, max_file_bytes: int, user_id: str, expires_at: str
    ) -> bool:
        """Connect the project, or connect it again; False for no such project."""
        made = await self.postgres.one(
            REMOTE_SET, project_id, url, sealed, max_file_bytes, user_id, now_iso(), expires_at
        )
        return made is not None

    async def delete(self, project_id: str) -> str | None:
        """The connection gone; its address, or None for none."""
        row = await self.postgres.one(REMOTE_DELETE, project_id)
        return str(row["url"]) if row else None

    async def due(self, most: int) -> list[dict[str, Any]]:
        return await self.postgres.run(REMOTE_DUE, now_iso(), most)

    async def done(
        self,
        project_id: str,
        url: str,
        *,
        status: str,
        error: str | None,
        attempt_at: str,
        pushed: dict[str, str] | None = None,
        sources: dict[str, str] | None = None,
        left_out: list[dict[str, Any]] | None = None,
    ) -> None:
        """How an attempt went; ``pushed`` and the rest only after a push, and only while the address is the same."""
        await self.postgres.execute(
            REMOTE_DONE,
            project_id,
            url,
            status,
            error,
            attempt_at,
            pushed,
            sources,
            left_out[:MAX_LEFT_OUT] if left_out is not None else None,
            now_iso() if pushed is not None else None,
        )
