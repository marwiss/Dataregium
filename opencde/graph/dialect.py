# This file was generated with the assistance of an AI coding tool.
"""Differences between the supported graph databases, in one place.

Application code writes dialect-neutral Cypher (ADR 0003). The statements
that differ, mainly schema DDL, come from the dialect object chosen by
``OPENCDE_GRAPH_DIALECT``. ``FORBIDDEN_CYPHER`` lists constructs that work on
one product only; a test scans the code base for them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from neo4j.exceptions import TransientError

from opencde.settings import GraphDialect

_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Patterns that must not appear in application Cypher (see ADR 0003).
FORBIDDEN_CYPHER: tuple[tuple[str, str], ...] = (
    (r"\bapoc\.", "APOC procedures (Neo4j plugin)"),
    (r"\belementId\s*\(", "elementId() (Neo4j only; use our uuid properties)"),
    (r"(?<![\w.])id\s*\(\s*\w+\s*\)", "id() (deprecated and product specific)"),
    (r"IN\s+TRANSACTIONS", "CALL {} IN TRANSACTIONS (batch in Python with UNWIND instead)"),
    (r"\bshortestPath\s*\(", "shortestPath() (not in Memgraph)"),
    (r"\bpoint\s*\(\s*\{", "point() (store floats instead)"),
    (r"\b(datetime|localdatetime|date|time|localtime|duration)\s*\(", "temporal functions (store ISO strings)"),
    (r"\bexists\s*\(\s*\w+\.\w+\s*\)", "exists(n.prop) (use IS NOT NULL)"),
    (r"\bisEmpty\s*\(", "isEmpty() (not in Memgraph)"),
    (r"\bpercentile(Cont|Disc)\s*\(", "percentile functions (not in Memgraph)"),
    (r"\bstDev[P]?\s*\(", "stDev() (not in Memgraph)"),
    (r"\bnormalize\s*\(", "normalize() (not in Memgraph)"),
    (r"\bsize\s*\(\s*\(", "size((pattern)) (use COUNT {})"),
)


def _check_name(name: str) -> str:
    if not _NAME.match(name):
        raise ValueError(f"unsafe identifier {name!r}")
    return name


@dataclass(frozen=True, slots=True)
class Dialect:
    """What the server needs to know about one graph database product."""

    name: GraphDialect
    default_database: str
    product: str
    # Neo4j backs a unique constraint with an index; Memgraph does not, so it
    # needs an explicit index on the same property for fast lookups.
    constraint_implies_index: bool

    def unique_constraint(self, label: str, prop: str) -> str:
        label, prop = _check_name(label), _check_name(prop)
        if self.name is GraphDialect.NEO4J:
            return f"CREATE CONSTRAINT {label}_{prop}_unique IF NOT EXISTS FOR (n:{label}) REQUIRE n.{prop} IS UNIQUE"
        return f"CREATE CONSTRAINT ON (n:{label}) ASSERT n.{prop} IS UNIQUE"

    def index(self, label: str, prop: str) -> str:
        label, prop = _check_name(label), _check_name(prop)
        if self.name is GraphDialect.NEO4J:
            return f"CREATE INDEX {label}_{prop}_index IF NOT EXISTS FOR (n:{label}) ON (n.{prop})"
        return f"CREATE INDEX ON :{label}({prop})"

    @property
    def list_constraints(self) -> str:
        """A statement answering one row per constraint."""
        return "SHOW CONSTRAINTS" if self.name is GraphDialect.NEO4J else "SHOW CONSTRAINT INFO"

    @property
    def show_transactions(self) -> str | None:
        """A statement listing the running transactions with their Bolt metadata, where the server needs to be told to
        stop one: Memgraph does not take the Bolt transaction timeout, while Neo4j stops a statement at it."""
        return "SHOW TRANSACTIONS" if self.name is GraphDialect.MEMGRAPH else None

    def terminate_transaction(self, transaction_id: str) -> str:
        """Memgraph's statement that stops a running transaction; its id is a number from ``SHOW TRANSACTIONS``."""
        if self.name is not GraphDialect.MEMGRAPH or not transaction_id.isdigit():
            raise ValueError("only a Memgraph transaction id of digits can be terminated")
        return f'TERMINATE TRANSACTIONS "{transaction_id}"'

    @property
    def already_exists_markers(self) -> tuple[str, ...]:
        """Substrings of the error a product raises when DDL is repeated. Treated as success."""
        return ("already exists", "AlreadyExists", "EquivalentSchemaRuleAlreadyExists", "ConstraintAlreadyExists")

    def is_full(self, exc: Exception) -> bool:
        """Whether the product refused a statement for want of memory: Memgraph at its ``--memory-limit`` by the start
        of its message, Neo4j by the code of its transaction memory pool or heap. Both are transient errors; the
        message alone is never enough, since another error can quote a value that holds the words."""
        if not isinstance(exc, TransientError):
            return False
        if self.name is GraphDialect.MEMGRAPH:
            return (exc.message or "").startswith("Memory limit exceeded")
        return (exc.code or "").startswith("Neo.TransientError.General.") and (exc.code or "").endswith(
            "OutOfMemoryError"
        )

    @property
    def full_at_once(self) -> bool:
        """Whether a refusal for want of memory ends the transaction at once instead of after the driver's tries:
        Memgraph's limit holds until something is deleted, while Neo4j's pool frees as other transactions end."""
        return self.name is GraphDialect.MEMGRAPH

    @property
    def storage_info(self) -> str | None:
        """Memgraph's statement answering its memory in use and its limit, one row per figure, outside explicit
        transactions; Neo4j tells it only through its own procedures."""
        return "SHOW STORAGE INFO" if self.name is GraphDialect.MEMGRAPH else None

    @property
    def free_memory(self) -> str | None:
        """Memgraph's statement that collects deleted and aborted data at once instead of at its next cycle, outside
        explicit transactions; Neo4j's memory is the JVM's."""
        return "FREE MEMORY" if self.name is GraphDialect.MEMGRAPH else None


NEO4J = Dialect(
    name=GraphDialect.NEO4J, default_database="neo4j", product="Neo4j 5.26 LTS", constraint_implies_index=True
)
MEMGRAPH = Dialect(
    name=GraphDialect.MEMGRAPH, default_database="memgraph", product="Memgraph 3.x", constraint_implies_index=False
)

DIALECTS: dict[GraphDialect, Dialect] = {NEO4J.name: NEO4J, MEMGRAPH.name: MEMGRAPH}


def get_dialect(name: GraphDialect) -> Dialect:
    return DIALECTS[name]


def refused_for_memory(exc: Exception) -> bool:
    """Whether either product refused a statement for want of memory, for code that runs a transaction of its own
    (the named queries, the administrators' query) and does not know which graph it was."""
    return any(dialect.is_full(exc) for dialect in DIALECTS.values())
