# This file was generated with the assistance of an AI coding tool.
"""The store's tables as a whole (ADR 0029): each table's rows out as JSON documents and back in, for the backup;
whether the tables are empty and emptying them, for the restore and the seed.

``TABLES`` names every table the migrations make, parents before children, with the migration that makes it: rows
put back in this order meet their foreign keys (a document's versions in the order of their index, so that one comes
after the version it derives from), and a backup taken at an earlier migration puts back the tables that migration
had. A table's rows go out as ``to_jsonb`` gives them, by column name, a page at a time in the order of their key,
from one snapshot of the database, and come back through ``jsonb_populate_recordset``, so that a table holds again
what it held, whichever columns it has. A unit test checks that ``TABLES`` and ``WIPE`` name every table of the
migrations; a table they do not know is refused, never left out. The ledger of the migrations is not among them: a
restore makes its own by migrating, up to the backup's migration before the rows go in and the rest after.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from typing import Any

from opencde.postgres.database import Postgres, Tx
from opencde.postgres.schema import applied, migrate, migrations

PAGE = 1000
MAX_BATCH_BYTES = 16 * 1024 * 1024
#: The migrations' own ledger, which is not a table of the server's data.
LEDGER = "schema_migrations"


@dataclass(frozen=True)
class Table:
    name: str
    since: int  # the migration that makes it
    out: str  # its rows as JSON documents (``data``), a page at a time: $1 skipped, $2 at most
    put: str  # rows given as one JSON array of documents by column name


USERS_OUT = "SELECT to_jsonb(t) AS data FROM users t ORDER BY t.id OFFSET $1 LIMIT $2"
USERS_IN = "INSERT INTO users SELECT * FROM jsonb_populate_recordset(NULL::users, $1::jsonb)"
PROJECTS_OUT = "SELECT to_jsonb(t) AS data FROM projects t ORDER BY t.project_id OFFSET $1 LIMIT $2"
PROJECTS_IN = "INSERT INTO projects SELECT * FROM jsonb_populate_recordset(NULL::projects, $1::jsonb)"
MEMBERSHIPS_OUT = "SELECT to_jsonb(t) AS data FROM memberships t ORDER BY t.user_id, t.project_id OFFSET $1 LIMIT $2"
MEMBERSHIPS_IN = "INSERT INTO memberships SELECT * FROM jsonb_populate_recordset(NULL::memberships, $1::jsonb)"
TEAMS_OUT = "SELECT to_jsonb(t) AS data FROM teams t ORDER BY t.id OFFSET $1 LIMIT $2"
TEAMS_IN = "INSERT INTO teams SELECT * FROM jsonb_populate_recordset(NULL::teams, $1::jsonb)"
TEAM_MEMBERS_OUT = "SELECT to_jsonb(t) AS data FROM team_members t ORDER BY t.team_id, t.user_id OFFSET $1 LIMIT $2"
TEAM_MEMBERS_IN = "INSERT INTO team_members SELECT * FROM jsonb_populate_recordset(NULL::team_members, $1::jsonb)"
WEB_SESSIONS_OUT = "SELECT to_jsonb(t) AS data FROM web_sessions t ORDER BY t.id OFFSET $1 LIMIT $2"
WEB_SESSIONS_IN = "INSERT INTO web_sessions SELECT * FROM jsonb_populate_recordset(NULL::web_sessions, $1::jsonb)"
OAUTH_CLIENTS_OUT = "SELECT to_jsonb(t) AS data FROM oauth_clients t ORDER BY t.client_id OFFSET $1 LIMIT $2"
OAUTH_CLIENTS_IN = """
INSERT INTO oauth_clients SELECT * FROM jsonb_populate_recordset(NULL::oauth_clients, $1::jsonb)
"""
AUTHORIZATION_CODES_OUT = """
SELECT to_jsonb(t) AS data FROM authorization_codes t ORDER BY t.code_hash OFFSET $1 LIMIT $2
"""
AUTHORIZATION_CODES_IN = """
INSERT INTO authorization_codes SELECT * FROM jsonb_populate_recordset(NULL::authorization_codes, $1::jsonb)
"""
ACCESS_TOKENS_OUT = "SELECT to_jsonb(t) AS data FROM access_tokens t ORDER BY t.token_hash OFFSET $1 LIMIT $2"
ACCESS_TOKENS_IN = """
INSERT INTO access_tokens SELECT * FROM jsonb_populate_recordset(NULL::access_tokens, $1::jsonb)
"""
REFRESH_TOKENS_OUT = "SELECT to_jsonb(t) AS data FROM refresh_tokens t ORDER BY t.token_hash OFFSET $1 LIMIT $2"
REFRESH_TOKENS_IN = """
INSERT INTO refresh_tokens SELECT * FROM jsonb_populate_recordset(NULL::refresh_tokens, $1::jsonb)
"""
DOCUMENTS_OUT = "SELECT to_jsonb(t) AS data FROM documents t ORDER BY t.document_id OFFSET $1 LIMIT $2"
DOCUMENTS_IN = "INSERT INTO documents SELECT * FROM jsonb_populate_recordset(NULL::documents, $1::jsonb)"
DOCUMENT_VERSIONS_OUT = """
SELECT to_jsonb(t) AS data FROM document_versions t ORDER BY t.document_id, t.version_index OFFSET $1 LIMIT $2
"""
DOCUMENT_VERSIONS_IN = """
INSERT INTO document_versions SELECT * FROM jsonb_populate_recordset(NULL::document_versions, $1::jsonb)
"""
PUBLICATIONS_OUT = "SELECT to_jsonb(t) AS data FROM publications t ORDER BY t.id OFFSET $1 LIMIT $2"
PUBLICATIONS_IN = "INSERT INTO publications SELECT * FROM jsonb_populate_recordset(NULL::publications, $1::jsonb)"
UPLOAD_SESSIONS_OUT = "SELECT to_jsonb(t) AS data FROM upload_sessions t ORDER BY t.id OFFSET $1 LIMIT $2"
UPLOAD_SESSIONS_IN = """
INSERT INTO upload_sessions SELECT * FROM jsonb_populate_recordset(NULL::upload_sessions, $1::jsonb)
"""
UPLOAD_PARTS_OUT = "SELECT to_jsonb(t) AS data FROM upload_parts t ORDER BY t.id OFFSET $1 LIMIT $2"
UPLOAD_PARTS_IN = "INSERT INTO upload_parts SELECT * FROM jsonb_populate_recordset(NULL::upload_parts, $1::jsonb)"
SELECTION_SESSIONS_OUT = "SELECT to_jsonb(t) AS data FROM selection_sessions t ORDER BY t.id OFFSET $1 LIMIT $2"
SELECTION_SESSIONS_IN = """
INSERT INTO selection_sessions SELECT * FROM jsonb_populate_recordset(NULL::selection_sessions, $1::jsonb)
"""
TOPICS_OUT = "SELECT to_jsonb(t) AS data FROM topics t ORDER BY t.guid OFFSET $1 LIMIT $2"
TOPICS_IN = "INSERT INTO topics SELECT * FROM jsonb_populate_recordset(NULL::topics, $1::jsonb)"
RELATED_TOPICS_OUT = """
SELECT to_jsonb(t) AS data FROM related_topics t ORDER BY t.topic_guid, t.related_guid OFFSET $1 LIMIT $2
"""
RELATED_TOPICS_IN = """
INSERT INTO related_topics SELECT * FROM jsonb_populate_recordset(NULL::related_topics, $1::jsonb)
"""
HEADER_FILES_OUT = "SELECT to_jsonb(t) AS data FROM header_files t ORDER BY t.id OFFSET $1 LIMIT $2"
HEADER_FILES_IN = "INSERT INTO header_files SELECT * FROM jsonb_populate_recordset(NULL::header_files, $1::jsonb)"
DOCUMENT_REFERENCES_OUT = "SELECT to_jsonb(t) AS data FROM document_references t ORDER BY t.guid OFFSET $1 LIMIT $2"
DOCUMENT_REFERENCES_IN = """
INSERT INTO document_references SELECT * FROM jsonb_populate_recordset(NULL::document_references, $1::jsonb)
"""
COMMENTS_OUT = "SELECT to_jsonb(t) AS data FROM comments t ORDER BY t.guid OFFSET $1 LIMIT $2"
COMMENTS_IN = "INSERT INTO comments SELECT * FROM jsonb_populate_recordset(NULL::comments, $1::jsonb)"
VIEWPOINTS_OUT = "SELECT to_jsonb(t) AS data FROM viewpoints t ORDER BY t.guid OFFSET $1 LIMIT $2"
VIEWPOINTS_IN = "INSERT INTO viewpoints SELECT * FROM jsonb_populate_recordset(NULL::viewpoints, $1::jsonb)"
COMPONENTS_OUT = "SELECT to_jsonb(t) AS data FROM components t ORDER BY t.id OFFSET $1 LIMIT $2"
COMPONENTS_IN = "INSERT INTO components SELECT * FROM jsonb_populate_recordset(NULL::components, $1::jsonb)"
BITMAPS_OUT = "SELECT to_jsonb(t) AS data FROM bitmaps t ORDER BY t.guid OFFSET $1 LIMIT $2"
BITMAPS_IN = "INSERT INTO bitmaps SELECT * FROM jsonb_populate_recordset(NULL::bitmaps, $1::jsonb)"
BCF_DOCUMENTS_OUT = "SELECT to_jsonb(t) AS data FROM bcf_documents t ORDER BY t.guid OFFSET $1 LIMIT $2"
BCF_DOCUMENTS_IN = """
INSERT INTO bcf_documents SELECT * FROM jsonb_populate_recordset(NULL::bcf_documents, $1::jsonb)
"""
BCF_EVENTS_OUT = "SELECT to_jsonb(t) AS data FROM bcf_events t ORDER BY t.id OFFSET $1 LIMIT $2"
BCF_EVENTS_IN = "INSERT INTO bcf_events SELECT * FROM jsonb_populate_recordset(NULL::bcf_events, $1::jsonb)"
CONVERSIONS_OUT = "SELECT to_jsonb(t) AS data FROM conversions t ORDER BY t.id OFFSET $1 LIMIT $2"
CONVERSIONS_IN = "INSERT INTO conversions SELECT * FROM jsonb_populate_recordset(NULL::conversions, $1::jsonb)"
VALIDATIONS_OUT = "SELECT to_jsonb(t) AS data FROM validations t ORDER BY t.id OFFSET $1 LIMIT $2"
VALIDATIONS_IN = "INSERT INTO validations SELECT * FROM jsonb_populate_recordset(NULL::validations, $1::jsonb)"
PROPOSALS_OUT = "SELECT to_jsonb(t) AS data FROM proposals t ORDER BY t.id OFFSET $1 LIMIT $2"
IFC_COPIES_OUT = "SELECT to_jsonb(t) AS data FROM ifc_copies t ORDER BY t.copy_id OFFSET $1 LIMIT $2"
IFC_COPIES_IN = "INSERT INTO ifc_copies SELECT * FROM jsonb_populate_recordset(NULL::ifc_copies, $1::jsonb)"
PROJECT_REMOTES_OUT = "SELECT to_jsonb(t) AS data FROM project_remotes t ORDER BY t.project_id OFFSET $1 LIMIT $2"
PROJECT_REMOTES_IN = """
INSERT INTO project_remotes SELECT * FROM jsonb_populate_recordset(NULL::project_remotes, $1::jsonb)
"""
PROPOSALS_IN = "INSERT INTO proposals SELECT * FROM jsonb_populate_recordset(NULL::proposals, $1::jsonb)"
CLASHES_OUT = "SELECT to_jsonb(t) AS data FROM clashes t ORDER BY t.id OFFSET $1 LIMIT $2"
CLASHES_IN = "INSERT INTO clashes SELECT * FROM jsonb_populate_recordset(NULL::clashes, $1::jsonb)"
MERGES_OUT = "SELECT to_jsonb(t) AS data FROM merges t ORDER BY t.id OFFSET $1 LIMIT $2"
MERGES_IN = "INSERT INTO merges SELECT * FROM jsonb_populate_recordset(NULL::merges, $1::jsonb)"
CHANGE_SET_DRAFTS_OUT = """
SELECT to_jsonb(t) AS data FROM change_set_drafts t ORDER BY t.changeset_id OFFSET $1 LIMIT $2
"""
CHANGE_SET_DRAFTS_IN = """
INSERT INTO change_set_drafts SELECT * FROM jsonb_populate_recordset(NULL::change_set_drafts, $1::jsonb)
"""
AUDIT_EVENTS_OUT = "SELECT to_jsonb(t) AS data FROM audit_events t ORDER BY t.id OFFSET $1 LIMIT $2"
AUDIT_EVENTS_IN = "INSERT INTO audit_events SELECT * FROM jsonb_populate_recordset(NULL::audit_events, $1::jsonb)"
REQUEST_LOG_ENTRIES_OUT = "SELECT to_jsonb(t) AS data FROM request_log_entries t ORDER BY t.id OFFSET $1 LIMIT $2"
REQUEST_LOG_ENTRIES_IN = """
INSERT INTO request_log_entries SELECT * FROM jsonb_populate_recordset(NULL::request_log_entries, $1::jsonb)
"""
SIGN_IN_THROTTLES_OUT = "SELECT to_jsonb(t) AS data FROM sign_in_throttles t ORDER BY t.key OFFSET $1 LIMIT $2"
SIGN_IN_THROTTLES_IN = """
INSERT INTO sign_in_throttles SELECT * FROM jsonb_populate_recordset(NULL::sign_in_throttles, $1::jsonb)
"""
PROJECT_REPOS_OUT = "SELECT to_jsonb(t) AS data FROM project_repos t ORDER BY t.project_id OFFSET $1 LIMIT $2"
PROJECT_REPOS_IN = """
INSERT INTO project_repos SELECT * FROM jsonb_populate_recordset(NULL::project_repos, $1::jsonb)
"""

TABLES: tuple[Table, ...] = (
    Table("users", 1, USERS_OUT, USERS_IN),
    Table("projects", 1, PROJECTS_OUT, PROJECTS_IN),
    Table("memberships", 1, MEMBERSHIPS_OUT, MEMBERSHIPS_IN),
    Table("teams", 1, TEAMS_OUT, TEAMS_IN),
    Table("team_members", 1, TEAM_MEMBERS_OUT, TEAM_MEMBERS_IN),
    Table("web_sessions", 1, WEB_SESSIONS_OUT, WEB_SESSIONS_IN),
    Table("oauth_clients", 2, OAUTH_CLIENTS_OUT, OAUTH_CLIENTS_IN),
    Table("authorization_codes", 2, AUTHORIZATION_CODES_OUT, AUTHORIZATION_CODES_IN),
    Table("access_tokens", 2, ACCESS_TOKENS_OUT, ACCESS_TOKENS_IN),
    Table("refresh_tokens", 2, REFRESH_TOKENS_OUT, REFRESH_TOKENS_IN),
    Table("documents", 3, DOCUMENTS_OUT, DOCUMENTS_IN),
    Table("document_versions", 3, DOCUMENT_VERSIONS_OUT, DOCUMENT_VERSIONS_IN),
    Table("publications", 3, PUBLICATIONS_OUT, PUBLICATIONS_IN),
    Table("upload_sessions", 3, UPLOAD_SESSIONS_OUT, UPLOAD_SESSIONS_IN),
    Table("upload_parts", 3, UPLOAD_PARTS_OUT, UPLOAD_PARTS_IN),
    Table("selection_sessions", 3, SELECTION_SESSIONS_OUT, SELECTION_SESSIONS_IN),
    Table("topics", 4, TOPICS_OUT, TOPICS_IN),
    Table("related_topics", 4, RELATED_TOPICS_OUT, RELATED_TOPICS_IN),
    Table("header_files", 4, HEADER_FILES_OUT, HEADER_FILES_IN),
    Table("document_references", 4, DOCUMENT_REFERENCES_OUT, DOCUMENT_REFERENCES_IN),
    Table("comments", 4, COMMENTS_OUT, COMMENTS_IN),
    Table("viewpoints", 4, VIEWPOINTS_OUT, VIEWPOINTS_IN),
    Table("components", 4, COMPONENTS_OUT, COMPONENTS_IN),
    Table("bitmaps", 4, BITMAPS_OUT, BITMAPS_IN),
    Table("bcf_documents", 4, BCF_DOCUMENTS_OUT, BCF_DOCUMENTS_IN),
    Table("bcf_events", 4, BCF_EVENTS_OUT, BCF_EVENTS_IN),
    Table("conversions", 5, CONVERSIONS_OUT, CONVERSIONS_IN),
    Table("validations", 5, VALIDATIONS_OUT, VALIDATIONS_IN),
    Table("proposals", 5, PROPOSALS_OUT, PROPOSALS_IN),
    Table("clashes", 5, CLASHES_OUT, CLASHES_IN),
    Table("merges", 5, MERGES_OUT, MERGES_IN),
    Table("change_set_drafts", 5, CHANGE_SET_DRAFTS_OUT, CHANGE_SET_DRAFTS_IN),
    Table("audit_events", 6, AUDIT_EVENTS_OUT, AUDIT_EVENTS_IN),
    Table("request_log_entries", 6, REQUEST_LOG_ENTRIES_OUT, REQUEST_LOG_ENTRIES_IN),
    Table("sign_in_throttles", 6, SIGN_IN_THROTTLES_OUT, SIGN_IN_THROTTLES_IN),
    Table("project_repos", 9, PROJECT_REPOS_OUT, PROJECT_REPOS_IN),
    Table("ifc_copies", 10, IFC_COPIES_OUT, IFC_COPIES_IN),
    Table("project_remotes", 11, PROJECT_REMOTES_OUT, PROJECT_REMOTES_IN),
)
BY_NAME = {table.name: table for table in TABLES}
#: Every table at once, for ``restore --force`` and ``seed --reset``, into a database with every migration.
WIPE = """
TRUNCATE users, projects, memberships, teams, team_members, web_sessions, oauth_clients, authorization_codes,
         access_tokens, refresh_tokens, documents, document_versions, publications, upload_sessions, upload_parts,
         selection_sessions, topics, related_topics, header_files, document_references, comments, viewpoints,
         components, bitmaps, bcf_documents, bcf_events, conversions, validations, proposals, clashes, merges,
         change_set_drafts, audit_events, request_log_entries, sign_in_throttles, project_repos, ifc_copies,
         project_remotes
