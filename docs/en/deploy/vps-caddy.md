# Production on your own server (Docker Compose + Caddy)

One Linux host (2 vCPU, 4 GB RAM is plenty to start), Docker with the compose
plugin, a DNS name pointing at it, ports 80 and 443 open. Caddy obtains and
renews the TLS certificate; the API, PostgreSQL and the backup job run on a
private network.

## 1. Prepare

```sh
git clone https://github.com/marwiss/Dataregium.git /opt/dataregium
cd /opt/dataregium
uv run opencde init --public-url https://cde.example.org
```

Without `--public-url`, `opencde init` asks for the values and then prints the commands that come next, for the administrator and the first client too.

`init` writes `.env` with `OPENCDE_ENVIRONMENT=production`,
`OPENCDE_TIER=bim-gis`, a secret key, the address of PostgreSQL in the stack
(`OPENCDE_STORE_URL=postgresql://opencde@store:5432/opencde`), a random password
for it (`OPENCDE_STORE_PASSWORD`, which Compose requires) and `OPENCDE_DOMAIN`.
Add `OPENCDE_ACME_EMAIL=you@example.org` so Let's Encrypt can reach you about
certificates. Keep `.env` private (it is git-ignored); it is the only file with
secrets. Production refuses to start without PostgreSQL's password, and without
the graph's (`OPENCDE_GRAPH_PASSWORD`) only when it uses a graph.

The tier is what the server offers. Tier 1, Documents (`--tier documents`),
offers the Foundation and Documents APIs; tier 2, BIM + GIS (`bim-gis`, which
`init` writes unless told otherwise), adds BCF API 3.0 and 2.1, the MCP
endpoint and the BIM functions on the IFC files, which `init` turns on with
`OPENCDE_BIM_ENABLED=true`. Both keep the server's own data in PostgreSQL and
use no graph.
Tier 3, Experimental (`--tier experimental`, or a yes to the guide's question
about IFC models in a graph), adds IFC-DB's graph: `init` then also writes the
graph's settings, `OPENCDE_IFC_DB_ENABLED=true` and `COMPOSE_PROFILES=graph`,
which starts Neo4j beside PostgreSQL. A function can be off within the tier,
never on above it: the server refuses to start, and names the setting, when the
APIs, the MCP endpoint or IFC-DB ask for more than the tier offers.
`opencde doctor` and the administration's overview show the tier.

