# This file was generated with the assistance of an AI coding tool.
"""What the IFC-DB service, the merge and the MCP tool ``list_models`` ask of the server's own data in PostgreSQL
(ADR 0029), without a database: a member's read joins the membership, a job's read is held to the project it runs in,
a value is only ever an argument, and the service finds the documents and the viewpoints where the store is.
``test_postgres_store.py`` reads the modules' syntax trees for statements built from strings; the database's own side
is ``tests/integration/test_postgres_service.py``."""

from __future__ import annotations

import re
from pathlib import Path

from opencde.ifcgraph.service import IfcGraphService
from opencde.postgres.database import Postgres
from opencde.storage import LocalStorage
from opencde.store.bcf import postgres as bcf
from opencde.store.bcf.postgres import PostgresViewpointRepository
from opencde.store.bcf.viewpoints import ViewpointRepository
from opencde.store.documents import postgres as documents
from opencde.store.documents.postgres import PostgresDocumentRepository
from opencde.store.documents.versions import DocumentRepository
from opencde.store.jobs import postgres as jobs
from opencde.store.jobs.postgres import PostgresConversionRepository
from tests.conftest import test_settings as make_settings
from tests.unit.test_postgres_store import URL, WEAK, FakePostgres

MEMBER = re.compile(r"JOIN memberships m ON m\.project_id = [a-z]\.project_id AND m\.user_id = \$1")


def test_a_members_read_joins_the_membership_and_a_jobs_read_is_held_to_its_project() -> None:
    for statement in (documents.MEMBER_HEADS, jobs.CONVERSIONS_FOR_MEMBER_VERSIONS):
        assert MEMBER.search(statement), statement
    assert "WHERE d.project_id = ANY($2::text[])" in documents.MEMBER_HEADS, "only the projects asked for"
    assert "c.project_id = w.project_id" in jobs.CONVERSIONS_FOR_MEMBER_VERSIONS, (
        "a version's conversion, its project's"
    )
    for statement in (documents.HEAD, documents.VERSION_FILE, documents.PROJECT_HEADS):
        assert "d.project_id = $1" in statement and "memberships" not in statement, statement
    for statement in (bcf.COMPONENT_GUIDS, bcf.COMPONENT_GTINS_SET):
        assert "t.project_id = $1" in statement and "($2::text IS NULL OR v.guid = $2)" in statement, statement


def test_the_checks_are_read_through_the_jobs_view_and_a_suffix_without_wildcards() -> None:
    assert "FROM version_validations c" in documents.VERSION_FILE and "c.state = $4" in documents.VERSION_FILE
    for statement in (documents.PROJECT_HEADS, documents.MEMBER_HEADS):
        assert "right(lower(h.file_name), length(s.suffix)) = s.suffix" in statement and "LIKE" not in statement
        assert "ORDER BY d.document_id, v.version_index DESC" in statement, "the latest version of each document"


async def test_a_value_is_only_ever_an_argument() -> None:
    fake = FakePostgres()
    found = PostgresDocumentRepository(fake)  # ty: ignore[invalid-argument-type]
    await found.head(project_id=WEAK, document_id=WEAK)
    await found.version_file(project_id=WEAK, document_id=WEAK, version_index=1)
    await found.project_heads(project_id=WEAK, suffixes=[WEAK], limit=1)
    await found.member_heads(user_id=WEAK, project_ids=[WEAK], suffixes=[WEAK], limit=1)
    await PostgresConversionRepository(fake).for_member_versions(user_id=WEAK, versions=[(WEAK, WEAK, 1)])  # ty: ignore[invalid-argument-type]
    viewpoints = PostgresViewpointRepository(fake, LocalStorage(Path(".")), max_image_bytes=1)  # ty: ignore[invalid-argument-type]
    await viewpoints.component_guids(project_id=WEAK, viewpoint_guid=WEAK)
    await viewpoints.set_gtins(project_id=WEAK, gtins={WEAK: WEAK}, viewpoint_guid=WEAK)
    assert len(fake.statements) == 7
    for statement, args in fake.statements:
        assert WEAK not in statement and WEAK in str(args)


async def test_the_versions_and_the_gtins_go_as_arrays_of_one_length() -> None:
    fake = FakePostgres()
    await PostgresConversionRepository(fake).for_member_versions(  # ty: ignore[invalid-argument-type]
        user_id="u", versions=[("p-1", "d-1", 1), ("p-2", "d-2", 3)]
    )
    viewpoints = PostgresViewpointRepository(fake, LocalStorage(Path(".")), max_image_bytes=1)  # ty: ignore[invalid-argument-type]
    await viewpoints.set_gtins(project_id="p-1", gtins={"g-1": "t-1", "g-2": "t-2"})
    (_, versions), (_, gtins) = fake.statements
    assert versions == ("u", ["p-1", "p-2"], ["d-1", "d-2"], [1, 3])
    assert gtins == ("p-1", None, ["g-1", "g-2"], ["t-1", "t-2"]), "every viewpoint of the project, each GTIN its own"


def test_the_service_finds_the_documents_and_the_viewpoints_where_the_store_is(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, ifcgraph_enabled=True)
    postgres = Postgres(URL, "", connect_timeout_seconds=1.0)
    graph = object()
    on_postgres = IfcGraphService(graph, LocalStorage(tmp_path), settings, postgres=postgres)  # ty: ignore[invalid-argument-type]
    assert isinstance(on_postgres.documents, PostgresDocumentRepository)
    assert isinstance(on_postgres.viewpoints, PostgresViewpointRepository)
    on_graph = IfcGraphService(graph, LocalStorage(tmp_path), settings)  # ty: ignore[invalid-argument-type]
    assert isinstance(on_graph.documents, DocumentRepository) and isinstance(on_graph.viewpoints, ViewpointRepository)
    assert on_graph.documents.graph is graph, "the server's own data, on the store"
