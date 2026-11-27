# Backup and restore

`opencde backup DIR` writes a folder `DIR/opencde-backup-<UTC time>/` with

| File | Content |
|---|---|
| `nodes.jsonl.gz` | every node: labels and properties, one JSON object per line |
| `relationships.jsonl.gz` | every relationship, with start and end node named by their unique keys |
| `store.jsonl.gz` | with PostgreSQL: every row of the server's own data, one JSON object per line |
| `storage.tar.gz` | the storage directory: documents, snapshots, bitmaps, snippets |
| `git/<project id>.bundle` | with `OPENCDE_GIT_ENABLED`: each project's repository, every ref |
| `manifest.json` | format, time, server version, dialect, counts, SHA-256 of each file |

Nodes are identified by the unique keys of the schema (`id`, `guid`,
`project_id`, …), never by database-internal ids, so a backup taken from Neo4j
restores into Memgraph and vice versa (ADR 0006). The database keeps running
during the backup; a backup taken while uploads are in progress may miss the
parts of those uploads, which the clients simply redo.

With the server's own data in PostgreSQL (`OPENCDE_STORE_URL`), as
`opencde init` and the Compose files set it up, its rows are in
`store.jsonl.gz`, each line `{"table": ..., "row": {...}}`, table after table
in the order of their foreign keys and all from one snapshot of the database.
`nodes.jsonl.gz` and `relationships.jsonl.gz` then hold IFC-DB's models alone,
and nothing on a server without a graph (tiers 1 and 2). `manifest.json` names
`postgres` in `store_dialect`, the number of rows in `rows`, the database's
last migration in `store_migration`, and `none` in `dialect` when there is no
graph. A server that keeps a repository per project (`OPENCDE_GIT_ENABLED`)
writes each as a bundle after the rows, so that it holds every commit they
name, with its SHA-256 among the others and their number in `repos`; the
restore verifies each and makes the project's repository from it, and refuses
a backup with repositories unless `OPENCDE_GIT_ENABLED` is set.

With the server's own data in a graph of its own (`OPENCDE_STORE_GRAPH_URI`,
[ifc-db.md](../ifc-db.md)), one backup holds both graphs in the same files,
IFC-DB's nodes from the IFC graph and the rest from the store, and
`manifest.json` names the store's dialect in `store_dialect`. A restore puts
each node where its label belongs, whichever graphs the backup came from, so a
backup of one database restores into two and the other way round; it refuses
unless both are empty or `--force` is given, and leaves out a relationship
between the two kinds, which only a database from before ADR 0027 can hold.

## Restore

```sh
opencde restore DIR/opencde-backup-20261105T031700Z           # into an empty database
opencde restore DIR/opencde-backup-20261105T031700Z --force   # replace the current contents
```

`restore` verifies the checksums, refuses a non-empty database unless `--force`,
recreates nodes and relationships in batches and unpacks the files into the
storage directory. Sessions and tokens are restored too; run
`opencde revoke --all` afterwards if the backup is old or came from another
server.

Into PostgreSQL, a backup of PostgreSQL goes into the empty tables of a
database with no migration after the backup's: `restore` migrates it up to the
backup's migration, puts the rows back in one transaction and then runs the
later migrations over them. A graph's backup restores into PostgreSQL too: the
server's own nodes and relationships become rows, and IFC-DB's nodes go to the
graph. What has no place in the tables is refused, all of it in one message,
and no row goes in. A backup that holds IFC-DB's models needs a server with a
graph (tier 3), and a backup of PostgreSQL does not restore into a graph.

## In Docker Compose

`docker/compose.prod.yaml` runs a `backup` service that calls `opencde backup /backups`
once a day and keeps the newest 14 folders in the `backups` volume. It waits for
PostgreSQL and backs it up with the stored files and, in tier 3, the graph's
models. Copy that volume somewhere else (another host, object storage); a backup
on the same disk as the data is only half a backup:

```sh
docker run --rm -v dataregium_backups:/backups -v "$PWD":/out alpine \
  tar czf /out/dataregium-backups.tgz -C /backups .
```

An installation that kept the old project name has the volume as `kontroll_backups`.

To restore on a fresh host, start the stack, copy a backup folder into the
`api` container (or mount it) and run
`docker compose exec api opencde restore /path --force`.

## From the graph to PostgreSQL

An installation from before PostgreSQL keeps the server's own data in the
graph. It moves to PostgreSQL with a backup and a restore. A backup written by
this release carries every node of the server's own data: those with a unique key,
and teams, the publication of a version, the components of viewpoints, the
trials, clash checks, merges and request log entries by the ids the server gave
them. A backup written by an earlier release leaves the latter out and says how
many nodes it left (`WARN … nodes without a key were not exported`). So update
the checkout and get this release's image first (`build api` instead of `pull api`
for a checkout that builds its own), and, with the old `.env` and the old stack's
graph still running, write the backup to the data volume, which the new stack
keeps, with this release's `opencde backup` in a one-off container. It needs
`OPENCDE_TIER=experimental` for that command, since this release keeps the
server's own data in a graph in tier 3 alone. The image's entrypoint is
`opencde`, so the command is given without it, and the container runs as the
user 10001, who must be able to write the folder (`/data` is theirs). Name the
release in `OPENCDE_IMAGE_TAG`, as the old `.env` may hold the old one:

```sh
OPENCDE_IMAGE_TAG=<release> docker compose pull api
OPENCDE_IMAGE_TAG=<release> docker compose run --rm --no-deps -e OPENCDE_TIER=experimental api backup /data/migration
```

Then remove `OPENCDE_STORE_GRAPH_URI` from `.env` if it is
set, and add `OPENCDE_STORE_PASSWORD` (a long random value) and `OPENCDE_TIER`
to `.env`, and `COMPOSE_PROFILES=graph` to keep IFC-DB's models in tier 3
(`OPENCDE_TIER=experimental`); or move the old `.env` aside, write a new one
with `opencde init` (`--tier experimental` to keep IFC-DB's models) and carry
the secrets over, such as `OPENCDE_SECRET_KEY` and `OPENCDE_GRAPH_PASSWORD`.
Start the new stack and restore the backup:

```sh
docker compose up -d
docker compose exec api opencde restore /data/migration/opencde-backup-<UTC time>
```

Add `--force` if it says the database is not empty. A backup of a graph
restores into PostgreSQL as rows, and a backup of PostgreSQL does not restore
into a graph.

## Test your restore

A backup you have never restored is a hope, not a backup. The integration test
`tests/integration/test_backup.py` restores a backup into an empty database on
both Neo4j and Memgraph, and `tests/integration/test_postgres_cli.py` restores
backups of PostgreSQL and of a graph into PostgreSQL; do the same once on a
staging instance after setting up production.