"""
PRESENT = "SELECT tablename FROM pg_tables WHERE schemaname = current_schema()"
COLUMNS = """
SELECT table_name, column_name, is_nullable = 'NO' AS required
FROM information_schema.columns
WHERE table_schema = current_schema()
"""
#: What a restore reports by table when PostgreSQL refuses a row: a constraint, or a value its column does not take.
REFUSED = ("IntegrityConstraintViolationError", "DataError")


async def present(postgres: Postgres | Tx) -> set[str]:
    """The tables the database has: those of the migrations it has had, and whatever else is there."""
    return {row["tablename"] for row in await postgres.run(PRESENT)}


async def unknown(postgres: Postgres | Tx) -> list[str]:
    """The tables the database has that ``TABLES`` does not name: a newer release's, or what else is there."""
    return sorted(await present(postgres) - BY_NAME.keys() - {LEDGER})


async def columns(postgres: Postgres) -> dict[str, dict[str, bool]]:
    """Each table's columns, and whether a row must have a value in it (``NOT NULL``)."""
    found: dict[str, dict[str, bool]] = {}
    for row in await postgres.run(COLUMNS):
        found.setdefault(row["table_name"], {})[row["column_name"]] = bool(row["required"])
    return found


async def is_empty(postgres: Postgres) -> bool:
    """Whether every table of the server's that the database has is empty: all of them before the first migration."""
    there = await present(postgres)
    for table in TABLES:
        statement = table.out
        if table.name in there and await postgres.run(statement, 0, 1):
            return False
    return True


