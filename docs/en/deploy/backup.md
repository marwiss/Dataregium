# Backup and restore

`opencde backup DIR` writes a folder `DIR/opencde-backup-<UTC time>/` with

| File | Content |
|---|---|
| `nodes.jsonl.gz` | every node: labels and properties, one JSON object per line |
| `relationships.jsonl.gz` | every relationship, with start and end node named by their unique keys |
| `store.jsonl.gz` | with PostgreSQL: every row of the server's own data, one JSON object per line |
| `storage.tar.gz` | the storage directory: documents, snapshots, bitmaps, snippets |
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
graph.

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
graph. It moves to PostgreSQL with a backup and a restore, which carry the nodes
that have a unique key: teams, the publication of a version, the components of
viewpoints and the trials, clash checks and merges are not in a graph's backup
and stay behind, and the backup says how many nodes it left out (`WARN … nodes
without a unique key were not exported`). With the old stack running, write a
backup to the data volume, which the new stack keeps:

```sh
docker compose exec api opencde backup /data/migration
```

Then update the checkout, remove `OPENCDE_STORE_GRAPH_URI` from `.env` if it is
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
