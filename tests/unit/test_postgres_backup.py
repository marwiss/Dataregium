# This file was generated with the assistance of an AI coding tool.
"""The backup with the server's own data in PostgreSQL (ADR 0029), without a database: the tables as a whole, a
graph's backup made rows, and the backup's files and manifest, over a fake PostgreSQL and recording graphs."""

from __future__ import annotations

import gzip
import json
import re
from pathlib import Path
from typing import Any

import pytest
from asyncpg.exceptions import ForeignKeyViolationError

from opencde import backup as backups
from opencde.postgres import from_graph, schema, tables
from opencde.postgres.from_graph import Gathered
from opencde.storage import LocalStorage
from tests.unit.test_postgres_store import FakePostgres
from tests.unit.test_store_split_cli import Graph, model_nodes

SHIPPED = [{"version": m.version, "name": m.name} for m in schema.migrations()]


def migration_tables() -> list[tuple[int, str, str]]:
    """``(migration, table, body)`` of every ``CREATE TABLE`` of the migrations, in their order."""
    found = []
    for migration in schema.migrations():
        text = migration.path.read_text(encoding="utf-8")
        found += [(migration.version, *match) for match in re.findall(r"CREATE TABLE (\w+) \((.*?)\n\);", text, re.S)]
    return found


def migration_columns() -> dict[str, dict[str, bool]]:
    """Each table's columns as ``tables.columns`` reads them from the catalogue: whether each must have a value."""
    found: dict[str, dict[str, bool]] = {}
    for _, table, body in migration_tables():
        for line in body.splitlines():
            words = line.split()
            if not words or words[0] in ("PRIMARY", "UNIQUE", "FOREIGN", "CHECK", "CONSTRAINT", "--"):
                continue
            found.setdefault(table, {})[words[0].strip('"')] = "NOT NULL" in line or "PRIMARY KEY" in line
    return found


def a_database(rows: dict[str, list[dict[str, Any]]], *, extra: tuple[str, ...] = ()) -> FakePostgres:
    """PostgreSQL with every table of the migrations, ``extra`` besides, every migration, and ``rows``."""
    present = [{"tablename": t.name} for t in tables.TABLES] + [{"tablename": name} for name in (tables.LEDGER, *extra)]
    statements = {table.out: table.name for table in tables.TABLES}

    def answer(query: str, args: tuple[Any, ...]) -> Any:
        if query == tables.PRESENT:
            return present
        if query == schema.LEDGER_PRESENT:
            return {"present": True}
        if query == schema.APPLIED:
            return SHIPPED
        if query in statements:
            skip, limit = args
            return [{"data": row} for row in rows.get(statements[query], [])[skip : skip + limit]]
        return None

    return FakePostgres(answer)


# -- the tables as a whole --------------------------------------------------------------------------------------


def test_the_tables_are_those_of_the_migrations_parents_first() -> None:
    made = migration_tables()
    assert [(t.since, t.name) for t in tables.TABLES] == [(version, table) for version, table, _ in made], (
        "every table, with the migration that makes it, in their order"
    )
    assert set(re.findall(r"\w+", tables.WIPE.replace("TRUNCATE", ""))) == {t.name for t in tables.TABLES}
    order = [t.name for t in tables.TABLES]
    for _, table, body in made:
        for parent in re.findall(r"REFERENCES (\w+)", body):
            assert order.index(parent) <= order.index(table), f"{table} comes after {parent}"
    for table in tables.TABLES:
        assert f" FROM {table.name} t ORDER BY " in table.out and f"NULL::{table.name}," in table.put


