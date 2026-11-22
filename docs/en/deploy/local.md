# Local test (Windows, macOS, Linux)

Everything runs in Docker on your machine; nothing is reachable from outside.

## Requirements

- Docker Desktop (Windows, macOS) or Docker Engine with the compose plugin (Linux)
- or Podman 5 with `podman compose`: the same files work, see [Podman](#podman) below
- [uv](https://docs.astral.sh/uv/) for the command line tools

## Steps

```sh
git clone https://github.com/marwiss/Dataregium.git
cd Dataregium
uv run opencde init --dev                                 # writes .env with fresh secrets
docker compose up -d --build
docker compose exec api opencde seed
```

`opencde init --dev` writes tier 2, BIM + GIS (`OPENCDE_TIER=bim-gis`):
PostgreSQL holds the server's own data, and no graph starts. `--tier experimental`
writes tier 3, Experimental, with IFC-DB on and Neo4j beside PostgreSQL
(`COMPOSE_PROFILES=graph`), and `--tier documents` tier 1, Documents.

`opencde seed` creates demonstration users (an administrator, a manager, an
editor, a contributor and a viewer), two projects and a public OAuth2 client,
and prints the passwords once.

| What | Where |
|---|---|
| API documentation | <http://127.0.0.1:8000/docs> |
| Administration | <http://127.0.0.1:8000/admin> (sign in as the administrator) |
| Readiness | <http://127.0.0.1:8000/readyz> |
| PostgreSQL | `postgresql://opencde@127.0.0.1:5442/opencde`, with `OPENCDE_STORE_PASSWORD` |
| Neo4j Browser, in tier 3 | <http://127.0.0.1:7484> (Bolt `127.0.0.1:7697`) |
| Server log | `docker compose logs -f api` |

Without a graph, as in tiers 1 and 2, `/readyz` answers
`{"status":"ready","graph":"none","store":"ok","workcopy":"none","store_dialect":"postgres"}`,
and 503 `{"status":"not_ready","graph":"none","store":"unavailable"}` while
PostgreSQL does not answer. In tier 3 `"graph"` is `"ok"` when the graph
answers too.

The development overlay publishes PostgreSQL on 5442 and, in tier 3, Neo4j on
7484/7697, rather than the defaults 5432 and 7474/7687, which another PostgreSQL
or Neo4j on the same machine often holds. Change the ports with `OPENCDE_DEV_*`
in `.env`. Two checkouts on one machine share the
Compose project name `dataregium` (`docker/compose.yaml` sets it); give the second one
`COMPOSE_PROJECT_NAME=dataregium-2` and other ports in its `.env`.

An installation made when the project was still called Kontroll has its data
in volumes named `kontroll_…`. It keeps them by setting
`COMPOSE_PROJECT_NAME=kontroll` in `.env`, which overrides the name in
`docker/compose.yaml`; without it, the next `up` starts a new project with empty
volumes.

An installation from before, with the server's own data in the graph, moves to
PostgreSQL with a backup and a restore, as
[From the graph to PostgreSQL](backup.md#from-the-graph-to-postgresql) says.

## Without Docker for the server

Run the databases in Docker (or the graph in Neo4j Desktop / Memgraph Lab) and
the server from the checkout:

```sh
uv sync
uv run opencde doctor
uv run opencde serve --reload
```

The server needs PostgreSQL, and in tier 3 the graph too; a server in tier 3
without `OPENCDE_STORE_URL` keeps its own data in the graph, as before
PostgreSQL, and needs only the graph. `.env` written by `opencde init --dev` points
at `postgresql://opencde@127.0.0.1:5442/opencde` and, in tier 3, at
`bolt://127.0.0.1:7697`: the ports of the development overlay.

## Memgraph instead of Neo4j

```sh
# .env: COMPOSE_FILE=docker/compose.yaml:docker/compose.memgraph.yaml:docker/compose.dev.yaml
docker compose up -d --build
```

Memgraph belongs to tier 3 like Neo4j: it starts with `COMPOSE_PROFILES=graph`
in `.env`, which `opencde init --dev --tier experimental` writes.
Set `OPENCDE_GRAPH_DIALECT=memgraph` and `OPENCDE_GRAPH_USER=memgraph` in `.env`
when running the server outside Docker. Memgraph Lab (`memgraph/lab`) can be
pointed at `127.0.0.1:7697`.

Memgraph keeps the graph in memory. `OPENCDE_MEMGRAPH_MEMORY_MIB` in `.env` caps
it (Memgraph's `--memory-limit`; `0`, the default, leaves it to Memgraph: 90 %
of the machine's memory, or all of it with swap). At the cap Memgraph refuses
what needs more: the server answers 503 `unavailable`, an import that does not
fit fails without leaving a part of the model behind, and from 95 % of the cap
`/readyz` answers 503 with `"graph": "full"`.

## Testing with Dataspatium

[Dataspatium](https://github.com/marwiss/Dataspatium) is the browser client
developed together with this server. Serve a checkout of it over http
(`python -m http.server 8080` in its folder), open `http://127.0.0.1:8080/`,
register the app as a public client, and add the server as an OpenCDE source
in Dataspatium (*Inhämta*) with `http://127.0.0.1:8000` and that client id:

```sh
uv run opencde client create "Dataspatium" --public --redirect-uri http://127.0.0.1:8080/oauth-callback.html
```

With `OPENCDE_DYNAMIC_CLIENT_REGISTRATION=true` in `.env` the app registers
itself instead. CORS allows every origin by default, so no further setting is
needed; `OPENCDE_REQUEST_LOG_ENABLED=true` lets `/admin/requests` show the
server side of the app's calls.
The [quick start](../quickstart.md) has the complete steps, in tier 3, and a
troubleshooting table.

## The local engine

Dataregium can run on a person's own computer as the engine behind Dataspatium
for models larger than a browser tab holds (ADR 0023). `opencde init --local`
writes a `.env` for that, always in tier 3: PostgreSQL for the server's own data,
the graph beside it for the models, and IFC-DB and the MCP endpoint on.
`OPENCDE_ENVIRONMENT=local` makes the server keep to it: the public URL must be
the loopback, the databases' passwords and a secret key are required, the CORS
origins must be named (`*` is refused), no database address may be announced
(`OPENCDE_GRAPH_PUBLIC_BOLT_URL`), and `opencde serve` refuses any interface but
the loopback, save the container's own in the engine's container (below). A
request whose `Host` is not `127.0.0.1`, `localhost` or
`::1` is answered `421`, before anything else, so that a page on the internet
cannot reach the engine through a name of its own that resolves to the
loopback. A public page that asks in its preflight
(`Access-Control-Request-Private-Network: true`) is allowed when its origin is
named. OAuth2 with PKCE is required locally as everywhere. The origins the
file writes are `null` (Dataspatium opened from a file), `http://127.0.0.1:8080`
(a local http server) and `https://marwiss.github.io`; drop `null` when the
app runs from an http origin.

The engine runs in its container or on the computer itself, with its databases
in Compose either way. In its container:

```sh
uv run opencde init --local   # .env: OPENCDE_ENVIRONMENT=local, random passwords, the origins Dataspatium comes from
docker compose up -d --build  # the API on http://127.0.0.1:8000 only; the databases are never published
```

`--build` builds the image from this checkout.

In its container the engine listens on the container's own interfaces, since
the container's loopback is not the computer's; `compose.local.yaml` publishes
the port on the computer's loopback alone and says so with
`OPENCDE_LOCAL_IN_CONTAINER=true`. `opencde serve` refuses that setting in a
process that runs in no container, and `opencde doctor` shows where the engine
listens. Never publish the engine's port on every interface, as
`docker run -p 8000:8000` does: the network would then reach an engine made for
one person, and the `Host` check stops a browser that a page has fooled, not a
program on the network that writes its own `Host`.

On the computer itself, without a container for the engine:

```sh
uv sync --extra postgres --extra ifc-db
uv run opencde init --local --no-container   # COMPOSE_FILE names compose.local-databases.yaml
docker compose up -d                         # PostgreSQL and Neo4j only, on 127.0.0.1:5442 and 127.0.0.1:7697
uv run opencde serve                         # the engine on http://127.0.0.1:8000, the loopback only
```

`compose.local-databases.yaml` publishes the databases on the computer's
loopback alone and starts no API container; programs on the same computer reach
the databases, with the passwords in `.env`. The `.env` of either way names the
databases on the loopback, and inside Compose the stack's own addresses replace
them. The engine on the computer keeps its files in the checkout's `data/`
(`OPENCDE_DATA_DIR`), the engine in its container in its volume, so keep to one
way for one installation.

## Podman

The Compose files work with Podman as well as Docker: the images have full names,
the data lives in named volumes, bind mounts carry the `z` label for SELinux, and
the development overlay binds ports above 1024 on 127.0.0.1. Run them with
`podman compose` (Podman 5), or with the `docker compose` command against Podman's
socket (`DOCKER_HOST=unix:///run/user/1000/podman/podman.sock`). Rootless Podman
cannot bind ports 80 and 443 for the production overlay: set
`sysctl net.ipv4.ip_unprivileged_port_start=80`, or run that stack as root. Podman
is free of charge (Apache-2.0); the tests run with Docker, and Podman is not part
of CI.

## Clean slate

```sh
docker compose down -v   # removes the volumes: databases and files
```

A server run on the computer itself keeps its files in the checkout's `data/`
instead; remove that folder too.