Without uv on the server: copy `.env.example` to `.env` and fill in the values
by hand (`python3 -c "import secrets; print(secrets.token_urlsafe(48))"` for
the secret key and for PostgreSQL's password).

## 2. Start

```sh
docker compose pull
docker compose up -d
docker compose exec api opencde doctor
docker compose exec api opencde user create admin --admin
```

The last command prints the administrator's password once. Sign in at
`https://cde.example.org/signin`, open `/admin`, create projects, users and the
OAuth2 clients your applications need.

The API waits for PostgreSQL, the service `store`, as it starts; the graph
starts only in tier 3. Without a graph, `/readyz` answers
`{"status":"ready","graph":"none","store":"ok","workcopy":"none","store_dialect":"postgres"}`,
and 503 with `{"status":"not_ready","graph":"none","store":"unavailable"}` while
PostgreSQL does not answer.

## 3. Operate

| Task | Command |
|---|---|
| Update to the latest image | `docker compose pull && docker compose up -d` |
| Pin a version | `OPENCDE_IMAGE_TAG=0.1.0` in `.env` (tags follow the GitHub releases) |
| Offer only some APIs | `OPENCDE_APIS=["bcf-2.1"]` in `.env` for a server for BCF API 2.1 alone, `["documents"]` for a document server; all three by default, and the Documents API alone in tier 1 |
| Logs | `docker compose logs -f api` (JSON lines) |
| Health | `https://cde.example.org/readyz`; the admin UI's Settings page |
| Backups | nightly into the `backups` volume by the `backup` service: PostgreSQL, the stored files and, in tier 3, the graph's models; see [backup.md](backup.md) |
| Revoke access in an incident | `docker compose ... exec api opencde revoke --all` (or `--user`, `--client`) |

An installation from before PostgreSQL keeps the server's own data in the
graph. It moves to PostgreSQL with a backup and a restore, see
[From the graph to PostgreSQL](backup.md#from-the-graph-to-postgresql).

## 4. Hardening checklist

- Neither PostgreSQL nor the graph is published; only Caddy listens on the
  host. Publishing the graph's Bolt port in tier 3 for graph clients is a
  deliberate step with its own overlay and TLS, see [bolt.md](bolt.md); an open
  port gives full database access to whoever has the password.
- The API container is read-only, non-root, without capabilities; storage is a
  named volume.
- `OPENCDE_TRUSTED_PROXIES` covers the compose network so the client address in
  the audit log is the real one; leave it unless you change the network.
- The administration can be kept to internal addresses. Add this to the site block in `Caddyfile` and adapt the addresses; `/admin/api` goes with it, so Dataspatium's administration works only from those addresses:

  ```caddyfile
  @admin_outside {
  	path /admin /admin/*
  	not remote_ip 10.0.0.0/8 192.168.0.0/16
  }
  respond @admin_outside 404
  ```

- The API allows 600 requests a minute per address. Everyone behind one address shares them, so raise the limit for a large office, for example `OPENCDE_RATE_LIMIT_API=3000/minute`. The same goes for the 10 sign-in attempts a minute, `OPENCDE_RATE_LIMIT_SIGN_IN=60/minute`; the wait per user name stops guessing anyway.
- Keep `OPENCDE_DYNAMIC_CLIENT_REGISTRATION=false` unless you need self-service
  client registration (Foundation API 2.2.3); register clients in `/admin`.
- Rotate the secret key and database password per your policy, restart, and re-sign in.
- Run `testssl.sh https://cde.example.org` and check
  <https://observatory.mozilla.org> after the first start.
- Firewall: allow 22 (from your addresses), 80 and 443 only.

## A small server

In tier 3 with Memgraph as the graph, on a machine with 4 GB of memory, add
`docker/compose.small.yaml` after `docker/compose.memgraph.yaml`. It caps
Memgraph and limits the graph and api containers, so that a model too large for
the machine fails alone instead of taking the machine's memory. Choose Memgraph
when you set the server up: it does not read Neo4j's data.

```sh
# .env: COMPOSE_FILE=docker/compose.yaml:docker/compose.prod.yaml:docker/compose.memgraph.yaml:docker/compose.small.yaml
# .env: COMPOSE_PROFILES=graph
docker compose up -d
```

| Limit | For 4 GB | In `.env` |
|---|---|---|
| Memgraph's cap: the graph and its queries | 1536 MiB | `OPENCDE_MEMGRAPH_MEMORY_MIB` |
| The graph container | 2048 MiB | `OPENCDE_GRAPH_CONTAINER_MIB` |
| The api container: the server and its IFC worker | 1024 MiB | `OPENCDE_API_CONTAINER_MIB` |
| Left to the system, PostgreSQL, Caddy and the backup | about 1 GB | |

To choose them for another machine:

- Leave about 1 GB to the system and share the rest between the containers,
  about two parts to the graph and one to the api.
- Keep the graph container 512 MiB above Memgraph's cap: Memgraph uses some
  memory it does not count, and the container counts the cache of its files.
- Memgraph held the measured models in 2 to 12 times the size of their files,
  for every version it keeps.
- The server uses about 150 MiB at rest and 350 MiB while it imports a model
  of 40,000 entities. The IFC worker needs 10 to 20 times the size of a large
  file, so 1024 MiB takes IFC files up to about 40 MB.
- Keep `OPENCDE_IFC_DB_MEMORY_MB` (4096 by default) at least 512 above the api
  container's limit. It limits the worker's address space, which runs about
  0.5 GiB above the memory the worker uses, and a worker stopped there can hang
  until its time limit. At the container's limit the kernel stops the worker,
  the largest process in it, and the import fails.
- Leave out `docker/compose.workcopy.yaml`: the working copy is a second
  Memgraph, without a cap.

What Memgraph does at its cap is in
[local.md](local.md#memgraph-instead-of-neo4j). The overlay is for Memgraph:
with Neo4j, `NEO4J_HEAP` and `NEO4J_PAGECACHE` bound the database (512m and
256m by default, about 1 GB for the container), which fits 4 GB as it is.