async def test_a_dump_reads_one_snapshot_page_by_page_and_refuses_a_table_it_does_not_know(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tables, "PAGE", 2)
    users = [{"id": f"u{n}"} for n in range(3)]
    database = a_database({"users": users, "projects": [{"project_id": "p1"}]})
    emitted: list[tuple[str, dict[str, Any]]] = []
    assert await tables.dump(database, lambda table, row: emitted.append((table, row))) == 4  # ty: ignore[invalid-argument-type]
    assert emitted == [
        ("users", users[0]),
        ("users", users[1]),
        ("users", users[2]),
        ("projects", {"project_id": "p1"}),
    ]
    assert database.transactions == 1 and database.statements[0][0].startswith("SET TRANSACTION ISOLATION LEVEL")
    assert [args for query, args in database.statements if query == tables.USERS_OUT] == [(0, 2), (2, 2)]

    with pytest.raises(ValueError, match="does not know: not_a_table"):
        await tables.dump(a_database({}, extra=("not_a_table",)), lambda table, row: None)  # ty: ignore[invalid-argument-type]


async def test_a_load_puts_rows_in_at_the_backups_migration_and_migrates_the_rest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migrated: list[int | None] = []
    had: dict[int, str] = {}

    async def migrate(postgres: Any, *, until: int | None = None) -> int:
        migrated.append(until)
        return 0

    async def applied(postgres: Any) -> dict[int, str]:
        return had

    monkeypatch.setattr(tables, "migrate", migrate)
    monkeypatch.setattr(tables, "applied", applied)
    database = FakePostgres()
    rows = [("users", {"id": "u1"}), ("users", {"id": "u2"}), ("projects", {"project_id": "p1"})]
    assert await tables.load(database, lambda: iter(rows), until=3) == 3  # ty: ignore[invalid-argument-type]
    assert migrated == [3, None], "up to the backup's migration before the rows, the rest after"
    assert database.statements == [
        (tables.USERS_IN, ([{"id": "u1"}, {"id": "u2"}],)),
        (tables.PROJECTS_IN, ([{"project_id": "p1"}],)),
    ]
    assert database.transactions == 1

    for until, given, words in (
        (8, rows, "after this server's 0007"),
        (3, [("topics", {"guid": "t"})], "does not have at 0003: 'topics'"),
        (5, [("audit_events", {})], "does not have at 0005: 'audit_events'"),
    ):
        with pytest.raises(ValueError, match=words):
            await tables.load(FakePostgres(), lambda given=given: iter(given), until=until)  # ty: ignore[invalid-argument-type]
    had.update({4: "0004_bcf"})
    with pytest.raises(ValueError, match="migrations after the backup's 0003 \\(0004\\): restore into a new database"):
        await tables.load(FakePostgres(), lambda: iter(rows), until=3)  # ty: ignore[invalid-argument-type]
    had.clear()

    def refuse(query: str, args: tuple[Any, ...]) -> Any:
        if query == tables.PROJECTS_IN:
            raise ForeignKeyViolationError("insert or update on table violates foreign key constraint")
        return None

    with pytest.raises(ValueError, match="refused the rows of projects: ForeignKeyViolationError"):
        await tables.load(FakePostgres(refuse), lambda: iter(rows))  # ty: ignore[invalid-argument-type]


# -- a graph's backup made rows ------------------------------------------------------------------------------------


def gathered(nodes: list[tuple[list[str], dict[str, Any]]], relationships: list[tuple[str, list, list]]) -> Gathered:
    found = Gathered()
    for labels, props in nodes:
        key = backups.node_key(labels, props)
        assert key is not None
        found.add_node(tuple(labels), key, props)
    for kind, a, b in relationships:
        found.add_relationship(
            {"a": a, "type": kind, "props": {"role": "editor", "since": T} if kind == "MEMBER_OF" else {}, "b": b}
        )
    return found


T = "2026-11-12T10:00:00.000Z"
USER = (["User"], {"id": "u1", "username": "anna", "password_hash": "x", "created_at": T})
PROJECT = (["Project"], {"project_id": "p1", "name": "P", "created_at": T, "topic_counter": 1})


