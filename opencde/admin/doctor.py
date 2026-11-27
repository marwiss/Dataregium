# This file was generated with the assistance of an AI coding tool.
"""Health checks shown on the settings page and printed by ``opencde doctor``."""

from __future__ import annotations

import asyncio
import importlib.util
import os
import socket
import struct
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from neo4j.exceptions import Neo4jError

from opencde import __version__
from opencde.graph.driver import ping_within
from opencde.graph.graph import Graph
from opencde.graph.schema import KEYED
from opencde.postgres.database import Postgres, PostgresUnavailable
from opencde.postgres.schema import applied, migrations
from opencde.settings import Settings, in_a_container
from opencde.storage import LocalStorage
from opencde.timeutil import now_iso
from opencde.ui.i18n import translate

NTP_EPOCH_OFFSET = 2_208_988_800  # seconds from 1900-01-01 (NTP epoch) to 1970-01-01 (Unix epoch)
NTP_PORT = 123
MAX_CLOCK_OFFSET_SECONDS = 5.0  # codes live 60 s and tokens carry expiry times: the clock must be roughly right


@dataclass(frozen=True)
class Check:
    """One check's result. Its words live in the language catalogues: ``check.<key>`` names it and
    ``check.detail.<detail_key>`` says how it went; a value that needs no words goes in ``value``."""

    key: str
    ok: bool
    detail_key: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    value: str = ""

    def name_in(self, lang: str) -> str:
        return translate(lang, f"check.{self.key}")

    def detail_in(self, lang: str) -> str:
        return translate(lang, f"check.detail.{self.detail_key}", **self.params) if self.detail_key else self.value

    @property
    def name(self) -> str:
        """In English, for the command line and the administration API."""
        return self.name_in("en")

    @property
    def detail(self) -> str:
        return self.detail_in("en")


def ntp_offset_from_response(data: bytes, sent: float, received: float) -> float | None:
    """The local clock's offset from the server's transmit timestamp in an SNTP response (RFC 4330)."""
    if len(data) < 48:
        return None
    seconds, fraction = struct.unpack("!II", data[40:48])
    server_time = seconds - NTP_EPOCH_OFFSET + fraction / 2**32
    return server_time - (sent + received) / 2


def ntp_offset_seconds(server: str, timeout: float = 1.5) -> float | None:
    """Ask an NTP server what time it is, SNTP client mode, and return the offset; None when it does not answer."""
    request = b"\x1b" + bytes(47)  # leap indicator 0, version 3, mode 3 (client)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(timeout)
            sent = time.time_ns() / 1e9
            sock.sendto(request, (server, NTP_PORT))
            data, _ = sock.recvfrom(48)
            received = time.time_ns() / 1e9
    except OSError:
        return None
    return ntp_offset_from_response(data, sent, received)


async def clock_check(server: str) -> Check:
    """Compare the server clock with NTP; expiry times are only as good as the clock."""
    if not server:
        return Check("clock", True, "not_checked_no_ntp", {"now": now_iso()})
    offset = await asyncio.to_thread(ntp_offset_seconds, server)
    if offset is None:
        return Check("clock", True, "not_checked_ntp", {"server": server, "port": NTP_PORT, "now": now_iso()})
    return Check(
        "clock",
        abs(offset) <= MAX_CLOCK_OFFSET_SECONDS,
        "clock_offset",
        {"offset": f"{offset:+.2f}", "server": server, "now": now_iso()},
    )


async def schema_check(graph: Graph, key: str = "schema") -> Check:
    """The unique constraints the server relies on; it creates them when it starts, in each of its graphs."""
    try:
        rows = await graph.schema_rows(graph.dialect.list_constraints)
    except Neo4jError as exc:
        return Check(key, False, "schema_unlisted", {"code": exc.code})
    counts = {"found": len(rows), "expected": len(KEYED)}
    complete = len(rows) >= len(KEYED)
    return Check(key, complete, "schema_count" if complete else "schema_short", counts)


