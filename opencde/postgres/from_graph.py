# This file was generated with the assistance of an AI coding tool.
"""A graph's backup as PostgreSQL's rows (ADR 0029): how a server whose own data was in a graph moves to PostgreSQL.

A graph's backup (``opencde.backup``) holds the nodes that have a unique key and the relationships between them.
``Gathered`` takes the server's own (IFC-DB's models go to the IFC graph as they are) and ``Gathered.rows`` makes the
tables' rows of them: a node's properties are the columns of the same names, what a relationship says (the project a
topic is in, the user a token is for, a member's role) is a column or a row of its own, and what the graph keeps as
JSON text where PostgreSQL keeps ``jsonb`` (a version's metadata, an upload's files) is read. A relationship that
says again what a row holds already (``CREATED_BY``, ``DERIVED_FROM``, a BCF document's ``UPLOADED_BY``) adds
nothing. A job's properties that are not its table's columns are its ``fields``, as the jobs' repositories keep them.

What has no place is refused rather than guessed, all of it in one message: a label without a table here, a relationship of
another kind, a property no column holds, a row without a value its table requires. What a graph's backup never held,
the nodes without a unique key (teams, a version's publication, a viewpoint's components, the trials of change sets,
clash checks and merges), is not among the rows either.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from typing import Any

from opencde.postgres.tables import TABLES

#: The labels whose nodes are rows, and their tables. A precheck is a ``Validation`` too.
TABLE_OF = {
    "User": "users",
    "Project": "projects",
    "WebSession": "web_sessions",
    "Client": "oauth_clients",
    "AuthorizationCode": "authorization_codes",
    "AccessToken": "access_tokens",
    "RefreshToken": "refresh_tokens",
    "Document": "documents",
    "DocumentVersion": "document_versions",
    "UploadSession": "upload_sessions",
    "UploadPart": "upload_parts",
    "SelectionSession": "selection_sessions",
    "Topic": "topics",
    "FileRef": "header_files",
    "DocumentReference": "document_references",
    "Comment": "comments",
    "Viewpoint": "viewpoints",
    "Bitmap": "bitmaps",
    "BcfDocument": "bcf_documents",
    "Event": "bcf_events",
    "Conversion": "conversions",
    "Validation": "validations",
    "ChangeSetDraft": "change_set_drafts",
    "AuditEvent": "audit_events",
    "SignInThrottle": "sign_in_throttles",
}
#: A project's BCF extensions are its row's ``extensions``.
EXTENSIONS = "Extensions"
#: ``(type, from, to)`` of a relationship that names a row's parent: the end that is the row, and its column for the
#: other end's key.
PARENTS: dict[tuple[str, str, str], tuple[str, str]] = {
    ("HAS_EXTENSIONS", "Project", "Extensions"): ("b", "project_id"),
    ("HAS_DOCUMENT", "Project", "Document"): ("b", "project_id"),
    ("HAS_DOCUMENT", "Project", "BcfDocument"): ("b", "project_id"),
    ("HAS_VERSION", "Document", "DocumentVersion"): ("b", "document_id"),
    ("HAS_PART", "UploadSession", "UploadPart"): ("b", "session_id"),
    ("HAS_TOPIC", "Project", "Topic"): ("b", "project_id"),
    ("HAS_FILE", "Topic", "FileRef"): ("b", "topic_guid"),
    ("HAS_DOCUMENT_REFERENCE", "Topic", "DocumentReference"): ("b", "topic_guid"),
    ("HAS_COMMENT", "Topic", "Comment"): ("b", "topic_guid"),
    ("HAS_VIEWPOINT", "Topic", "Viewpoint"): ("b", "topic_guid"),
    ("HAS_BITMAP", "Viewpoint", "Bitmap"): ("b", "viewpoint_guid"),
    ("HAS_EVENT", "Project", "Event"): ("b", "project_id"),
    ("HAS_CONVERSION", "Project", "Conversion"): ("b", "project_id"),
    ("HAS_VALIDATION", "DocumentVersion", "Validation"): ("b", "version_id"),
    ("HAS_PRECHECK", "Project", "Validation"): ("b", "project_id"),
    ("HAS_DRAFT", "Project", "ChangeSetDraft"): ("b", "project_id"),
    ("FOR", "WebSession", "User"): ("a", "user_id"),
    ("FOR", "AuthorizationCode", "User"): ("a", "user_id"),
    ("FOR", "AccessToken", "User"): ("a", "user_id"),
    ("FOR", "RefreshToken", "User"): ("a", "user_id"),
    ("ISSUED_TO", "AuthorizationCode", "Client"): ("a", "client_id"),
    ("ISSUED_TO", "AccessToken", "Client"): ("a", "client_id"),
    ("ISSUED_TO", "RefreshToken", "Client"): ("a", "client_id"),
    ("UPLOADED_BY", "DocumentVersion", "User"): ("a", "uploaded_by"),
}
#: What a row holds already: the author's name, the version derived from, the BCF document's author.
REPEATS = frozenset(
    {
        ("CREATED_BY", "Topic", "User"),
        ("CREATED_BY", "Comment", "User"),
        ("CREATED_BY", "Viewpoint", "User"),
        ("DERIVED_FROM", "DocumentVersion", "DocumentVersion"),
        ("UPLOADED_BY", "BcfDocument", "User"),
    }
)
MEMBER_OF = ("MEMBER_OF", "User", "Project")
RELATED_TO = ("RELATED_TO", "Topic", "Topic")
#: What a row takes where the node has nothing, as the graph's readers take it.
DEFAULTS: dict[str, dict[str, Any]] = {
    "users": {"is_admin": False, "disabled": False, "must_change_password": False},  # nosec B105  # a flag
    "projects": {"topic_counter": 0, "block_on_validation_errors": False, "ai_policy": "local", "extensions": {}},
    "oauth_clients": {
        "disabled": False,
        "pending_approval": False,
        "dynamic": False,
        "pkce_optional": False,
        "headless_uploads": False,
    },
    "authorization_codes": {"redirect_required": False, "spent": False},
    "refresh_tokens": {"spent": False},
    "documents": {"latest_index": 0},
    "document_versions": {"metadata": []},
    "upload_sessions": {"files": []},
    "upload_parts": {"received": False},
    "selection_sessions": {"extensions": [], "selected": []},
    "viewpoints": {"has_components": False, "default_visibility": True, "has_hints": False},
    "change_set_drafts": {"steps": "[]", "refs": 0},
}
#: The jobs whose properties beyond their columns are their ``fields``; a conversion's ``model_id`` is its id.
WITH_FIELDS = frozenset({"conversions", "validations"})
DROPPED = {"conversions": frozenset({"model_id"})}

Key = tuple[str, Any]  # a node's label and the value of its unique key


class Gathered:
    """The server's own nodes and relationships of a graph's backup, gathered to become rows."""

    def __init__(self) -> None:
        self.nodes: list[tuple[tuple[str, ...], Key, dict[str, Any]]] = []
        self.relationships: list[tuple[tuple[str, str, str], Key, Key, dict[str, Any]]] = []

    def add_node(self, labels: tuple[str, ...], key: tuple[str, str, Any], props: dict[str, Any]) -> None:
        """A node as the backup holds it, with its unique key (``backup.node_key``): ``(label, property, value)``."""
        self.nodes.append((labels, (key[0], key[2]), props))

    def add_relationship(self, record: dict[str, Any]) -> None:
        """A relationship as the backup holds it: ``a`` and ``b`` are ``[label, property, value]``."""
        (a_label, _, a_value), (b_label, _, b_value) = record["a"], record["b"]
        shape = (record["type"], a_label, b_label)
        self.relationships.append((shape, (a_label, a_value), (b_label, b_value), record.get("props") or {}))

    def rows(self, columns: dict[str, dict[str, bool]]) -> list[tuple[str, dict[str, Any]]]:
        """The rows of every table, as ``(table, row)`` in the order of ``tables.TABLES``, by the tables' ``columns``
        (``tables.columns``). Raises ``ValueError`` naming everything that has no place, and how often."""
        problems: Counter[str] = Counter()
        parents: dict[Key, dict[str, Any]] = defaultdict(dict)
        by_table: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for shape, a, b, props in self.relationships:
            if shape == MEMBER_OF:
                by_table["memberships"].append({**props, "user_id": a[1], "project_id": b[1]})
            elif shape == RELATED_TO:
                by_table["related_topics"].append({**props, "topic_guid": a[1], "related_guid": b[1]})
            elif shape in PARENTS:
                end, column = PARENTS[shape]
                row, other = (b, a) if end == "b" else (a, b)
                parents[row][column] = other[1]
            elif shape not in REPEATS:
                problems[f"{shape[0]} from {shape[1]} to {shape[2]}: a relationship with no place"] += 1

        extensions: dict[Any, dict[str, Any]] = {}
        for labels, key, props in self.nodes:
            if EXTENSIONS in labels:
                project = parents[key].get("project_id")
                if project is None:
                    problems["Extensions: without a project"] += 1
                extensions[project] = {name: value for name, value in props.items() if name != "id"}
                continue
            label = next((label for label in labels if label in TABLE_OF), None)
            if label is None:
                problems[f"{key[0]}: no table in PostgreSQL holds it"] += 1
                continue
            table = TABLE_OF[label]
            row = {**props, **parents[key]}
            metadata = row.get("metadata")
            if table == "document_versions" and isinstance(metadata, str):
                row["metadata"] = json.loads(metadata)
            if table == "upload_sessions" and "files_json" in row:
                row["files"] = json.loads(row.pop("files_json") or "[]")
            if table == "validations" and "Precheck" not in labels:
                version = parents[("DocumentVersion", row.get("version_id"))]
                row["project_id"] = parents[("Document", version.get("document_id"))].get("project_id")
            by_table[table].append(row)
        for row in by_table["projects"]:
            row["extensions"] = extensions.get(row.get("project_id"), {})

        found: list[tuple[str, dict[str, Any]]] = []
        for table in TABLES:
            rows = by_table.pop(table.name, [])
            if table.name == "document_versions":  # the version derived from goes in first
                rows.sort(key=lambda row: (str(row.get("document_id")), row.get("version_index") or 0))
            found += [(table.name, _shaped(table.name, row, columns.get(table.name, {}), problems)) for row in rows]
        if problems:
            told = "; ".join(f"{what} ({count})" for what, count in sorted(problems.items()))
            raise ValueError(f"the backup has what PostgreSQL has no place for: {told}")
        return found


def _shaped(table: str, row: dict[str, Any], columns: dict[str, bool], problems: Counter[str]) -> dict[str, Any]:
    """The row by its table's columns: the defaults where it has nothing, the fields of a job, and a problem for
    each property no column holds and each column it must have and has not."""
    row = {name: value for name, value in row.items() if name not in DROPPED.get(table, ())}
    for name, value in DEFAULTS.get(table, {}).items():
        if row.get(name) is None:
            row[name] = value
    if table in WITH_FIELDS:
        fields = {name: value for name, value in row.items() if name not in columns}
        row = {name: value for name, value in row.items() if name in columns} | {"fields": fields}
    for name in row.keys() - columns.keys():
        problems[f"{table}.{name}: no column holds it"] += 1
    for name, required in columns.items():
        if required and row.get(name) is None:
            problems[f"{table}.{name}: a value it must have is missing"] += 1
    return row