async def wipe(postgres: Postgres) -> None:
    """Empty every table; the caller has migrated the database, so that it has them all."""
    await postgres.execute(WIPE)


async def dump(postgres: Postgres, emit: Callable[[str, dict[str, Any]], None]) -> int:
    """Hand ``emit`` every row of every table as ``(table, document)``, parents first, from one snapshot of the
    database; returns how many. A table ``TABLES`` does not know is refused rather than left out."""

    async def work(tx: Tx) -> int:
        await tx.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        there = await present(tx)
        strange = sorted(there - BY_NAME.keys() - {LEDGER})
        if strange:
            raise ValueError(f"the database has tables this server does not know: {', '.join(strange)}")
        count = 0
        for table in TABLES:
            statement = table.out
            skip = 0
            while table.name in there:
                rows = await tx.run(statement, skip, PAGE)
                for row in rows:
                    emit(table.name, row["data"])
                count += len(rows)
                if len(rows) < PAGE:
                    break
                skip += PAGE
        return count

    return await postgres.write(work)


async def load(
    postgres: Postgres, rows: Callable[[], Iterable[tuple[str, dict[str, Any]]]], *, until: int | None = None
) -> int:
    """Put ``rows`` (``(table, document)``, parents first) into the empty tables of a database that has no migration
    after ``until``, the backup's (every one this server ships when None): the database is migrated up to ``until``,
    the rows go in, in one transaction, and the later migrations then run over them as over any database. ``rows``
    is called again when PostgreSQL runs the transaction anew. Returns how many went in."""
    shipped = len(migrations())
    version = shipped if until is None else until
    if version > shipped:
        raise ValueError(f"the backup was taken at migration {version:04d}, after this server's {shipped:04d}")
    later = sorted(number for number in await applied(postgres) if number > version)
    if later:
        named = ", ".join(f"{number:04d}" for number in later)
        raise ValueError(
            f"the database has migrations after the backup's {version:04d} ({named}): restore into a new database"
        )
    await migrate(postgres, until=version)

    async def work(tx: Tx) -> int:
        count = 0
        for table, batch in _batches(rows(), version):
            statement = table.put
            try:
                await tx.execute(statement, batch)
            except Exception as exc:
                if not any(kind.__name__ in REFUSED for kind in type(exc).__mro__):
                    raise
                raise ValueError(f"PostgreSQL refused the rows of {table.name}: {type(exc).__name__}") from None
            count += len(batch)
        return count

    count = await postgres.write(work)
    await migrate(postgres)
    return count


def _batches(rows: Iterable[tuple[str, dict[str, Any]]], version: int) -> Iterator[tuple[Table, list[dict[str, Any]]]]:
    """The rows in batches of one table each, of at most ``PAGE`` rows and about ``MAX_BATCH_BYTES``, in their
    order; a table this server does not know at ``version`` is refused."""
    current: Table | None = None
    batch: list[dict[str, Any]] = []
    size = 0
    for name, row in rows:
        table = BY_NAME.get(name)
        if table is None or table.since > version:
            raise ValueError(f"the backup holds rows of a table this server does not have at {version:04d}: {name!r}")
        if current is not None and batch and (table is not current or len(batch) >= PAGE or size >= MAX_BATCH_BYTES):
            yield current, batch
            batch, size = [], 0
        current = table
        batch.append(row)
        size += len(json.dumps(row, separators=(",", ":")))
    if current is not None and batch:
        yield current, batch
