# Produksjon på eigen server (Docker Compose + Caddy)

Éin Linux-blir (2 vCPU og 4 GB RAM er rikeleg til å byrje med), Docker med
compose-tillegget, eit DNS-namn som peikar på han, portane 80 og 443 opne.
Caddy hentar og fornyar TLS-sertifikatet; API-et, databasen og
tryggingskopieringsjobben køyrer på eit privat nettverk.

## 1. Førebu

```sh
git clone https://github.com/marwiss/Dataregium.git /opt/dataregium
cd /opt/dataregium
uv run opencde init --public-url https://cde.example.org
```

Utan `--public-url` spør `opencde init` etter verdiane og skriv deretter ut kommandoane som kjem etterpå, òg for administratoren og den første klienten.

`init` skriv `.env` med `OPENCDE_ENVIRONMENT=production`, ein løynd nøkkel,
eit databasepassord og `OPENCDE_DOMAIN`. Legg til
`OPENCDE_ACME_EMAIL=deg@example.org` slik at Let's Encrypt kan nå deg om
sertifikata. Hald `.env` privat (han er git-ignorert); det er den einaste fila
med løyndomar.

Utan uv på serveren: kopier `.env.example` til `.env` og fyll inn verdiane for
hand (`python3 -c "import secrets; print(secrets.token_urlsafe(48))"` for den
løynde nøkkelen).

## 2. Start

```sh
docker compose pull
docker compose up -d
docker compose exec api opencde doctor
docker compose exec api opencde user create admin --admin
```

Den siste kommandoen skriv ut passordet til administratoren éin gong. Logg inn
på `https://cde.example.org/signin`, opne `/admin`, og opprett prosjekta,
brukarane og OAuth2-klientane programma dine treng.

## 3. Drift

| Oppgåve | Kommando |
|---|---|
| Oppdater til nyaste image | `docker compose pull && docker compose up -d` |
| Lås ein versjon | `OPENCDE_IMAGE_TAG=3.0.0` i `.env` (taggane følgjer GitHub-utgåvene) |
| Tilby berre nokre API-ar | `OPENCDE_APIS=["bcf-2.1"]` i `.env` for ein tenar for berre BCF API 2.1, `["documents"]` for ein dokumenttenar; alle tre som standard |
| Loggar | `docker compose logs -f api` (JSON-linjer) |
| Helse | `https://cde.example.org/readyz`; sida Innstillingar i administrasjonen |
| Tryggingskopiar | kvar natt til volumet `backups` av tenesta `backup`; sjå [backup.md](backup.md) |
| Trekk tilbake tilgang ved ei hending | `docker compose ... exec api opencde revoke --all` (eller `--user`, `--client`) |

## 4. Sjekkliste for herding

- Databasen blir ikkje publisert; berre Caddy lyttar på verten. Å publisere
  Bolt-porten for grafklientar er eit medvite steg med eige overlegg og TLS,
  sjå [bolt.md](bolt.md); ein open port gjev full databasetilgang til den som
  har passordet.
- API-containeren er skriveverna, køyrer utan root og utan capabilities;
  lagringa er eit namngjeve volum.
- `OPENCDE_TRUSTED_PROXIES` dekkjer compose-nettverket slik at klientadressa i
  revisjonsloggen er den verkelege; lat han stå med mindre du endrar
  nettverket.
- Administrasjonen kan haldast til interne adresser. Legg dette til i nettstadblokka i `Caddyfile`, og tilpass adressene; `/admin/api` følgjer med, så administrasjonen i Dataspatium verkar berre frå dei adressene:

  ```caddyfile
  @admin_outside {
  	path /admin /admin/*
  	not remote_ip 10.0.0.0/8 192.168.0.0/16
  }
  respond @admin_outside 404
  ```

- API-et tillèt 600 førespurnader i minuttet per adresse. Alle bak same adresse deler dei, så hev grensa for eit stort kontor, til dømes `OPENCDE_RATE_LIMIT_API=3000/minute`. Det same gjeld dei 10 innloggingsforsøka i minuttet, `OPENCDE_RATE_LIMIT_SIGN_IN=60/minute`; ventetida per brukarnamn stoppar gjetting uansett.
- Hald på `OPENCDE_DYNAMIC_CLIENT_REGISTRATION=false` med mindre du treng
  sjølvbetent klientregistrering (Foundation API 2.2.3); registrer klientar i
  `/admin`.
- Roter den løynde nøkkelen og databasepassordet etter din policy, start på nytt og logg inn igjen.
- Køyr `testssl.sh https://cde.example.org` og sjekk
  <https://observatory.mozilla.org> etter første start.
- Brannmur: tillat berre 22 (frå adressene dine), 80 og 443.

## Ein liten server

Med Memgraph på ei maskin med 4 GB minne legg du til
`docker/compose.small.yaml` etter `docker/compose.memgraph.yaml`. Overlegget
set eit tak for Memgraph og ei grense for graf- og api-containerane, slik at
ein modell som er for stor for maskina, mislukkast åleine i staden for å ta
minnet til maskina. Vel Memgraph når du set opp serveren: Memgraph les ikkje
dataa til Neo4j.

```sh
# .env: COMPOSE_FILE=docker/compose.yaml:docker/compose.prod.yaml:docker/compose.memgraph.yaml:docker/compose.small.yaml
docker compose up -d
```

| Grense | For 4 GB | I `.env` |
|---|---|---|
| Taket til Memgraph: grafen og spørjingane mot han | 1536 MiB | `OPENCDE_MEMGRAPH_MEMORY_MIB` |
| Grafcontaineren | 2048 MiB | `OPENCDE_GRAPH_CONTAINER_MIB` |
| Api-containeren: serveren og IFC-arbeidsprosessen hans | 1024 MiB | `OPENCDE_API_CONTAINER_MIB` |
| Att til systemet, Caddy og tryggingskopieringa | om lag 1 GB | |

Slik vel du dei for ei anna maskin:

- La om lag 1 GB vere att til systemet, og del resten mellom containerane, om
  lag to delar til grafen og éin til api-et.
- Hald grafcontaineren 512 MiB over taket til Memgraph: Memgraph brukar noko
  minne som ikkje blir rekna, og containeren reknar med cachen for filene.
- Memgraph heldt dei målte modellane i 2 til 12 gonger storleiken på filene
  deira, for kvar versjon som blir teken vare på.
- Serveren brukar om lag 150 MiB i kvile og 350 MiB medan han importerer ein
  modell med 40 000 entitetar. IFC-arbeidsprosessen treng 10 til 20 gonger
  storleiken på ei stor fil, så 1024 MiB rekk til IFC-filer opp til om lag
  40 MB.
- Hald `OPENCDE_IFC_DB_MEMORY_MB` (4096 som standard) minst 512 over grensa til
  api-containeren. Innstillinga avgrensar adresserommet til arbeidsprosessen,
  som ligg om lag 0,5 GiB over minnet arbeidsprosessen brukar, og ein
  arbeidsprosess som blir stoppa der, kan henge til tidsgrensa er nådd. Ved
  grensa til containeren stoppar kjernen arbeidsprosessen, den største
  prosessen i containeren, og importen mislukkast.
- Utelat `docker/compose.workcopy.yaml`: arbeidskopien er ein andre Memgraph,
  utan tak.

Kva Memgraph gjer ved taket, står i
[local.md](local.md#memgraph-i-staden-for-neo4j). Overlegget er for Memgraph:
med Neo4j avgrensar `NEO4J_HEAP` og `NEO4J_PAGECACHE` databasen (512m og 256m
som standard, om lag 1 GB for containeren), noko som får plass i 4 GB som det
er.