def test_a_graphs_backup_becomes_the_rows_of_its_tables() -> None:
    version = {
        "id": "v1",
        "version_index": 1,
        "version_number": "1",
        "creation_date": T,
        "title": "Model",
        "file_name": "a.ifc",
        "size_in_bytes": 3,
        "sha256": "s",
        "storage_key": "documents/a",
        "author": "anna",
        "metadata": '[{"name": "Status", "value": ["Draft"]}]',
    }
    derived = {**version, "id": "v2", "version_index": 2, "parent_version_index": 1, "metadata": "[]"}
    nodes = [
        USER,
        PROJECT,
        (["Extensions"], {"id": "e1", "topic_type": ["Issue"], "topic_status": ["Open"]}),
        (
            ["Client"],
            {
                "client_id": "c1",
                "name": "C",
                "redirect_uris": ["http://127.0.0.1/cb"],
                "is_public": True,
                "created_at": T,
            },
        ),
        (["AccessToken"], {"token_hash": "h", "scope": "s", "family_id": "f", "created_at": T, "expires_at": T}),
        (["Document"], {"document_id": "d1", "title": "Model", "created_at": T, "latest_index": 2}),
        (["DocumentVersion"], derived),
        (["DocumentVersion"], version),
        (
            ["Topic"],
            {
                "guid": "t1",
                "title": "T",
                "server_assigned_id": "ISSUE-1",
                "creation_date": T,
                "creation_author": "anna",
            },
        ),
        (["Comment"], {"guid": "k1", "topic_guid": "t1", "date": T, "author": "anna", "comment": "Hi"}),
        (
            ["UploadSession"],
            {
                "id": "s1",
                "token_hash": "h",
                "user_id": "u1",
                "username": "anna",
                "callback_url": "u",
                "callback_expires_at": T,
                "created_at": T,
                "expires_at": T,
                "state": "open",
                "files_json": '[{"name": "a"}]',
            },
        ),
        (
            ["Conversion"],
            {
                "id": "m1",
                "model_id": "m1",
                "project_id": "p1",
                "file_name": "a.ifc",
                "storage_key": "documents/a",
                "state": "done",
                "created_at": T,
                "done_at": T,
                "entities": 12,
            },
        ),
        (
            ["Validation"],
            {
                "id": "r1",
                "storage_key": "documents/a",
                "file_name": "a.ifc",
                "state": "done",
                "created_at": T,
                "done_at": T,
                "issue_count": 0,
            },
        ),
        (
            ["Precheck", "Validation"],
            {
                "id": "r2",
                "project_id": "p1",
                "storage_key": "prechecks/x",
                "file_name": "x.ifc",
                "state": "queued",
                "created_at": T,
                "expires_at": T,
            },
        ),
    ]
    relationships = [
        ("MEMBER_OF", ["User", "id", "u1"], ["Project", "project_id", "p1"]),
        ("HAS_EXTENSIONS", ["Project", "project_id", "p1"], ["Extensions", "id", "e1"]),
        ("FOR", ["AccessToken", "token_hash", "h"], ["User", "id", "u1"]),
        ("ISSUED_TO", ["AccessToken", "token_hash", "h"], ["Client", "client_id", "c1"]),
        ("HAS_DOCUMENT", ["Project", "project_id", "p1"], ["Document", "document_id", "d1"]),
        ("HAS_VERSION", ["Document", "document_id", "d1"], ["DocumentVersion", "id", "v1"]),
        ("HAS_VERSION", ["Document", "document_id", "d1"], ["DocumentVersion", "id", "v2"]),
        ("UPLOADED_BY", ["DocumentVersion", "id", "v1"], ["User", "id", "u1"]),
        ("DERIVED_FROM", ["DocumentVersion", "id", "v2"], ["DocumentVersion", "id", "v1"]),
        ("HAS_TOPIC", ["Project", "project_id", "p1"], ["Topic", "guid", "t1"]),
        ("CREATED_BY", ["Topic", "guid", "t1"], ["User", "id", "u1"]),
        ("HAS_COMMENT", ["Topic", "guid", "t1"], ["Comment", "guid", "k1"]),
        ("HAS_CONVERSION", ["Project", "project_id", "p1"], ["Conversion", "id", "m1"]),
        ("HAS_VALIDATION", ["DocumentVersion", "id", "v1"], ["Validation", "id", "r1"]),
        ("HAS_PRECHECK", ["Project", "project_id", "p1"], ["Validation", "id", "r2"]),
    ]
    rows = gathered(nodes, relationships).rows(migration_columns())
    by_table: dict[str, list[dict[str, Any]]] = {}
    for table, row in rows:
        by_table.setdefault(table, []).append(row)
    order = [t.name for t in tables.TABLES]
    assert [table for table, _ in rows] == sorted((table for table, _ in rows), key=order.index), "parents first"

    (user,) = by_table["users"]
    assert user["is_admin"] is False and user["disabled"] is False, "what the graph's readers take for missing"
    (project,) = by_table["projects"]
    assert (
        project["extensions"] == {"topic_type": ["Issue"], "topic_status": ["Open"]} and project["ai_policy"] == "local"
    )
    assert by_table["memberships"] == [{"role": "editor", "since": T, "user_id": "u1", "project_id": "p1"}]
    (token,) = by_table["access_tokens"]
    assert (token["user_id"], token["client_id"]) == ("u1", "c1")
    first, second = by_table["document_versions"]
    assert (first["id"], second["id"]) == ("v1", "v2"), "the version derived from goes in first"
    assert first["metadata"] == [{"name": "Status", "value": ["Draft"]}] and first["uploaded_by"] == "u1"
    assert first["document_id"] == second["document_id"] == "d1" and second.get("uploaded_by") is None
    assert by_table["documents"][0]["project_id"] == "p1" and by_table["topics"][0]["project_id"] == "p1"
    assert by_table["comments"][0]["topic_guid"] == "t1"
    assert (
        by_table["upload_sessions"][0]["files"] == [{"name": "a"}]
        and "files_json" not in by_table["upload_sessions"][0]
    )
    (conversion,) = by_table["conversions"]
    assert "model_id" not in conversion and conversion["fields"] == {"entities": 12} and conversion["done_at"] == T
    check, precheck = by_table["validations"]
    assert (check["version_id"], check["project_id"], check["fields"]) == ("v1", "p1", {"done_at": T, "issue_count": 0})
    assert (precheck.get("version_id"), precheck["project_id"], precheck["expires_at"]) == (None, "p1", T)


