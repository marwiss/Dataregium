# This file was generated with the assistance of an AI coding tool.
"""The backup, the seed and the command line with the server's own data in PostgreSQL (ADR 0029), against real
databases: a backup of PostgreSQL restored into an emptied PostgreSQL and into a new one holds the same rows; a
graph's backup restored into PostgreSQL reads back through PostgreSQL's repositories, and what has no place there is
refused with nothing written; and the commands run against PostgreSQL alone, with no graph to answer.

``-k on_postgresql`` selects them; the graph's backup needs Neo4j beside PostgreSQL.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import pytest
from pydantic import SecretStr
from typer.testing import CliRunner

from opencde import audit
from opencde import backup as backups
from opencde import seed as seeding
from opencde.accounts.roles import Role
from opencde.auth import pkce
from opencde.cli import app
from opencde.graph.graph import Graph
from opencde.postgres import tables
from opencde.postgres.database import Postgres
from opencde.storage import LocalStorage
from opencde.store.accounts.postgres import (
    PostgresProjectRepository,
    PostgresTeamRepository,
    PostgresUserRepository,
    PostgresWebSessionRepository,
)
from opencde.store.accounts.sessions import WebSessionRepository
from opencde.store.accounts.users import UserRepository
from opencde.store.bcf.postgres import PostgresCommentRepository, PostgresTopicRepository, PostgresViewpointRepository
from opencde.store.documents.postgres import PostgresDocumentRepository
from opencde.store.jobs.conversions import ConversionRepository
from opencde.store.jobs.postgres import PostgresConversionRepository, PostgresValidationRepository
from opencde.store.jobs.validations import ValidationRepository
from opencde.store.oauth2.clients import ClientRepository
from opencde.store.oauth2.postgres import PostgresClientRepository, PostgresTokenRepository
from opencde.store.oauth2.tokens import TokenRepository
from tests.conftest import test_settings as make_settings
from tests.integration.conftest import PostgresTarget, emptied_postgres

pytestmark = pytest.mark.integration

PASSWORD = "a-long-and-strong-passphrase"
REDIRECT = "http://127.0.0.1/callback"
VERIFIER = "v" * 50
TTLS = {"code_ttl": 60, "access_ttl": 3600, "refresh_ttl": 30 * 86400, "refresh_idle": 7 * 86400}


async def rows_of(postgres: Postgres) -> dict[str, list[dict[str, Any]]]:
    """Every table's rows, as the backup reads them."""
    found: dict[str, list[dict[str, Any]]] = defaultdict(list)
    await tables.dump(postgres, lambda table, row: found[table].append(row))
    return dict(found)


async def signed_in(tokens: Any, user_id: str, client_id: str) -> Any:
    code = await tokens.issue_code(
        user_id=user_id,
        client_id=client_id,
        redirect_uri=REDIRECT,
        scope="bcf documents",
        code_challenge=pkce.challenge_for(VERIFIER),
    )
    return await tokens.redeem_code(code=code, client_id=client_id, redirect_uri=None, code_verifier=VERIFIER)


def seeded(storage: LocalStorage, **where: Any) -> Any:
    return seeding.seed(
        where.pop("graph", None),
        storage=storage,
        shared_password=PASSWORD,
        public_redirects=[REDIRECT],
        allow_localhost_redirects=True,
        **where,
    )


@pytest.fixture
async def other_database(pg: Postgres, postgres_target: PostgresTarget) -> Any:
    """A new, empty database beside the session's, with no migration: where a server moves to."""
    await pg.execute("CREATE DATABASE moved")
    parts = urlsplit(postgres_target.url)
    moved = Postgres(urlunsplit(parts._replace(path="/moved")), postgres_target.password, connect_timeout_seconds=10)
    try:
        yield moved
    finally:
        await moved.close()
        await pg.execute("DROP DATABASE moved")


