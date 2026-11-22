# Managed database and container service (no server of your own)

Run the API on a container service (Fly.io, Render, Railway, Azure Container
Apps, Google Cloud Run), the server's own data on a managed PostgreSQL and the
graph on a managed Neo4j (AuraDB) or Memgraph Cloud instance. The graph holds
IFC-DB's models and is tier 3 (`OPENCDE_TIER=experimental`); tiers 1 and 2
need PostgreSQL alone.

## Database

- **PostgreSQL** holds the server's own data. Create a database and note its
  host, its name, the user and the password.
- **Neo4j AuraDB Free** suits demos: about 200 000 nodes, paused after 72 hours
  without activity and deleted after 30 days of pause. AuraDB Professional for
  anything that must stay up.
- Create the instance, note the `neo4j+s://xxxx.databases.neo4j.io` URI, the
  user and the password.
- **Memgraph Cloud** works the same way with `bolt+s://` and
  `OPENCDE_GRAPH_DIALECT=memgraph`.

## API container

Deploy `ghcr.io/marwiss/opencdeserver:<version>` with these variables:

```
OPENCDE_ENVIRONMENT=production
OPENCDE_PUBLIC_URL=https://<the service's https URL>
OPENCDE_SECRET_KEY=<48 random characters>
OPENCDE_TIER=experimental
OPENCDE_STORE_URL=postgresql://<user>@<host>:5432/<database>
OPENCDE_STORE_PASSWORD=<from the provider>
OPENCDE_IFC_DB_ENABLED=true
OPENCDE_GRAPH_DIALECT=neo4j
OPENCDE_GRAPH_URI=neo4j+s://xxxx.databases.neo4j.io
OPENCDE_GRAPH_USER=neo4j
OPENCDE_GRAPH_PASSWORD=<from Aura>
OPENCDE_DATA_DIR=/data
OPENCDE_TRUSTED_PROXIES=["0.0.0.0/0"]
```

- `OPENCDE_STORE_URL` must not hold the password, which is
  `OPENCDE_STORE_PASSWORD`; production refuses to start without it, and wants
  `OPENCDE_GRAPH_PASSWORD` only when a graph is used.
- For tier 1 or 2, set `OPENCDE_TIER=documents` or `bim-gis` and leave out
  `OPENCDE_IFC_DB_ENABLED` and the `OPENCDE_GRAPH_*` lines: the server then
  uses no graph. Without `OPENCDE_STORE_URL` the graph holds the server's own
  data too, as before PostgreSQL (tier 3 only).
- The image has asyncpg for PostgreSQL and IfcOpenShell for IFC-DB
  (`ARG OPENCDE_EXTRAS="--extra postgres --extra ifc-db"`); build it with
  `--build-arg OPENCDE_EXTRAS="--extra postgres"` for a slimmer one without
  IfcOpenShell where IFC-DB stays off.
- Mount a persistent volume at `/data`: uploaded documents and images live
  there. Without a volume they vanish on redeploy.
- `OPENCDE_TRUSTED_PROXIES` is wide because the platform's load balancer
  addresses are not fixed; the platform guarantees that only its proxy reaches
  the container.
- The container listens on 8000, answers `/healthz` (liveness) and `/readyz`
  (readiness, checks the databases).
- Create the first administrator from the platform's shell:
  `opencde user create admin --admin`.

## Backups

The `backup` sidecar of `docker/compose.prod.yaml` does not apply here. Schedule
`opencde backup /data/backups` (a cron job or a scheduled task on the platform)
and copy the folder to object storage; Aura has its own snapshots as well.

## Your own server with a database elsewhere

If you run the rest of the stack yourself, as in [your own server](vps-caddy.md), and only the
graph elsewhere, for IFC-DB's models in tier 3 (`OPENCDE_TIER=experimental` and
`OPENCDE_IFC_DB_ENABLED=true`, as `opencde init --tier experimental` writes
them; without IFC-DB the server uses no graph), say where it is in `.env` and
leave the graph container out:

```
OPENCDE_STACK_GRAPH_URI=neo4j+s://xxxx.databases.neo4j.io
OPENCDE_GRAPH_USER=neo4j
OPENCDE_GRAPH_PASSWORD=<from Aura>
```

```bash
# .env: COMPOSE_FILE=docker/compose.yaml:docker/compose.prod.yaml:docker/compose.external-db.yaml
docker compose up -d
```

The API and the backup sidecar then use that graph; `compose.external-db.yaml`
leaves the graph container out also when `COMPOSE_PROFILES=graph` is set.
PostgreSQL in the stack holds the server's own data, unless
`OPENCDE_STACK_STORE_URL` names another, such as a managed one, whose password
is then `OPENCDE_STORE_PASSWORD`; set but empty, it keeps that data in the
graph, as before PostgreSQL. For Memgraph, also set
`OPENCDE_GRAPH_DIALECT=memgraph`.