async def postgres_checks(settings: Settings, postgres: Postgres) -> list[Check]:
    """PostgreSQL of the server's own data (ADR 0029): whether it answers, and its migrations beside those the
    server ships, which it applies when it starts."""
    where = {"url": postgres.url}
    if not await postgres.ping(settings.graph_connect_timeout_seconds):
        return [Check("store", False, "postgres_silent", where), Check("store_schema", False, "schema_no_database")]
    had = await applied(postgres)
    shipped = migrations()
    unknown = sorted(set(had) - {migration.version for migration in shipped})
    if unknown:
        named = ", ".join(f"{version:04d}" for version in unknown)
        return [
            Check("store", True, "postgres_ok", where),
            Check("store_schema", False, "migrations_newer", {"unknown": named}),
        ]
    counts = {"applied": len(had), "shipped": len(shipped), "last": had[max(had)] if had else "-"}
    complete = len(had) == len(shipped)
    return [
        Check("store", True, "postgres_ok", where),
        Check("store_schema", complete, "migrations_count" if complete else "migrations_short", counts),
    ]


async def store_checks(settings: Settings, graph: Graph, store: Graph | None, postgres: Postgres | None) -> list[Check]:
    """The server's own data when it is not in the IFC graph's connection: in a graph of its own (ADR 0027), whether
    it answers and its constraints; in PostgreSQL (ADR 0029), whether it answers and its migrations."""
    if postgres is not None:
        return await postgres_checks(settings, postgres)
    if store is None or store is graph:
        return [Check("store", True, "store_shared")]
    reachable = await ping_within(store.driver, store.database, settings.graph_connect_timeout_seconds)
    where = {"dialect": store.dialect.name.value, "uri": settings.store_graph_uri, "database": store.database}
    return [
        Check("store", reachable, "graph_ok" if reachable else "graph_silent", where),
        await schema_check(store, "store_schema") if reachable else Check("store_schema", False, "schema_no_database"),
    ]