def test_the_nodes_without_a_unique_key_are_kept_by_their_ids_and_become_rows() -> None:
    """A team with its members and responsible user, a version's publication, a viewpoint's component, a change
    set's trial, a clash check, a merge and a request's log entry: the backup keys them by the ids the server gave
    them (LD-64, SA-108), and a move to PostgreSQL makes them rows."""
    version = {
        "id": "v1",
        "version_index": 1,
        "version_number": "1",
        "creation_date": T,
        "title": "Model",
        "file_name": "a.ifc",
        "size_in_bytes": 3,
        "sha256": "s",
        "storage_key": "documents/a",
        "author": "anna",
        "metadata": "[]",
    }
    topic = {"guid": "t1", "title": "T", "server_assigned_id": "ISSUE-1", "creation_date": T, "creation_author": "anna"}
    viewpoint = {"guid": "vp1", "index": 0, "author": "anna", "creation_date": T, "camera_kind": "perspective"}
    nodes = [
        USER,
        PROJECT,
        (["Document"], {"document_id": "d1", "title": "Model", "created_at": T, "latest_index": 1}),
        (["DocumentVersion"], version),
        (["Topic"], topic),
        (["Viewpoint"], viewpoint),
        (["Team"], {"id": "team1", "name": "Facades", "ids_document_id": "d1", "created_at": T, "updated_at": T}),
        (
            ["Publication"],
            {
                "id": "pub1",
                "storage_key": "published/x.zip",
                "file_name": "x.zip",
                "size_in_bytes": 9,
                "sha256": "h",
                "published_at": T,
                "published_by": "anna",
            },
        ),
        (
            ["Component"],
            {"id": "c1", "role": "selection", "group_index": 0, "position": 0, "ifc_guid": "2O2Fr$t4X7Zf8NOew3FLOH"},
        ),
        (
            ["Proposal"],
            {
                "id": "pr1",
                "project_id": "p1",
                "topic_guid": "t1",
                "snippet_key": "snippets/k.bin",
                "author": "anna",
                "state": "clean",
                "created_at": T,
                "report": "packed",
            },
        ),
        (
            ["Clash"],
            {
                "id": "cl1",
                "project_id": "p1",
                "state": "done",
                "request": "{}",
                "author": "anna",
                "created_at": T,
                "report": "r",
            },
        ),
        (
            ["Merge"],
            {
                "id": "m1",
                "kind": "prepare",
                "state": "done",
                "request": "{}",
                "author": "anna",
                "created_at": T,
                "model_id": "x",
                "version_index": 2,
            },
        ),
        (
            ["RequestLog"],
            {
                "id": "r1",
                "at": T,
                "method": "GET",
                "path": "/health",
                "status": 200,
                "duration_ms": 1.5,
                "bytes_in": 0,
                "bytes_out": 2,
                "expires_at": T,
            },
        ),
    ]
    relationships = [
        ("HAS_DOCUMENT", ["Project", "project_id", "p1"], ["Document", "document_id", "d1"]),
        ("HAS_VERSION", ["Document", "document_id", "d1"], ["DocumentVersion", "id", "v1"]),
        ("HAS_TOPIC", ["Project", "project_id", "p1"], ["Topic", "guid", "t1"]),
        ("HAS_VIEWPOINT", ["Topic", "guid", "t1"], ["Viewpoint", "guid", "vp1"]),
        ("HAS_TEAM", ["Project", "project_id", "p1"], ["Team", "id", "team1"]),
        ("RESPONSIBLE", ["Team", "id", "team1"], ["User", "id", "u1"]),
        ("HAS_MEMBER", ["Team", "id", "team1"], ["User", "id", "u1"]),
        ("HAS_PUBLICATION", ["DocumentVersion", "id", "v1"], ["Publication", "id", "pub1"]),
        ("HAS_COMPONENT", ["Viewpoint", "guid", "vp1"], ["Component", "id", "c1"]),
        ("HAS_PROPOSAL", ["Topic", "guid", "t1"], ["Proposal", "id", "pr1"]),
        ("HAS_CLASH", ["Project", "project_id", "p1"], ["Clash", "id", "cl1"]),
        ("HAS_MERGE", ["Project", "project_id", "p1"], ["Merge", "id", "m1"]),
    ]
    rows: dict[str, list[dict[str, Any]]] = {}
    for table, row in gathered(nodes, relationships).rows(migration_columns()):
        rows.setdefault(table, []).append(row)
    assert rows["teams"] == [
        {
            "id": "team1",
            "name": "Facades",
            "ids_document_id": "d1",
            "created_at": T,
            "updated_at": T,
            "project_id": "p1",
            "responsible_user_id": "u1",
        }
    ]
    assert rows["team_members"] == [{"team_id": "team1", "user_id": "u1"}]
    assert rows["publications"][0]["version_id"] == "v1" and rows["components"][0]["viewpoint_guid"] == "vp1"
    assert rows["proposals"][0]["fields"] == {"report": "packed"} and rows["proposals"][0]["topic_guid"] == "t1"
    assert rows["clashes"][0]["fields"] == {"report": "r"}
    assert rows["merges"][0]["fields"] == {"version_index": 2} and rows["merges"][0]["project_id"] == "p1"
    assert rows["request_log_entries"][0]["path"] == "/health"