async def test_on_postgresql_a_backup_restores_the_same_rows(
    pg: Postgres, other_database: Postgres, tmp_path: Path
) -> None:
    storage = LocalStorage(tmp_path / "data")
    seed = await seeded(storage, postgres=pg)
    officer = await PostgresUserRepository(pg).get_by_username("officer")
    assert officer is not None
    pair = await signed_in(PostgresTokenRepository(pg, **TTLS), officer.id, seed.public_client_id)
    project_id = seed.projects["Building permit"]
    conversions = PostgresConversionRepository(pg)
    await conversions.create(
        project_id=project_id,
        model_id="m1",
        file_name=seeding.SAMPLE_MODEL_FILE_NAME,
        storage_key=seed.sample_model_key,
        document_id=seeding.SEED_DOCUMENT_ID,
        version_index=1,
    )
    await conversions.set_state("m1", "done", entities=12, ifc_project_guid="0YvctVUKr0kugbFTf53O9L")
    await PostgresValidationRepository(pg).record(
        version_id=seed.sample_version_id,
        storage_key=seed.sample_model_key,
        file_name=seeding.SAMPLE_MODEL_FILE_NAME,
        checked={"state": "done", "issue_count": 0, "error_count": 0, "truncated": False},
    )
    await PostgresTeamRepository(pg).save(
        project_id,
        team_id=None,
        name="Inspection",
        responsible_user_id=officer.id,
        member_ids=[officer.id],
        ids_document_id=seeding.SEED_DOCUMENT_ID,
    )
    await PostgresWebSessionRepository(pg, ttl_seconds=3600, idle_seconds=600).create(officer.id, "127.0.0.1")
    before = await rows_of(pg)
    assert {"users", "memberships", "oauth_clients", "access_tokens", "document_versions", "components"} <= set(before)
    assert {"conversions", "validations", "teams", "team_members", "web_sessions", "bcf_events"} <= set(before)

    manifest = await backups.export(None, storage, tmp_path / "backup", postgres=pg)
    assert (manifest.store_dialect, manifest.store_migration, manifest.nodes) == ("postgres", 5, 0)
    assert manifest.rows == sum(len(rows) for rows in before.values())

    await tables.wipe(pg)
    assert await tables.is_empty(pg)
    await backups.restore(None, LocalStorage(tmp_path / "again"), tmp_path / "backup", postgres=pg)
    assert await rows_of(pg) == before, "the same rows, into an emptied database"
    caller = await PostgresTokenRepository(pg, **TTLS).principal(pair.access_token)
    assert caller is not None and caller.username == "officer", "a token still answers"

    await backups.restore(None, LocalStorage(tmp_path / "moved"), tmp_path / "backup", postgres=other_database)
    assert await rows_of(other_database) == before, "and into a new one, which it migrates"
    users = PostgresUserRepository(other_database)
    assert await users.authenticate("officer", PASSWORD) is not None
    assert (LocalStorage(tmp_path / "moved")).exists(seed.sample_model_key), "the files came with them"


async def test_on_postgresql_a_graphs_backup_restores_as_rows(graph: Graph, pg: Postgres, tmp_path: Path) -> None:
    """How a server moves from a graph to PostgreSQL: the graph's backup, restored into PostgreSQL, reads back
    through PostgreSQL's repositories as it read through the graph's."""
    storage = LocalStorage(tmp_path / "data")
    seed = await seeded(storage, graph=graph)
    officer = await UserRepository(graph).get_by_username("officer")
    assert officer is not None
    pair = await signed_in(TokenRepository(graph, **TTLS), officer.id, seed.public_client_id)
    secret = await ClientRepository(graph).rotate_secret(seed.confidential_client_id)
    project_id = seed.projects["Building permit"]
    await ConversionRepository(graph).create(
        project_id=project_id,
        model_id="m1",
        file_name=seeding.SAMPLE_MODEL_FILE_NAME,
        storage_key=seed.sample_model_key,
        document_id=seeding.SEED_DOCUMENT_ID,
        version_index=1,
    )
    await ConversionRepository(graph).set_state("m1", "done", entities=12)
    checks = ValidationRepository(graph)
    await checks.record(
        version_id=seed.sample_version_id,
        storage_key=seed.sample_model_key,
        file_name=seeding.SAMPLE_MODEL_FILE_NAME,
        checked={"state": "done", "issue_count": 0, "error_count": 0},
    )
    await checks.open_precheck(
        precheck_id="0d0c4f0e-0000-4000-8000-000000000001",
        project_id=project_id,
        storage_key="prechecks/x.ifc",
        file_name="x.ifc",
        author="officer",
    )
    web = await WebSessionRepository(graph, ttl_seconds=3600, idle_seconds=600).create(officer.id, "127.0.0.1")
    await backups.export(graph, storage, tmp_path / "backup")

    await backups.restore(None, LocalStorage(tmp_path / "moved"), tmp_path / "backup", postgres=pg)
    users, projects = PostgresUserRepository(pg), PostgresProjectRepository(pg)
    user = await users.authenticate("officer", PASSWORD)
    assert user is not None and user.id == officer.id, "the password's hash came along"
    assert await users.project_role(officer.id, project_id) is Role.MANAGER
    extensions = await projects.extensions(project_id)
    assert extensions is not None and "Inspection" in extensions["topic_type"] and "officer" in extensions["users"]
    clients = PostgresClientRepository(pg, allow_localhost_redirects=True)
    assert await clients.authenticate(seed.confidential_client_id, secret) is not None
    caller = await PostgresTokenRepository(pg, **TTLS).principal(pair.access_token)
    assert caller is not None and caller.user_id == officer.id and caller.client_id == seed.public_client_id
    assert await PostgresWebSessionRepository(pg, ttl_seconds=3600, idle_seconds=600).resolve(web) is not None

    version = await PostgresDocumentRepository(pg).latest(user_id=officer.id, document_id=seeding.SEED_DOCUMENT_ID)
    assert version is not None and version.version_id == seed.sample_version_id and version.metadata
    topics = PostgresTopicRepository(pg)
    topic = await topics.get(user_id=officer.id, project_id=project_id, guid=seeding.SEED_TOPIC_GUID)
    assert topic is not None and topic.labels == ["Reuse"] and topic.server_assigned_id == "ISSUE-0001"
    comment = await PostgresCommentRepository(pg).get(
        user_id=officer.id, project_id=project_id, topic_guid=seeding.SEED_TOPIC_GUID, guid=seeding.SEED_COMMENT_GUID
    )
    assert comment is not None and comment.viewpoint_guid == seeding.SEED_VIEWPOINT_GUID
    viewpoint = await PostgresViewpointRepository(pg, storage, max_image_bytes=1_000_000).get(
        user_id=officer.id, project_id=project_id, topic_guid=seeding.SEED_TOPIC_GUID, guid=seeding.SEED_VIEWPOINT_GUID
    )
    assert viewpoint is not None, "the viewpoint, without the components a graph's backup does not hold"
    conversion = await PostgresConversionRepository(pg).get("m1")
    assert conversion is not None and conversion["state"] == "done" and conversion["entities"] == 12
    kept = await pg.run("SELECT project_id, version_id, expires_at IS NOT NULL AS expires FROM validations ORDER BY 3")
    assert [(row["project_id"], row["version_id"] is not None, row["expires"]) for row in kept] == [
        (project_id, True, False),
        (project_id, False, True),
    ], "a version's check and a precheck"
    events = await pg.one("SELECT count(*) AS n FROM bcf_events WHERE project_id = $1", project_id)
    assert events and events["n"] >= 2, "the topic's and the comment's events"