async def headers_check(settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> Check:
    """Ask the server at its public address, which also tries the TLS certificate when the address is https."""
    url = settings.url("/healthz")
    try:
        async with httpx.AsyncClient(timeout=3.0, transport=transport) as client:
            response = await client.get(url)
    except httpx.HTTPError as exc:
        if "CERTIFICATE" in str(exc).upper():
            return Check("headers", False, "headers_certificate", {"url": url})
        return Check("headers", True, "headers_no_answer", {"url": url})
    wanted = ["x-content-type-options", "content-security-policy", "referrer-policy"]
    if settings.is_production:
        wanted.append("strict-transport-security")  # the proxy in front adds this one
    missing = [name for name in wanted if name not in response.headers]
    if missing:
        return Check("headers", False, "headers_missing", {"names": ", ".join(missing)})
    return Check("headers", True, "headers_present", {"url": url})


def listen_check(settings: Settings) -> Check:
    """Where the local engine listens (ADR 0023, F77 a): on the loopback, or on its container's own interfaces;
    the setting in a process outside a container fails, since serve refuses to start so."""
    if not settings.local_in_container:
        return Check("local_listen", True, "local_loopback")
    if in_a_container():
        return Check("local_listen", True, "local_in_container")
    return Check("local_listen", False, "local_not_in_container")


async def git_check(settings: Settings) -> Check:
    """Whether the projects' repositories can be written (ADR 0030): git answers, and the server's own data is in
    PostgreSQL, as a server whose data is in the graph keeps none (F99 a)."""
    if not settings.git_on:
        return Check("git", False, "git_needs_postgres")
    from opencde.repos.git import Git, GitError

    try:
        version = await Git(settings.data_dir / "git", settings.git_path, 20).version()
    except GitError:
        return Check("git", False, "git_missing", {"path": settings.git_path})
    return Check("git", True, "git_ok", {"version": version})


_TRIAL = (
    "ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION((''),'2;1');\nFILE_NAME('','2026-01-01T00:00:00+00:00',(''),(''),'','','');\n"
    "FILE_SCHEMA(('IFC4'));\nENDSEC;\nDATA;\n{lines}ENDSEC;\nEND-ISO-10303-21;\n"
)


async def ifcmerge_check(settings: Settings) -> Check:
    """Whether ifcmerge merges a small model of the doctor's own: two changes to one wall, one on each side."""
    import tempfile
    from pathlib import Path

    from opencde.repos.merge import MergeError, merge

    if not settings.ifcmerge_on:
        return Check("ifcmerge", False, "ifcmerge_needs_git")
    wall = "#1=IFCWALL('2O2Fr$t4X7Zf8NOew3FLOH',$,{name},{description},$,$,$,$,$);\n"
    sides = {
        "base.ifc": wall.format(name="'W'", description="$"),
        "theirs.ifc": wall.format(name="'Main'", description="$"),
        "ours.ifc": wall.format(name="'W'", description="'Mine'"),
    }
    with tempfile.TemporaryDirectory(prefix="ifcmerge-check-") as folder:
        root = Path(folder)
        for name, line in sides.items():
            (root / name).write_text(_TRIAL.format(lines=line), encoding="utf-8")
        (root / "merging").mkdir()
        try:
            done = await merge(
                perl=settings.perl_path,
                ifcmerge=str(settings.ifcmerge_path),
                base=root / "base.ifc",
                theirs=root / "theirs.ifc",
                ours=root / "ours.ifc",
                folder=root / "merging",
                seconds=60,
                memory_mb=settings.ifcmerge_memory_mb,
            )
        except MergeError as exc:
            return Check("ifcmerge", False, "ifcmerge_failed", {"error": str(exc)[:200]})
        merged = done.path.read_text(encoding="utf-8") if done.path is not None else ""
    if "'Main','Mine'" not in merged:
        return Check("ifcmerge", False, "ifcmerge_wrong", {"path": str(settings.ifcmerge_path)})
    return Check("ifcmerge", True, "ifcmerge_ok", {"path": str(settings.ifcmerge_path)})


#: The projects with versions of text files and no repository: their history comes with ``opencde git migrate``.
WITHOUT_HISTORY = r"""
SELECT count(DISTINCT d.project_id) AS projects
FROM document_versions v JOIN documents d ON d.document_id = v.document_id
WHERE v.commit IS NULL AND lower(v.file_name) ~ '\.(ifc|ids|xml|json|geojson|csv|txt|md)$'
  AND NOT EXISTS (SELECT 1 FROM project_repos r WHERE r.project_id = d.project_id)
"""


async def git_history_check(postgres: Postgres) -> Check:
    """How many projects have versions from before their repositories, for ``opencde git migrate``."""
    try:
        row = await postgres.one(WITHOUT_HISTORY)
    except PostgresUnavailable:  # the store's own check says what is wrong with the database
        return Check("git_history", True, "git_history_unknown")
    count = int(row["projects"]) if row else 0
    return Check("git_history", True, "git_history_missing" if count else "git_history_ok", {"projects": count})


#: The projects mirrored to a forge, those whose last attempt did not push everything, and the files left out.
MIRRORS = """
SELECT project_id, status, left_out FROM project_remotes WHERE expires_at > $1 ORDER BY project_id LIMIT 10000
"""
NAMED = 5  # the files left out that the check names


async def git_remotes_check(postgres: Postgres) -> Check:
    """How the mirrors to the forges go (ADR 0032): a project whose push failed or was changed at the forge makes the
    check fail, and the files left out are named by project and path in the repository (F208 c), as each project's
    status names them all."""
    try:
        rows = await postgres.run(MIRRORS, now_iso())
    except PostgresUnavailable:
        return Check("git_remotes", True, "git_remotes_unknown")
    failing = sum(row["status"] in ("error", "changed") for row in rows)
    files = [f"{row['project_id']}:{entry['path']}" for row in rows for entry in row["left_out"] or []]
    found = {
        "projects": len(rows),
        "failing": failing,
        "left_out": len(files),
        "files": ", ".join(files[:NAMED]) + (" …" if len(files) > NAMED else "") or "-",
    }
    return Check("git_remotes", not failing, "git_remotes_failing" if failing else "git_remotes_ok", found)


async def run_checks(
    settings: Settings,
    graph: Graph,
    storage: LocalStorage,
    *,
    store: Graph | None,
    postgres: Postgres | None,
) -> list[Check]:
    """Every check, of the IFC graph ``graph`` and of the ``store`` of the server's own data beside it, or of
    ``postgres`` when it holds that data. Without IFC-DB a server with PostgreSQL uses no graph, and none is asked."""
    checks = [Check("version", True, value=__version__), Check("environment", True, value=settings.environment)]
    checks.append(Check("tier", True, value=settings.tier.value))
    if settings.is_local:
        checks.append(listen_check(settings))
    checks.append(Check("apis", True, value=", ".join(settings.apis)))
    if settings.bim_on:  # the BIM functions run IfcOpenShell in their workers (F59)
        ready = importlib.util.find_spec("ifcopenshell") is not None
        checks.append(Check("bim", ready, "bim_ready" if ready else "bim_without_extra"))
    if settings.git_enabled:
        checks.append(await git_check(settings))
        if settings.git_on and postgres is not None:
            checks.append(await git_history_check(postgres))
            if settings.git_remotes_on:
                checks.append(await git_remotes_check(postgres))
    if settings.ifcmerge_path:
        checks.append(await ifcmerge_check(settings))
    https = settings.public_url.startswith("https://")
    fine = https or settings.environment != "production"
    checks.append(
        Check("public_url", fine, value=settings.public_url)
        if https
        else Check("public_url", fine, "public_url_http", {"url": settings.public_url})
    )
    if postgres is not None and not settings.ifcgraph_enabled:
        checks.append(Check("graph", True, "graph_unused"))
    else:
        reachable = await ping_within(graph.driver, graph.database, settings.graph_connect_timeout_seconds)
        where = {"dialect": graph.dialect.name.value, "uri": settings.graph_uri, "database": settings.database_name}
        checks.append(Check("graph", reachable, "graph_ok" if reachable else "graph_silent", where))
        checks.append(await schema_check(graph) if reachable else Check("schema", False, "schema_no_database"))
    checks += await store_checks(settings, graph, store, postgres)
    probe = storage.root / ".write-probe"
    try:
        probe.write_bytes(b"ok")
        probe.unlink()
        checks.append(Check("storage", True, "storage_ok", {"path": str(storage.root)}))
    except OSError as exc:
        checks.append(
            Check("storage", False, "storage_failed", {"path": str(storage.root), "error": type(exc).__name__})
        )
    if settings.renamed_settings:  # the old names still work, so this is a reminder rather than a fault
        names = ", ".join(f"{old} → {new}" for old, new in settings.renamed_settings)
        checks.append(Check("renamed_settings", True, "renamed_settings", {"names": names}))
    checks.append(Check("secret_key", len(settings.secret_key.get_secret_value()) >= 32, "secret_key"))
    checks.append(
        Check(
            "rate_limits",
            settings.rate_limits_enabled or settings.environment != "production",
            "rate_limits_on" if settings.rate_limits_enabled else "rate_limits_off",
        )
    )
    registration = "registration_on" if settings.dynamic_client_registration else "registration_off"
    checks.append(Check("dynamic_registration", True, registration))
    checks.append(Check("cors", True, "cors_origins", {"origins": ", ".join(settings.cors_origins)}))
    checks.append(await headers_check(settings))
    checks.append(await clock_check(settings.ntp_server))
    checks.append(Check("process", True, "process_pid", {"pid": os.getpid()}))
    return checks
