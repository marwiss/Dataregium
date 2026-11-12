# This file was generated with the assistance of an AI coding tool.
"""A manager's role and a team's IDS document are found through the accounts' and the documents' protocols, in
whichever store holds them (ADR 0029), without a database."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from opencde.accounts.roles import Role
from opencde.accounts.routes_manage import _managed
from opencde.accounts.routes_teams import TeamIn, _checked
from opencde.store.oauth2.tokens import Principal

PROJECT = "6f3c2b5e-8d1a-4c2e-9f7b-1a2b3c4d5e6f"
IDS = "0b7a4e52-3c1d-4f6e-8a9b-2c3d4e5f6a7b"
CALLER = Principal(
    user_id="u-m",
    username="manager",
    display_name="Manager",
    is_admin=False,
    client_id="c-1",
    scopes=("project",),
    token_hash="h",
)


class Users:
    def __init__(self, role: Role | None) -> None:
        self.role = role
        self.asked: list[tuple[str, str]] = []

    async def project_role(self, user_id: str, project_id: str) -> Role | None:
        self.asked.append((user_id, project_id))
        return self.role


class Documents:
    def __init__(self, project_id: str, file_name: str) -> None:
        self.latest_version = SimpleNamespace(project_id=project_id, file_name=file_name)
        self.asked: list[dict[str, Any]] = []

    async def latest(self, **asked: Any) -> Any:
        self.asked.append(asked)
        return self.latest_version if asked["document_id"] == IDS else None


class Projects:
    async def members(self, project_id: str) -> list[tuple[Any, Role]]:
        return [(SimpleNamespace(id="u-e", username="editor"), Role.EDITOR)]


async def test_the_managers_role_is_the_users_own_in_the_project() -> None:
    users = Users(Role.MANAGER)
    await _managed(users, CALLER, PROJECT)  # ty: ignore[invalid-argument-type]
    assert users.asked == [("u-m", PROJECT)], "from the caller's own membership"
    for role, status in ((Role.EDITOR, 403), (None, 404)):
        with pytest.raises(HTTPException) as refused:
            await _managed(Users(role), CALLER, PROJECT)  # ty: ignore[invalid-argument-type]
        assert refused.value.status_code == status


@pytest.mark.parametrize(
    ("project_id", "file_name", "document_id", "taken"),
    [
        (PROJECT, "Walls.IDS", IDS, True),
        (PROJECT, "model.ifc", IDS, False),  # not an IDS file
        ("another-project", "walls.ids", IDS, False),  # an IDS document of another project of the caller's
        (PROJECT, "walls.ids", "1b7a4e52-3c1d-4f6e-8a9b-2c3d4e5f6a7b", False),  # none the caller sees
    ],
)
async def test_a_teams_ids_document_is_the_latest_version_the_caller_sees_in_the_project(
    project_id: str, file_name: str, document_id: str, taken: bool
) -> None:
    documents = Documents(project_id, file_name)
    body = TeamIn(name="Walls", responsible="editor", members=[], ids_document_id=document_id)
    if taken:
        fields = await _checked(documents, Projects(), CALLER, PROJECT, body)  # ty: ignore[invalid-argument-type]
        assert fields["ids_document_id"] == IDS and fields["responsible_user_id"] == "u-e"
    else:
        with pytest.raises(HTTPException, match="names no IDS document of the project"):
            await _checked(documents, Projects(), CALLER, PROJECT, body)  # ty: ignore[invalid-argument-type]
    assert documents.asked == [{"user_id": "u-m", "document_id": document_id}], "a member's read"