def test_what_has_no_place_in_postgresql_is_refused_all_at_once() -> None:
    nodes = [
        (["User"], {**USER[1], "favourite_colour": "green"}),
        PROJECT,
        (
            ["Topic"],
            {
                "guid": "t1",
                "title": "T",
                "server_assigned_id": "ISSUE-1",
                "creation_date": T,
                "creation_author": "anna",
            },
        ),
    ]
    relationships = [("LIKES", ["User", "id", "u1"], ["Project", "project_id", "p1"])]
    found = gathered(nodes, relationships)
    for ghost in ("g1", "g2"):  # a keyed label of a later release, which no table of this one holds
        found.add_node(("Ghost",), ("Ghost", "id", ghost), {"id": ghost})
    with pytest.raises(ValueError) as refused:
        found.rows(migration_columns())
    told = str(refused.value)
    for words in (
        "Ghost: no table in PostgreSQL holds it (2)",
        "LIKES from User to Project: a relationship with no place (1)",
        "users.favourite_colour: no column holds it (1)",
        "topics.project_id: a value it must have is missing (1)",
    ):
        assert words in told, told


def test_every_label_of_the_servers_own_data_has_its_table_or_its_reason() -> None:
    """A label of the graph's schema either becomes rows, or is named in the module's docstring as refused."""
    from opencde.graph.schema import BACKUP_ONLY, UNIQUE

    order = {t.name for t in tables.TABLES}
    assert set(from_graph.TABLE_OF.values()) <= order and set(from_graph.DEFAULTS) <= order
    columns = migration_columns()
    for table, defaults in from_graph.DEFAULTS.items():
        assert set(defaults) <= set(columns[table]), table
    for label in {label for label, _ in (*UNIQUE, *BACKUP_ONLY)} - set(from_graph.TABLE_OF) - {from_graph.EXTENSIONS}:
        assert f"``{label}``" in (from_graph.__doc__ or ""), f"{label} is neither mapped nor said to be refused"


