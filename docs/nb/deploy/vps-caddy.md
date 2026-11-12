# Produksjon på egen server (Docker Compose + Caddy)

Én Linux-vert (2 vCPU og 4 GB RAM er rikelig til å begynne med), Docker med
compose-tillegget, et DNS-navn som peker på den, portene 80 og 443 åpne. Caddy
henter og fornyer TLS-sertifikatet; API-et, databasen og
sikkerhetskopieringsjobben kjører på et privat nettverk.

## 1. Forbered

```sh
git clone https://github.com/marwiss/Dataregium.git /opt/dataregium
cd /opt/dataregium
uv run opencde init --public-url https://cde.example.org
```

Uten `--public-url` spør `opencde init` etter verdiene og skriver deretter ut kommandoene som kommer etterpå, også for administratoren og den første klienten.

`init` skriver `.env` med `OPENCDE_ENVIRONMENT=production`, en hemmelig nøkkel,
et databasepassord og `OPENCDE_DOMAIN`. Legg til
`OPENCDE_ACME_EMAIL=deg@example.org` slik at Let's Encrypt kan nå deg om
sertifikatene. Hold `.env` privat (den er git-ignorert); det er den eneste
filen med hemmeligheter.

Uten uv på serveren: kopier `.env.example` til `.env` og fyll inn verdiene for
hånd (`python3 -c "import secrets; print(secrets.token_urlsafe(48))"` for den
hemmelige nøkkelen).

## 2. Start

```sh
docker compose pull
docker compose up -d
docker compose exec api opencde doctor
docker compose exec api opencde user create admin --admin
```

Den siste kommandoen skriver ut administratorens passord én gang. Logg inn på
`https://cde.example.org/signin`, åpne `/admin`, og opprett prosjektene,
brukerne og OAuth2-klientene programmene dine trenger.

## 3. Drift

| Oppgave | Kommando |
|---|---|
| Oppdater til nyeste image | `docker compose pull && docker compose up -d` |
| Lås en versjon | `OPENCDE_IMAGE_TAG=3.0.0` i `.env` (taggene følger GitHub-utgivelsene) |
| Tilby bare noen API-er | `OPENCDE_APIS=["bcf-2.1"]` i `.env` for en server for bare BCF API 2.1, `["documents"]` for en dokumentserver; alle tre som standard |
| Logger | `docker compose logs -f api` (JSON-linjer) |
| Helse | `https://cde.example.org/readyz`; siden Innstillinger i administrasjonen |
| Sikkerhetskopier | hver natt til volumet `backups` av tjenesten `backup`; se [backup.md](backup.md) |
| Trekk tilbake tilgang ved en hendelse | `docker compose ... exec api opencde revoke --all` (eller `--user`, `--client`) |

## 4. Sjekkliste for herding

- Databasen publiseres ikke; bare Caddy lytter på verten. Å publisere
  Bolt-porten for grafklienter er et bevisst skritt med eget overlegg og TLS,
  se [bolt.md](bolt.md); en åpen port gir full databasetilgang til den som har
  passordet.
- API-containeren er skrivebeskyttet, kjører uten root og uten capabilities;
  lagringen er et navngitt volum.
- `OPENCDE_TRUSTED_PROXIES` dekker compose-nettverket slik at klientadressen i
  revisjonsloggen er den virkelige; la den stå med mindre du endrer nettverket.
- Administrasjonen kan holdes til interne adresser. Legg dette til i nettstedsblokken i `Caddyfile`, og tilpass adressene; `/admin/api` følger med, så Dataspatiums administrasjon virker bare fra de adressene:

  ```caddyfile
  @admin_outside {
  	path /admin /admin/*
  	not remote_ip 10.0.0.0/8 192.168.0.0/16
  }
  respond @admin_outside 404
  ```

- API-et tillater 600 forespørsler i minuttet per adresse. Alle bak samme adresse deler dem, så hev grensen for et stort kontor, for eksempel `OPENCDE_RATE_LIMIT_API=3000/minute`. Det samme gjelder de 10 innloggingsforsøkene i minuttet, `OPENCDE_RATE_LIMIT_SIGN_IN=60/minute`; ventetiden per brukernavn stopper gjetting uansett.
- Behold `OPENCDE_DYNAMIC_CLIENT_REGISTRATION=false` med mindre du trenger
  selvbetjent klientregistrering (Foundation API 2.2.3); registrer klienter i
  `/admin`.
- Roter den hemmelige nøkkelen og databasepassordet etter din policy, start på nytt og logg inn igjen.
- Kjør `testssl.sh https://cde.example.org` og sjekk
  <https://observatory.mozilla.org> etter første start.
- Brannmur: tillat bare 22 (fra dine adresser), 80 og 443.

## En liten server

Med Memgraph på en maskin med 4 GB minne legger du til
`docker/compose.small.yaml` etter `docker/compose.memgraph.yaml`. Den setter
et tak for Memgraph og en grense for graf- og api-containerne, slik at en
modell som er for stor for maskinen, mislykkes alene i stedet for å ta
maskinens minne. Velg Memgraph når du setter opp serveren: Memgraph leser ikke
dataene til Neo4j.

```sh
# .env: COMPOSE_FILE=docker/compose.yaml:docker/compose.prod.yaml:docker/compose.memgraph.yaml:docker/compose.small.yaml
docker compose up -d
```

| Grense | For 4 GB | I `.env` |
|---|---|---|
| Memgraphs tak: grafen og spørringene mot den | 1536 MiB | `OPENCDE_MEMGRAPH_MEMORY_MIB` |
| Grafcontaineren | 2048 MiB | `OPENCDE_GRAPH_CONTAINER_MIB` |
| Api-containeren: serveren og IFC-arbeidsprosessen dens | 1024 MiB | `OPENCDE_API_CONTAINER_MIB` |
| Igjen til systemet, Caddy og sikkerhetskopieringen | omtrent 1 GB | |

Slik velger du dem for en annen maskin:

- La omtrent 1 GB være igjen til systemet, og del resten mellom containerne,
  omtrent to deler til grafen og én til api-et.
- Hold grafcontaineren 512 MiB over Memgraphs tak: Memgraph bruker noe minne
  den ikke teller, og containeren teller cachen for filene dens.
- Memgraph holdt de målte modellene i 2 til 12 ganger størrelsen på filene
  deres, for hver versjon den beholder.
- Serveren bruker omtrent 150 MiB i hvile og 350 MiB mens den importerer en
  modell med 40 000 entiteter. IFC-arbeidsprosessen trenger 10 til 20 ganger
  størrelsen på en stor fil, så 1024 MiB rekker til IFC-filer opp til omtrent
  40 MB.
- Hold `OPENCDE_IFC_DB_MEMORY_MB` (4096 som standard) minst 512 over
  api-containerens grense. Den begrenser adresserommet til arbeidsprosessen,
  som ligger omtrent 0,5 GiB over minnet arbeidsprosessen bruker, og en
  arbeidsprosess som stoppes der, kan henge til tidsgrensen dens er nådd. Ved
  containerens grense stopper kjernen arbeidsprosessen, den største prosessen i
  containeren, og importen mislykkes.
- Utelat `docker/compose.workcopy.yaml`: arbeidskopien er en andre Memgraph,
  uten tak.

Hva Memgraph gjør ved taket sitt, står i
[local.md](local.md#memgraph-i-stedet-for-neo4j). Overlegget er for Memgraph:
med Neo4j begrenser `NEO4J_HEAP` og `NEO4J_PAGECACHE` databasen (512m og 256m
som standard, omtrent 1 GB for containeren), noe som får plass i 4 GB som det
er.
