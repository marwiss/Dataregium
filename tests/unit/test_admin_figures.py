# This file was generated with the assistance of an AI coding tool.
"""The administrators' figures without a database: ``Figures`` puts together what each area answers for, and the
dependency reads them from the store that holds the data (ADR 0029)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from opencde.admin.queries import Figures, figures, store_answers
from opencde.postgres.database import Postgres
from opencde.store.accounts.postgres import PostgresProjectRepository, PostgresUserRepository
from opencde.store.accounts.projects import ProjectRepository
from opencde.store.accounts.users import UserRepository
from opencde.store.bcf.documents import BcfDocumentRepository
from opencde.store.bcf.postgres import PostgresBcfDocumentRepository, PostgresTopicRepository
from opencde.store.bcf.topics import TopicRepository
from opencde.store.documents.postgres import PostgresDocumentRepository
from opencde.store.documents.versions import DocumentRepository
from opencde.store.jobs.conversions import ConversionRepository
from opencde.store.jobs.postgres import PostgresConversionRepository
from opencde.store.logbook.requests import RequestLogRepository
from opencde.store.oauth2.clients import ClientRepository
from opencde.store.oauth2.postgres import PostgresClientRepository, PostgresTokenRepository
from opencde.store.oauth2.tokens import TokenRepository
from tests.conftest import test_settings as make_settings
from tests.unit.test_postgres_store import URL

MEMBERSHIPS = [
    {"user_id": "u-v", "username": "viewer", "project_id": "p-b", "project_name": "Building", "role": "viewer"},
    {"user_id": "u-e", "username": "editor", "project_id": "p-d", "project_name": "Demolition", "role": "viewer"},
    {"user_id": "u-e", "username": "editor", "project_id": "p-b", "project_name": "Building", "role": "editor"},
]


class Answers:
    """Stands in for every store: each method answers with what it was given."""

    def __init__(self, **answers: Any) -> None:
        self.answers = answers

    def __getattr__(self, name: str) -> Any:
        async def answer(*args: Any, **kwargs: Any) -> Any:
            return self.answers[name]

        return answer


def _figures() -> Figures:
    projects = [SimpleNamespace(project_id="p-b"), SimpleNamespace(project_id="p-d")]
    return Figures(
        users=Answers(count=3),  # ty: ignore[invalid-argument-type]
        projects=Answers(all=projects, all_memberships=MEMBERSHIPS),  # ty: ignore[invalid-argument-type]
        clients=Answers(all=["a", "b"]),  # ty: ignore[invalid-argument-type]
        tokens=Answers(live_count=4),  # ty: ignore[invalid-argument-type]
        topics=Answers(counts_per_project={"p-b": 2, "": 1}),  # ty: ignore[invalid-argument-type]
        documents=Answers(counts_per_project={"p-b": 1}),  # ty: ignore[invalid-argument-type]
        bcf_documents=Answers(counts_per_project={"p-b": 1, "p-d": 1}),  # ty: ignore[invalid-argument-type]
        conversions=Answers(counts_per_project={"p-b": 2, "": 1}),  # ty: ignore[invalid-argument-type]
        request_log=Answers(count=9),  # ty: ignore[invalid-argument-type]
    )


async def test_the_figures_are_the_areas_own_put_together() -> None:
    found = _figures()
    assert await found.counts() == {
        "users": 3,
        "projects": 2,
        "clients": 2,
        "topics": 3,
        "documents": 1,
        "bcf_documents": 2,
        "sessions": 4,
    }, "a topic no project holds is counted as the graph counted it"
    assert await found.project_stats() == {
        "p-b": {"members": 2, "topics": 2, "documents": 2},
        "p-d": {"members": 1, "topics": 0, "documents": 1},
    }
    assert await found.memberships_by_user() == {
        "u-v": [{"project_id": "p-b", "role": "viewer"}],
        "u-e": [{"project_id": "p-b", "role": "editor"}, {"project_id": "p-d", "role": "viewer"}],
    }, "by project name"
    assert (await found.members_by_project())["p-b"] == [
        {"user_id": "u-e", "username": "editor", "role": "editor"},
        {"user_id": "u-v", "username": "viewer", "role": "viewer"},
    ], "by user name"
    assert await found.membership_counts() == {"u-v": 1, "u-e": 2}
    assert await found.model_counts() == {"p-b": 2, "": 3}, "every model under the empty key"


def test_the_figures_are_read_where_the_data_is() -> None:
    graph: Any = object()
    settings, request_log = make_settings(), RequestLogRepository(graph)
    on_graph = figures(graph, None, settings, request_log)
    assert isinstance(on_graph.users, UserRepository) and isinstance(on_graph.projects, ProjectRepository)
    assert isinstance(on_graph.clients, ClientRepository) and isinstance(on_graph.tokens, TokenRepository)
    assert isinstance(on_graph.topics, TopicRepository) and isinstance(on_graph.documents, DocumentRepository)
    assert isinstance(on_graph.bcf_documents, BcfDocumentRepository)
    assert isinstance(on_graph.conversions, ConversionRepository) and on_graph.request_log is request_log
    postgres = Postgres(URL, "", connect_timeout_seconds=1.0)
    on_postgres = figures(graph, postgres, settings, request_log)
    assert isinstance(on_postgres.users, PostgresUserRepository)
    assert isinstance(on_postgres.projects, PostgresProjectRepository)
    assert isinstance(on_postgres.clients, PostgresClientRepository)
    assert isinstance(on_postgres.tokens, PostgresTokenRepository)
    assert isinstance(on_postgres.topics, PostgresTopicRepository)
    assert isinstance(on_postgres.documents, PostgresDocumentRepository)
    assert isinstance(on_postgres.bcf_documents, PostgresBcfDocumentRepository)
    assert isinstance(on_postgres.conversions, PostgresConversionRepository)


async def test_the_store_that_answers_is_the_one_that_holds_the_figures() -> None:
    class Pinged:
        def __init__(self) -> None:
            self.asked: list[float] = []

        async def ping(self, seconds: float) -> bool:
            self.asked.append(seconds)
            return True

    postgres, settings = Pinged(), make_settings(graph_connect_timeout_seconds=2.5)
    graph: Any = SimpleNamespace(driver=None, database="neo4j")  # never asked when PostgreSQL holds the figures
    assert await store_answers(graph, postgres, settings)  # ty: ignore[invalid-argument-type]
    assert postgres.asked == [2.5]