# -- the backup's files and manifest ---------------------------------------------------------------------------------


async def test_a_backup_with_postgresql_holds_its_rows_beside_the_models(tmp_path: Path) -> None:
    ifc = Graph("ifc", model_nodes())
    database = a_database({"users": [{"id": "u1", "username": "anna"}], "topics": [{"guid": "t1"}]})
    manifest = await backups.export(ifc, LocalStorage(tmp_path / "s"), tmp_path / "b", postgres=database)  # type: ignore[arg-type]
    assert (manifest.store_dialect, manifest.rows, manifest.store_migration, manifest.nodes) == ("postgres", 2, 7, 2)
    written = json.loads((tmp_path / "b" / "manifest.json").read_text(encoding="utf-8"))
    assert written["store_dialect"] == "postgres" and written["rows"] == 2 and written["store_migration"] == 7
    assert set(written["checksums"]) == {*backups.GRAPH_FILES, backups.STORE_ROWS}
    with gzip.open(tmp_path / "b" / backups.STORE_ROWS, "rt", encoding="utf-8") as handle:
        assert [json.loads(line) for line in handle] == [
            {"table": "users", "row": {"id": "u1", "username": "anna"}},
            {"table": "topics", "row": {"guid": "t1"}},
        ]
    assert not ifc.labels() - {"Model", "Entity"}, "the IFC graph is asked for its models alone"

    alone = await backups.export(None, LocalStorage(tmp_path / "s"), tmp_path / "c", postgres=a_database({}))  # type: ignore[arg-type]
    assert (alone.nodes, alone.dialect, alone.rows) == (0, "none", 0), "no graph, no nodes"
    with pytest.raises(backups.BackupError, match="does not know: not_a_table"):
        await backups.export(
            None, LocalStorage(tmp_path / "s"), tmp_path / "d", postgres=a_database({}, extra=("not_a_table",))
        )  # type: ignore[arg-type]
    assert not (tmp_path / "d").exists(), "refused before anything is written"


