# Hanterad databas och containertjänst (utan egen server)

Kör API:t på en containertjänst (Fly.io, Render, Railway, Azure Container
Apps, Google Cloud Run), serverns egna data på en hanterad PostgreSQL och
grafen på en hanterad Neo4j (AuraDB) eller en Memgraph Cloud-instans. Grafen
håller IFC-DB:s modeller och hör till nivå 3 (`OPENCDE_TIER=experimental`);
nivå 1 och 2 behöver bara PostgreSQL.

## Databas

- **PostgreSQL** håller serverns egna data. Skapa en databas och notera dess
  värd, dess namn, användaren och lösenordet.
- **Neo4j AuraDB Free** passar för demonstrationer: ungefär 200 000 noder,
  pausas efter 72 timmar utan aktivitet och raderas efter 30 dagars paus.
  AuraDB Professional för allt som måste vara uppe.
- Skapa instansen och notera URI:n `neo4j+s://xxxx.databases.neo4j.io`,
  användaren och lösenordet.
- **Memgraph Cloud** fungerar på samma sätt med `bolt+s://` och
  `OPENCDE_GRAPH_DIALECT=memgraph`.

## API-container

Driftsätt `ghcr.io/marwiss/opencdeserver:<version>` med dessa variabler:

```
OPENCDE_ENVIRONMENT=production
OPENCDE_PUBLIC_URL=https://<tjänstens https-adress>
OPENCDE_SECRET_KEY=<48 slumpmässiga tecken>
OPENCDE_TIER=experimental
OPENCDE_STORE_URL=postgresql://<användare>@<värd>:5432/<databas>
OPENCDE_STORE_PASSWORD=<från leverantören>
OPENCDE_IFC_DB_ENABLED=true
OPENCDE_GRAPH_DIALECT=neo4j
OPENCDE_GRAPH_URI=neo4j+s://xxxx.databases.neo4j.io
OPENCDE_GRAPH_USER=neo4j
OPENCDE_GRAPH_PASSWORD=<från Aura>
OPENCDE_DATA_DIR=/data
OPENCDE_TRUSTED_PROXIES=["0.0.0.0/0"]
```

- `OPENCDE_STORE_URL` får inte innehålla lösenordet, som är
  `OPENCDE_STORE_PASSWORD`; produktion vägrar starta utan det och kräver
  `OPENCDE_GRAPH_PASSWORD` bara när en graf används.
- För nivå 1 eller 2, sätt `OPENCDE_TIER=documents` eller `bim-gis` och
  utelämna `OPENCDE_IFC_DB_ENABLED` och raderna `OPENCDE_GRAPH_*`: servern
  använder då ingen graf. Utan `OPENCDE_STORE_URL` håller grafen också
  serverns egna data, som före PostgreSQL (bara nivå 3).
- Avbildningen har asyncpg för PostgreSQL och IfcOpenShell för IFC-DB
  (`ARG OPENCDE_EXTRAS="--extra postgres --extra ifc-db"`); bygg den med
  `--build-arg OPENCDE_EXTRAS="--extra postgres"` för en slankare utan
  IfcOpenShell där IFC-DB förblir avstängd.
- Montera en beständig volym på `/data`: uppladdade dokument och bilder ligger
  där. Utan volym försvinner de vid nästa driftsättning.
- `OPENCDE_TRUSTED_PROXIES` är bred eftersom plattformens
  lastbalanseraradresser inte är fasta; plattformen garanterar att bara dess
  proxy når containern.
- Containern lyssnar på 8000 och svarar på `/healthz` (liveness) och `/readyz`
  (readiness, kontrollerar databaserna).
- Skapa den första administratören från plattformens skal:
  `opencde user create admin --admin`.

## Säkerhetskopior

Sidovagnen `backup` i `docker/compose.prod.yaml` gäller inte här. Schemalägg
`opencde backup /data/backups` (ett cron-jobb eller en schemalagd uppgift på
plattformen) och kopiera mappen till objektlagring; Aura har dessutom egna
snapshots.

## Egen server med databasen någon annanstans

Om du kör resten av stacken själv, som på [en egen server](vps-caddy.md), och
bara grafen någon annanstans, för IFC-DB:s modeller i nivå 3
(`OPENCDE_TIER=experimental` och `OPENCDE_IFC_DB_ENABLED=true`, som `opencde
init --tier experimental` skriver dem; utan IFC-DB använder servern ingen graf),
ange var den finns i `.env` och lämna grafcontainern utanför:

```
OPENCDE_STACK_GRAPH_URI=neo4j+s://xxxx.databases.neo4j.io
OPENCDE_GRAPH_USER=neo4j
OPENCDE_GRAPH_PASSWORD=<från Aura>
```

```bash
# .env: COMPOSE_FILE=docker/compose.yaml:docker/compose.prod.yaml:docker/compose.external-db.yaml
docker compose up -d
```

API:et och säkerhetskopieringen använder då den grafen;
`compose.external-db.yaml` lämnar grafcontainern utanför också när
`COMPOSE_PROFILES=graph` är satt. PostgreSQL i stacken håller serverns egna
data, om inte `OPENCDE_STACK_STORE_URL` anger en annan, som en hanterad, vars
lösenord då är `OPENCDE_STORE_PASSWORD`; satt men tom håller den dessa data i
grafen, som före PostgreSQL. För Memgraph, sätt också
`OPENCDE_GRAPH_DIALECT=memgraph`.
