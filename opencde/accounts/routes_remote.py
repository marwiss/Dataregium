# This file was generated with the assistance of an AI coding tool.
"""A project's repository mirrored to a forge, set by its managers: an extension outside the OpenCDE specifications
(ADR 0032, F206 c-F209 c).

Off unless the server's administrator has turned it on and named the forges' hosts (F209 c). A ``manager`` of the
project connects it with the address of an empty repository at the forge and a fine-grained token for that repository
alone, after acknowledging that the project's files, with the personal data they may hold, leave the server
(``acknowledged``). The token is sealed at once, never shown again and never logged; the connection lasts until the
token's ``expires_at``. The server pushes in its cleaner's round (``opencde.repos.remote``), and the status says how
the last push went and which large files the mirror leaves out. Every change leaves an audit event.
"""

from __future__ import annotations

import asyncio
import re
from datetime import timedelta
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from opencde.accounts.routes_manage import IdPath, ProjectPrincipal, _managed, _record
from opencde.deps import AuditLogDep, PostgresDep, SettingsDep, UsersDep
from opencde.errors import CODE_VALIDATION, ApiError
from opencde.repos.remote import RemoteRefused, check_url, seal
from opencde.settings import Settings
from opencde.store.accounts.remotes import PostgresRemotes
from opencde.timeutil import now, parse_iso, to_iso

router = APIRouter(prefix="/manage/1.0", tags=["Project administration (extension)"])

DEFAULT_DAYS = 365
MOST_DAYS = 366
MIB = 1024 * 1024
_TOKEN = re.compile(r"[\x21-\x7e]{8,512}")  # printable, without spaces: as forges give them


class RemoteSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(
        min_length=12,
        max_length=500,
        description="The https address of an empty repository at a forge whose host the server names, without a "
        "sign-in, as https://github.com/owner/repository.git.",
    )
    token: SecretStr = Field(
        description="A fine-grained token for that repository alone, allowed to read and write its contents; it is "
        "never shown again."
    )
    expires_at: str | None = Field(
        None,
        max_length=40,
        description="When the token expires at the forge, at most 366 days ahead (365 days when left out); the "
        "connection goes then.",
    )
    max_file_bytes: int = Field(
        100 * MIB,
        ge=MIB,
        le=5 * 1024 * MIB,
        description="The forge's limit for a file (100 MiB at GitHub): a commit with a larger one is mirrored "
        "without it (F208 c).",
    )
    acknowledged: Literal[True] = Field(
        description="That the project's files, with the personal data they may hold, leave the server for the forge."
    )


def _remotes(settings: Settings, postgres: PostgresDep) -> PostgresRemotes:
    if not settings.git_remotes_on or postgres is None:
        raise HTTPException(404, "This server mirrors no repository to a forge")
    return PostgresRemotes(postgres)


def _status(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "url": row["url"],
        "max_file_bytes": row["max_file_bytes"],
        "connected_at": row["connected_at"],
        "expires_at": row["expires_at"],
        "status": row["status"],
        "error": row["error"],
        "pushed_at": row["pushed_at"],
        "pushed": sorted(row["pushed"] or {}),
        "left_out": row["left_out"] or [],
    }


def _expires_at(text: str | None) -> str:
    moment = now() + timedelta(days=DEFAULT_DAYS)
    if text is not None:
        try:
            moment = parse_iso(text)
        except ValueError:
            raise ApiError(400, "expires_at is an ISO 8601 moment", code=CODE_VALIDATION) from None
    if not now() < moment <= now() + timedelta(days=MOST_DAYS):
        raise ApiError(400, f"expires_at is in the next {MOST_DAYS} days", code=CODE_VALIDATION)
    return to_iso(moment)


@router.get(
    "/projects/{project_id}/remote",
    summary="Project administration (extension): the forge the project's repository is mirrored to",
)
async def project_remote(
    principal: ProjectPrincipal, project_id: IdPath, settings: SettingsDep, postgres: PostgresDep, users: UsersDep
) -> dict[str, Any]:
    remotes = _remotes(settings, postgres)
    await _managed(users, principal, project_id)
    row = await remotes.get(project_id)
    if row is None:
        raise HTTPException(404, "The project is not mirrored to a forge")
    return _status(row)


@router.put(
    "/projects/{project_id}/remote",
    summary="Project administration (extension): mirror the project's repository to a forge",
)
async def set_project_remote(
    request: Request,
    principal: ProjectPrincipal,
    project_id: IdPath,
    body: RemoteSettings,
    settings: SettingsDep,
    postgres: PostgresDep,
    users: UsersDep,
    audit_log: AuditLogDep,
) -> dict[str, Any]:
    remotes = _remotes(settings, postgres)
    await _managed(users, principal, project_id)
    token = body.token.get_secret_value()
    if not _TOKEN.fullmatch(token):
        raise ApiError(400, "The token is 8 to 512 printable characters", code=CODE_VALIDATION)
    expires_at = _expires_at(body.expires_at)
    try:
        url = await asyncio.to_thread(check_url, body.url, settings.git_remote_hosts)
    except RemoteRefused as exc:
        raise ApiError(400, str(exc), code=CODE_VALIDATION) from None
    sealed = seal(settings.secret_key.get_secret_value(), token)
    made = await remotes.set(
        project_id,
        url=url,
        sealed=sealed,
        max_file_bytes=body.max_file_bytes,
        user_id=principal.user_id,
        expires_at=expires_at,
    )
    if not made:
        raise HTTPException(404, "No such project")
    await _record(request, audit_log, principal, "remote.set", project_id=project_id, url=url, expires_at=expires_at)
    row = await remotes.get(project_id)
    if row is None:  # gone again at once: deleted by another manager
        raise HTTPException(404, "The project is not mirrored to a forge")
    return _status(row)


@router.delete(
    "/projects/{project_id}/remote",
    status_code=204,
    summary="Project administration (extension): stop mirroring the project's repository",
)
async def delete_project_remote(
    request: Request,
    principal: ProjectPrincipal,
    project_id: IdPath,
    settings: SettingsDep,
    postgres: PostgresDep,
    users: UsersDep,
    audit_log: AuditLogDep,
) -> Response:
    remotes = _remotes(settings, postgres)
    await _managed(users, principal, project_id)
    url = await remotes.delete(project_id)
    if url is None:
        raise HTTPException(404, "The project is not mirrored to a forge")
    await _record(request, audit_log, principal, "remote.delete", project_id=project_id, url=url)
    return Response(status_code=204)
