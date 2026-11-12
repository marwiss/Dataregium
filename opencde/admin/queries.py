# This file was generated with the assistance of an AI coding tool.
"""Read-only aggregates for the admin pages, from the store that holds the data (ADR 0029).

These are the one place that does not start at a user: they serve only
administrators, who see the whole server. Each area answers for its own
figures (the users, the projects and every membership, the clients and the
live refresh tokens, the topics, documents, BCF documents and models of each
project, the request log's entries); :class:`Figures` puts them together for
the admin pages and ``/admin/api``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends

from opencde.deps import GraphDep, PostgresDep, RequestLogDep, SettingsDep
from opencde.graph.driver import ping_within
from opencde.graph.graph import Graph
from opencde.postgres.database import Postgres
from opencde.settings import Settings
from opencde.store.protocols import (
    BcfDocumentStore,
    ClientStore,
    ConversionStore,
    DocumentStore,
    ProjectStore,
    RequestLogStore,
    TokenStore,
    TopicStore,
    UserStore,
)


@dataclass(frozen=True)
class Figures:
    users: UserStore
    projects: ProjectStore
    clients: ClientStore
    tokens: TokenStore
    topics: TopicStore
    documents: DocumentStore
    bcf_documents: BcfDocumentStore
    conversions: ConversionStore
    request_log: RequestLogStore

    async def counts(self) -> dict[str, int]:
        return {
            "users": await self.users.count(),
            "projects": len(await self.projects.all()),
            "clients": len(await self.clients.all()),
            "topics": sum((await self.topics.counts_per_project()).values()),
            "documents": sum((await self.documents.counts_per_project()).values()),
            "bcf_documents": sum((await self.bcf_documents.counts_per_project()).values()),
            "sessions": await self.tokens.live_count(),
        }

    async def project_stats(self) -> dict[str, dict[str, int]]:
        """Members, topics and documents (the Documents API's and BCF's) of every project."""
        members = await self.members_by_project()
        topics = await self.topics.counts_per_project()
        documents = await self.documents.counts_per_project()
        bcf_documents = await self.bcf_documents.counts_per_project()
        return {
            project.project_id: {
                "members": len(members.get(project.project_id, [])),
                "topics": topics.get(project.project_id, 0),
                "documents": documents.get(project.project_id, 0) + bcf_documents.get(project.project_id, 0),
            }
            for project in await self.projects.all()
        }

    async def membership_counts(self) -> dict[str, int]:
        """Memberships per user id; a user who has none is not among them."""
        counts: dict[str, int] = {}
        for row in await self.projects.all_memberships():
            counts[row["user_id"]] = counts.get(row["user_id"], 0) + 1
        return counts

    async def memberships_by_user(self) -> dict[str, list[dict[str, str]]]:
        """``{user_id: [{"project_id", "role"}]}`` for every membership on the server, by project name."""
        result: dict[str, list[dict[str, str]]] = {}
        for row in sorted(await self.projects.all_memberships(), key=lambda row: row["project_name"]):
            result.setdefault(row["user_id"], []).append({"project_id": row["project_id"], "role": row["role"]})
        return result

    async def members_by_project(self) -> dict[str, list[dict[str, str]]]:
        """``{project_id: [{"user_id", "username", "role"}]}`` for every membership on the server, by user name."""
        result: dict[str, list[dict[str, str]]] = {}
        for row in sorted(await self.projects.all_memberships(), key=lambda row: row["username"]):
            result.setdefault(row["project_id"], []).append(
                {"user_id": row["user_id"], "username": row["username"], "role": row["role"]}
            )
        return result

    async def model_counts(self) -> dict[str, int]:
        """Imported IFC models per project id (every state), plus the total under ``""``."""
        counts = await self.conversions.counts_per_project()
        counts[""] = sum(counts.values())
        return counts


def figures(graph: GraphDep, postgres: PostgresDep, settings: SettingsDep, request_log: RequestLogDep) -> Figures:
    """The figures from the stores of their areas, which the dependencies of the routes choose likewise."""
    # The areas' modules import the routes' dependencies, which import this module.
    from opencde.store.accounts.postgres import project_store, user_store
    from opencde.store.bcf.postgres import bcf_document_store, topic_store
    from opencde.store.documents.postgres import document_store
    from opencde.store.jobs.postgres import conversion_store
    from opencde.store.oauth2.postgres import client_store, token_store

    return Figures(
        users=user_store(graph, postgres),
        projects=project_store(graph, postgres),
        clients=client_store(graph, postgres, settings),
        tokens=token_store(graph, postgres, settings),
        topics=topic_store(graph, postgres, id_prefix=settings.server_assigned_id_prefix),
        documents=document_store(graph, postgres),
        bcf_documents=bcf_document_store(graph, postgres),
        conversions=conversion_store(graph, postgres),
        request_log=request_log,
    )


FiguresDep = Annotated[Figures, Depends(figures)]


async def store_answers(graph: Graph, postgres: Postgres | None, settings: Settings) -> bool:
    """Whether the store of the figures answers: PostgreSQL when ``OPENCDE_STORE_URL`` names it, the graph
    otherwise."""
    if postgres is not None:
        return await postgres.ping(settings.graph_connect_timeout_seconds)
    return await ping_within(graph.driver, graph.database, settings.graph_connect_timeout_seconds)