async def test_on_postgresql_a_graphs_backup_with_what_has_no_place_is_refused(
    graph: Graph, pg: Postgres, tmp_path: Path
) -> None:
    storage = LocalStorage(tmp_path / "data")
    await seeded(storage, graph=graph)
    await audit.record(graph, "web.sign_in", ip="127.0.0.1")
    await backups.export(graph, storage, tmp_path / "backup")
    with pytest.raises(backups.BackupError, match=r"AuditEvent: no table in PostgreSQL holds it \(1\)"):
        await backups.restore(None, LocalStorage(tmp_path / "moved"), tmp_path / "backup", postgres=pg)
    assert await tables.is_empty(pg), "nothing written"


def test_on_postgresql_the_command_line_runs_with_no_graph(
    postgres_target: PostgresTarget, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The commands over the server's own data, the seed, the doctor, the backup and the restore, with
    ``OPENCDE_STORE_URL`` set, IFC-DB off and no graph at the graph's address."""
    asyncio.run(_emptied(postgres_target))
    settings = make_settings(
        tmp_path,
        store_url=postgres_target.url,
        store_password=SecretStr(postgres_target.password),
        graph_connect_timeout_seconds=10.0,
        ifcgraph_enabled=False,
    )
    monkeypatch.setattr("opencde.cli._settings", lambda: settings)

    def run(*command: str, code: int = 0) -> str:
        done = CliRunner().invoke(app, list(command))
        assert done.exit_code == code, done.output
        return done.output

    told = run("seed", "--password", PASSWORD)
    assert "officer" in told and "  graph " not in told, "no model without IFC-DB"
    assert "not empty" in run("seed", code=1)
    run("user", "create", "anna", "--password", PASSWORD, "--email", "anna@example.test")
    made = run("project", "create", "Bridge")
    (project_id,) = re.findall(r"project Bridge (\S+)", made)
    run("user", "grant", "anna", project_id, "editor")
    assert re.search(r"anna .* Bridge \(editor\)", run("user", "list"))
    assert "editor       anna" in run("project", "list")
    run("user", "set-password", "anna", "--password", PASSWORD + "!")
    run("user", "disable", "anna")
    created = run("client", "create", "Inspection app", "--redirect-uri", "https://app.example.test/cb")
    (client_id,) = re.findall(r"client_id (\S+)", created)
    assert "new client_secret" in run("client", "rotate-secret", client_id)
    run("client", "approve", client_id)
    assert "Inspection app" in run("client", "list")
    assert "deleted 0 self-registered" in run("client", "prune")
    assert "revoked 0 tokens" in run("revoke", "--user", "anna")
    assert "revoked 0 tokens" in run("revoke", "--client", client_id)
    run("revoke", "--all")
    assert "expired rows" in run("cleanup")
    run("ifc-db", "list")
    doctor = run("doctor")
    assert "OK      Graph database: not used" in doctor
    assert "OK      Schema of the server's own data: 5 of 5 migrations applied, the last 0005_jobs" in doctor

    backed = run("backup", str(tmp_path / "backups"))
    (folder,) = (tmp_path / "backups").iterdir()
    rows = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))["rows"]
    assert f"{rows} rows of PostgreSQL" in backed and rows > 20
    assert "not empty" in run("restore", str(folder), code=1)
    run("restore", str(folder), "--force")
    assert re.search(r"anna .* Bridge \(editor\)", run("user", "list")), "back as it was"


async def _emptied(target: PostgresTarget) -> None:
    postgres = await emptied_postgres(target)
    await postgres.close()