async def test_a_backup_of_postgresql_restores_into_postgresql_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loaded: list[tuple[list[tuple[str, dict[str, Any]]], int | None]] = []

    async def load(postgres: Any, rows: Any, *, until: int | None = None) -> int:
        loaded.append((list(rows()), until))
        return len(loaded[-1][0])

    monkeypatch.setattr(tables, "load", load)
    database = a_database({"users": [{"id": "u1"}]})
    await backups.export(Graph("ifc", model_nodes()), LocalStorage(tmp_path / "s"), tmp_path / "b", postgres=database)  # type: ignore[arg-type]

    into = Graph("ifc")
    await backups.restore(into, LocalStorage(tmp_path / "again"), tmp_path / "b", postgres=FakePostgres())  # type: ignore[arg-type]
    assert loaded == [([("users", {"id": "u1"})], 7)]
    assert into.labels() == {"Model", "Entity", "IfcWall"}, "the models to the IFC graph"
    with pytest.raises(backups.BackupError, match="restores into PostgreSQL alone"):
        await backups.restore(Graph("ifc"), LocalStorage(tmp_path / "x"), tmp_path / "b")  # type: ignore[arg-type]
    with pytest.raises(backups.BackupError, match="2 nodes of IFC-DB's models and the server uses no graph"):
        await backups.restore(None, LocalStorage(tmp_path / "x"), tmp_path / "b", postgres=FakePostgres())  # type: ignore[arg-type]


async def test_a_graphs_backup_restores_into_postgresql_as_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loaded: list[list[tuple[str, dict[str, Any]]]] = []

    async def load(postgres: Any, rows: Any, *, until: int | None = None) -> int:
        loaded.append(list(rows()))
        return 0

    async def columns(postgres: Any) -> dict[str, dict[str, bool]]:
        return migration_columns()

    async def migrate(postgres: Any, *, until: int | None = None) -> int:
        return 0

    monkeypatch.setattr(tables, "load", load)
    monkeypatch.setattr(tables, "columns", columns)
    monkeypatch.setattr(schema, "migrate", migrate)
    one = [
        ("UNWIND labels(n) AS label", [{"label": label} for label in ("User", "Project", "Model", "Entity")]),
        ("MATCH (n:User)", [{"labels": ["User"], "props": USER[1]}]),
        ("MATCH (n:Project)", [{"labels": ["Project"], "props": PROJECT[1]}]),
        *model_nodes()[1:3],
        (
            "MATCH (a:User)-[r]->(b)",
            [_to("MEMBER_OF", "u1", ["Project"], {"project_id": "p1"}, role="editor", since=T)],
        ),
        ("MATCH (a:Project)-[r]->(b)", [_to("HAS_MODEL", "p1", ["Model"], {"id": "m1"})]),
    ]
    await backups.export(Graph("one", one), LocalStorage(tmp_path / "s"), tmp_path / "b")  # type: ignore[arg-type]

    into = Graph("ifc")
    await backups.restore(into, LocalStorage(tmp_path / "again"), tmp_path / "b", postgres=FakePostgres())  # type: ignore[arg-type]
    (rows,) = loaded
    assert [table for table, _ in rows] == ["users", "projects", "memberships"]
    assert rows[2][1] == {"role": "editor", "since": T, "user_id": "u1", "project_id": "p1"}
    assert into.labels() == {"Model", "Entity", "IfcWall"}, "the server's own nodes stay out of the IFC graph"
    assert not any("HAS_MODEL" in statement for statement in into.statements), "nor what crossed to them"


def _to(kind: str, start: str, labels: list[str], keys: dict[str, str], **props: str) -> dict[str, Any]:
    """A relationship's row as the export's statement returns it."""
    return {"a_key": start, "type": kind, "props": props, "b_labels": labels, "b_keys": keys}
