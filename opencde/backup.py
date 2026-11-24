# This file was generated with the assistance of an AI coding tool.
"""Backup and restore at the application level.

Neo4j Community can only dump a database that is stopped, and Memgraph has
its own snapshot format, so the server exports its own data: every node and
relationship as JSON lines (identified by the unique keys of the schema, and the
nodes without a constraint by the ids the server gave them, ``BACKUP_ONLY``; never
by database-internal ids) plus a tar archive of the storage directory. The
result restores into either database. See ADR 0006.

When the server's own data has a graph of its own (``OPENCDE_STORE_GRAPH_URI``,
ADR 0027), one backup holds both graphs: the store's nodes and IFC-DB's from
the IFC graph, in the same files. A restore puts each node where it belongs by
its label, so a backup of one database restores into two and the other way round.

When the server's own data is in PostgreSQL (``OPENCDE_STORE_URL``, ADR 0029), the two files hold IFC-DB's nodes
alone, and the store's rows are beside them in ``store.jsonl.gz``: one JSON line per row, ``{"table": ..., "row":
{...}}``, table after table in the order of their foreign keys (``opencde.postgres.tables``), all of them from one
snapshot. The manifest names ``store_dialect`` ``postgres``, the rows, and the last migration the database had
(``store_migration``). Such a backup restores into PostgreSQL alone, into empty tables: migrated up to the backup's
migration before the rows go in, and the rest after. A graph's backup restores into PostgreSQL too, its nodes and
relationships made rows (``opencde.postgres.from_graph``), which refuses whatever has no place there: that is how a
server moves. A backup of PostgreSQL does not restore into a graph. Without IFC-DB a server with PostgreSQL uses no
graph, and its backups hold no nodes.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import re
import tarfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ifc_db.graph import UNIQUE as LIBRARY_UNIQUE

from opencde import __version__
from opencde.graph.graph import Graph
from opencde.graph.schema import BACKUP_ONLY, KEYED
from opencde.storage import CATEGORIES, LocalStorage
from opencde.timeutil import now_iso

if TYPE_CHECKING:
    from opencde.postgres.database import Postgres
    from opencde.postgres.from_graph import Gathered

FORMAT = 1
BATCH = 1000
MAX_BATCH_BYTES = 16 * 1024 * 1024
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
#: What the backup keys a node by: a label's unique key, or the id of a node without one.
BACKUP_KEYED: tuple[tuple[str, str], ...] = (*KEYED, *BACKUP_ONLY)
_KEY_PROPS = sorted({prop for _, prop in BACKUP_KEYED})
_KEY_PROJECTION = "{" + ", ".join(f".{prop}" for prop in _KEY_PROPS) + "}"
#: The labels of IFC-DB's nodes, which live in the IFC graph; every other node is the server's own (ADR 0027).
MODEL_LABELS = frozenset(label for label, _ in LIBRARY_UNIQUE)
POSTGRES = "postgres"
#: The store's rows, when the server's own data is in PostgreSQL.
STORE_ROWS = "store.jsonl.gz"
GRAPH_FILES = ("nodes.jsonl.gz", "relationships.jsonl.gz", "storage.tar.gz")


def of_the_models(labels: list[str] | tuple[str, ...]) -> bool:
    """Whether a node with these labels belongs in the IFC graph rather than the store."""
    return any(label in MODEL_LABELS for label in labels)


class BackupError(RuntimeError):
    pass


@dataclass(frozen=True)
class Manifest:
    created_at: str
    server_version: str
    dialect: str
    nodes: int
    relationships: int
    skipped_nodes: int
    files: int
    checksums: dict[str, str]
    store_dialect: str | None = None  # the store's, when the server's own data had a graph of its own or PostgreSQL
    rows: int | None = None  # the store's rows, when it was PostgreSQL
    store_migration: int | None = None  # and the last migration it had

    def to_json(self) -> dict[str, Any]:
        optional = ("store_dialect", "rows", "store_migration")
        fields = {key: value for key, value in self.__dict__.items() if key not in optional or value is not None}
        return {"format": FORMAT, **fields}


def node_key(labels: list[str], props: dict[str, Any]) -> tuple[str, str, Any] | None:
    """``(label, property, value)`` identifying a node across databases, or None when it has no key."""
    for label, prop in BACKUP_KEYED:
        if label in labels and props.get(prop) is not None:
            return label, prop, props[prop]
    return None


def _safe(name: str) -> str:
    if not _NAME.match(name):
        raise BackupError(f"unsafe label or relationship type in backup: {name!r}")
    return name


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


# ---------------------------------------------------------------------------- export


async def _present_labels(graph: Graph) -> set[str]:
    rows = await graph.run("MATCH (n) UNWIND labels(n) AS label RETURN DISTINCT label")
    return {row["label"] for row in rows}


async def export(
    graph: Graph | None,
    storage: LocalStorage,
    target_dir: Path,
    *,
    store: Graph | None = None,
    postgres: Postgres | None = None,
) -> Manifest:
    """Write ``nodes.jsonl.gz``, ``relationships.jsonl.gz``, ``storage.tar.gz`` and ``manifest.json``: from the IFC
    graph ``graph``, and from the ``store`` when it is another graph, IFC-DB's nodes from the one and the server's
    own from the other. With ``postgres`` the server's own data is its rows, in ``store.jsonl.gz``, and only IFC-DB's
    nodes come from ``graph``, which is None when the server uses no graph."""
    from opencde.postgres import tables
    from opencde.postgres.schema import applied

    if postgres is not None and (strange := await tables.unknown(postgres)):
        raise BackupError(f"PostgreSQL has tables this server does not know: {', '.join(strange)}")
    apart = store is not None and store is not graph
    if postgres is not None:
        parts = [] if graph is None else [(graph, [k for k in BACKUP_KEYED if k[0] in MODEL_LABELS])]
    elif graph is None:
        raise BackupError("a backup of a graph needs the graph")
    elif apart and store is not None:  # each graph its own kind of node, so that nothing is written twice
        parts = [
            (store, [k for k in BACKUP_KEYED if k[0] not in MODEL_LABELS]),
            (graph, [k for k in BACKUP_KEYED if k[0] in MODEL_LABELS]),
        ]
    else:
        parts = [(graph, list(BACKUP_KEYED))]
    # A command-line operation: nothing else runs on this event loop, so blocking file work is fine here.
    target_dir.mkdir(parents=True, exist_ok=False)  # noqa: ASYNC240
    nodes = relationships = skipped = 0
    with (
        gzip.open(target_dir / "nodes.jsonl.gz", "wt", encoding="utf-8") as nodes_out,
        gzip.open(target_dir / "relationships.jsonl.gz", "wt", encoding="utf-8") as relationships_out,
    ):
        for source, keyed in parts:
            counted = await _export_graph(source, keyed, nodes_out, relationships_out)
            nodes, relationships, skipped = nodes + counted[0], relationships + counted[1], skipped + counted[2]

    rows = migration = None
    if postgres is not None:
        migration = max(await applied(postgres), default=0)
        with gzip.open(target_dir / STORE_ROWS, "wt", encoding="utf-8") as rows_out:

            def write_row(table: str, row: dict[str, Any]) -> None:
                rows_out.write(json.dumps({"table": table, "row": row}) + "\n")

            rows = await tables.dump(postgres, write_row)

    files = 0
    with tarfile.open(target_dir / "storage.tar.gz", "w:gz") as archive:
        for category in sorted(CATEGORIES):
            for key in storage.iter_keys(category):
                archive.add(storage.path(key), arcname=key)
                files += 1

    names = (*GRAPH_FILES, STORE_ROWS) if postgres is not None else GRAPH_FILES
    checksums = {name: _sha256(target_dir / name) for name in names}
    if postgres is not None:
        store_dialect = POSTGRES
    else:
        store_dialect = store.dialect.name.value if apart and store is not None else None
    manifest = Manifest(
        created_at=now_iso(),
        server_version=__version__,
        dialect=graph.dialect.name.value if graph is not None else "none",
        nodes=nodes,
        relationships=relationships,
        skipped_nodes=skipped,
        files=files,
        checksums=checksums,
        store_dialect=store_dialect,
        rows=rows,
        store_migration=migration,
    )
    (target_dir / "manifest.json").write_text(json.dumps(manifest.to_json(), indent=2), encoding="utf-8")
    return manifest


async def _export_graph(
    graph: Graph, keyed: list[tuple[str, str]], nodes_out: Any, relationships_out: Any
) -> tuple[int, int, int]:
    """The nodes of the ``keyed`` labels that ``graph`` has and the relationships that start at them; returns how many
    nodes and relationships were written, and how many nodes of the graph no keyed label covers."""
    present = await _present_labels(graph)
    primary = [(label, prop) for label, prop in keyed if label in present]
    covered = {label for label, _ in primary}
    nodes = relationships = skipped = 0
    seen: set[tuple[str, str, Any]] = set()

    for label, prop in primary:
        skip = 0
        while True:
            rows = await graph.run(
                f"MATCH (n:{_safe(label)}) WHERE n.{_safe(prop)} IS NOT NULL "
                f"RETURN labels(n) AS labels, properties(n) AS props ORDER BY n.{_safe(prop)} SKIP $skip LIMIT $batch",
                skip=skip,
                batch=BATCH,
            )
            for row in rows:
                key = node_key(row["labels"], row["props"])
                if key is None or key in seen:
                    continue  # a node with two unique labels is exported once, under the first
                seen.add(key)
                nodes_out.write(json.dumps({"labels": sorted(row["labels"]), "props": row["props"]}) + "\n")
                nodes += 1
            if len(rows) < BATCH:
                break
            skip += BATCH
    uncovered = present - covered
    if uncovered:
        row = await graph.one(
            "MATCH (n) WHERE NOT any(label IN labels(n) WHERE label IN $covered) RETURN count(n) AS n",
            covered=sorted(covered),
        )
        skipped = int(row["n"]) if row else 0

    first: dict[str, str] = {}  # a label's first key names its nodes; a second (a user's username) would repeat them
    for label, prop in primary:
        first.setdefault(label, prop)
    for label, prop in first.items():
        skip = 0
        while True:
            rows = await graph.run(
                f"MATCH (a:{_safe(label)})-[r]->(b) WHERE a.{_safe(prop)} IS NOT NULL "
                f"RETURN a.{_safe(prop)} AS a_key, type(r) AS type, properties(r) AS props, "
                f"labels(b) AS b_labels, b {_KEY_PROJECTION} AS b_keys "
                f"ORDER BY a.{_safe(prop)} SKIP $skip LIMIT $batch",
                skip=skip,
                batch=BATCH,
            )
            for row in rows:
                b_key = node_key(row["b_labels"], row["b_keys"])
                if b_key is None:
                    continue
                a_key = (label, prop, row["a_key"])
                if node_key([label], {prop: row["a_key"]}) != a_key:
                    continue  # the start node is exported under another label; it appears there
                relationships_out.write(
                    json.dumps(
                        {
                            "a": [label, prop, row["a_key"]],
                            "type": row["type"],
                            "props": row["props"] or {},
                            "b": list(b_key),
                        }
                    )
                    + "\n"
                )
                relationships += 1
            if len(rows) < BATCH:
                break
            skip += BATCH
    return nodes, relationships, skipped


# ---------------------------------------------------------------------------- restore


def read_manifest(source_dir: Path) -> Manifest:
    path = source_dir / "manifest.json"
    if not path.is_file():
        raise BackupError(f"{source_dir} holds no manifest.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("format") != FORMAT:
        raise BackupError(f"unsupported backup format {data.get('format')!r}")
    for name, expected in data["checksums"].items():
        actual = _sha256(source_dir / name)
        if actual != expected:
            raise BackupError(f"{name} is corrupt (checksum mismatch)")
    data.pop("format")
    return Manifest(**data)


async def restore(
    graph: Graph | None,
    storage: LocalStorage,
    source_dir: Path,
    *,
    store: Graph | None = None,
    postgres: Postgres | None = None,
) -> Manifest:
    """Load a backup into an empty database and storage; the caller wipes first when needed. With a ``store`` apart
    from the IFC graph ``graph``, IFC-DB's nodes go to the IFC graph and every other node to the store, whichever
    graphs the backup was taken from; a relationship between the two, which no database can hold, is left out. With
    ``postgres`` the server's own data goes there, from a backup of PostgreSQL or of a graph, and IFC-DB's nodes to
    ``graph``, which is None when the server uses no graph."""
    manifest = read_manifest(source_dir)
    if postgres is not None:
        await _restore_into_postgres(graph, postgres, source_dir, manifest)
    elif manifest.store_dialect == POSTGRES:
        raise BackupError("a backup of PostgreSQL restores into PostgreSQL alone (OPENCDE_STORE_URL)")
    elif graph is None:
        raise BackupError("a backup of a graph needs the graph")
    else:
        await _restore_nodes(source_dir, {True: graph, False: store or graph})

    with tarfile.open(source_dir / "storage.tar.gz", "r:gz") as archive:
        for member in archive.getmembers():
            if not member.isfile() or not _is_storage_key(member.name):
                raise BackupError(f"unexpected entry in storage archive: {member.name!r}")
        archive.extractall(storage.root, filter="data")
    return manifest


async def _restore_nodes(source_dir: Path, targets: dict[bool, Graph | None]) -> None:
    """The backup's nodes and relationships into the graphs of ``targets``, IFC-DB's (``True``) and the server's own
    (``False``); a side without a graph is left out, and so is a relationship between two graphs."""
    groups: dict[bool, dict[tuple[str, ...], list[dict[str, Any]]]] = {True: {}, False: {}}
    pending = pending_bytes = 0
    with gzip.open(source_dir / "nodes.jsonl.gz", "rt", encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            labels = tuple(_safe(label) for label in record["labels"])
            side = of_the_models(labels)
            if targets[side] is None:
                continue  # the server's own, which PostgreSQL holds
            groups[side].setdefault(labels, []).append(record["props"])
            pending += 1
            pending_bytes += len(line)  # a block of an IFC model's geometry is a lot of text in one node
            if pending >= BATCH or pending_bytes >= MAX_BATCH_BYTES:
                for each, grouped in groups.items():
                    await _create_nodes(targets[each], grouped)
                groups = {True: {}, False: {}}
                pending = pending_bytes = 0
    for each, grouped in groups.items():
        await _create_nodes(targets[each], grouped)

    rel_groups: dict[bool, dict[tuple[str, str, str, str, str], list[dict[str, Any]]]] = {True: {}, False: {}}
    pending = 0
    with gzip.open(source_dir / "relationships.jsonl.gz", "rt", encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            a_label, a_prop, a_value = record["a"]
            b_label, b_prop, b_value = record["b"]
            side = of_the_models([a_label])
            if targets[True] is not targets[False] and of_the_models([b_label]) != side:
                continue  # across the two graphs: the two kinds of data meet by values only
            if targets[side] is None:
                continue
            key = (_safe(a_label), _safe(a_prop), _safe(record["type"]), _safe(b_label), _safe(b_prop))
            rel_groups[side].setdefault(key, []).append({"a": a_value, "b": b_value, "props": record["props"]})
            pending += 1
            if pending >= BATCH:
                for each, grouped in rel_groups.items():
                    await _create_relationships(targets[each], grouped)
                rel_groups = {True: {}, False: {}}
                pending = 0
    for each, grouped in rel_groups.items():
        await _create_relationships(targets[each], grouped)


async def _restore_into_postgres(graph: Graph | None, postgres: Postgres, source_dir: Path, manifest: Manifest) -> None:
    """The store's rows into PostgreSQL, from its own backup or made of a graph's, and then IFC-DB's nodes into
    ``graph``. No row goes in unless all of them can."""
    from opencde.postgres import tables
    from opencde.postgres.schema import migrate

    try:
        if manifest.store_dialect == POSTGRES:
            if graph is None and manifest.nodes:
                raise BackupError(NO_GRAPH.format(nodes=manifest.nodes))
            await tables.load(postgres, lambda: _store_rows(source_dir), until=manifest.store_migration)
        else:
            gathered, models = _gather(source_dir)
            if graph is None and models:
                raise BackupError(NO_GRAPH.format(nodes=models))
            await migrate(postgres)
            rows = gathered.rows(await tables.columns(postgres))
            await tables.load(postgres, lambda: rows)
    except ValueError as exc:  # what has no place, a table this server does not know, a row PostgreSQL refused
        raise BackupError(str(exc)) from None
    if graph is not None:
        await _restore_nodes(source_dir, {True: graph, False: None})


NO_GRAPH = "the backup holds {nodes} nodes of IFC-DB's models and the server uses no graph: turn IFC-DB on first"


def _store_rows(source_dir: Path) -> Iterator[tuple[str, dict[str, Any]]]:
    with gzip.open(source_dir / STORE_ROWS, "rt", encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            yield record["table"], record["row"]


def _gather(source_dir: Path) -> tuple[Gathered, int]:
    """The server's own nodes and relationships of a graph's backup, and how many of IFC-DB's nodes it holds."""
    from opencde.postgres.from_graph import Gathered

    gathered, models = Gathered(), 0
    with gzip.open(source_dir / "nodes.jsonl.gz", "rt", encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            labels = tuple(record["labels"])
            if of_the_models(labels):
                models += 1
                continue
            key = node_key(list(labels), record["props"])
            if key is None:
                raise BackupError(f"a node without its unique key in the backup: {labels}")
            gathered.add_node(labels, key, record["props"])
    with gzip.open(source_dir / "relationships.jsonl.gz", "rt", encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            if not of_the_models([record["a"][0]]) and not of_the_models([record["b"][0]]):
                gathered.add_relationship(record)
    return gathered, models


def _is_storage_key(name: str) -> bool:
    category, _, filename = name.partition("/")
    return bool(category and filename and "/" not in filename and ".." not in filename and not filename.startswith("."))


async def _create_nodes(graph: Graph | None, groups: dict[tuple[str, ...], list[dict[str, Any]]]) -> None:
    if graph is None:
        return
    for labels, rows in groups.items():
        label_clause = ":".join(labels)
        await graph.run(f"UNWIND $rows AS row CREATE (n:{label_clause}) SET n = row", rows=rows)


async def _create_relationships(
    graph: Graph | None, groups: dict[tuple[str, str, str, str, str], list[dict[str, Any]]]
) -> None:
    if graph is None:
        return
    for (a_label, a_prop, rel_type, b_label, b_prop), rows in groups.items():
        await graph.run(
            f"UNWIND $rows AS row "
            f"MATCH (a:{a_label} {{{a_prop}: row.a}}), (b:{b_label} {{{b_prop}: row.b}}) "
            f"CREATE (a)-[r:{rel_type}]->(b) SET r = row.props",
            rows=rows,
        )
