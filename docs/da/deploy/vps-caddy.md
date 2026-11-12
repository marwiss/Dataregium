# Produktion på egen server (Docker Compose + Caddy)

En Linux-vært (2 vCPU og 4 GB RAM er rigeligt til at begynde med), Docker med
compose-pluginet, et DNS-navn, der peger på den, portene 80 og 443 åbne. Caddy
henter og fornyer TLS-certifikatet; API'et, databasen og
sikkerhedskopieringsjobbet kører på et privat netværk.

## 1. Forbered

```sh
git clone https://github.com/marwiss/Dataregium.git /opt/dataregium
cd /opt/dataregium
uv run opencde init --public-url https://cde.example.org
```

Uden `--public-url` spørger `opencde init` om værdierne og udskriver derefter de næste kommandoer, også til administratoren og den første klient.

`init` skriver `.env` med `OPENCDE_ENVIRONMENT=production`, en hemmelig nøgle,
et databasekodeord og `OPENCDE_DOMAIN`. Tilføj
`OPENCDE_ACME_EMAIL=dig@example.org`, så Let's Encrypt kan kontakte dig om
certifikaterne. Hold `.env` privat (den er git-ignoreret); det er den eneste
fil med hemmeligheder.

Uden uv på serveren: kopiér `.env.example` til `.env`, og udfyld værdierne i
hånden (`python3 -c "import secrets; print(secrets.token_urlsafe(48))"` til
den hemmelige nøgle).

## 2. Start

```sh
docker compose pull
docker compose up -d
docker compose exec api opencde doctor
docker compose exec api opencde user create admin --admin
```

Den sidste kommando udskriver administratorens kodeord én gang. Log ind på
`https://cde.example.org/signin`, åbn `/admin`, og opret de projekter, brugere
og OAuth2-klienter, dine programmer har brug for.

## 3. Drift

| Opgave | Kommando |
|---|---|
| Opdatér til det nyeste image | `docker compose pull && docker compose up -d` |
| Fastlås en version | `OPENCDE_IMAGE_TAG=3.0.0` i `.env` (tags følger GitHub-udgivelserne) |
| Tilbyd kun nogle API'er | `OPENCDE_APIS=["bcf-2.1"]` i `.env` for en server til BCF API 2.1 alene, `["documents"]` for en dokumentserver; alle tre som standard |
| Logs | `docker compose logs -f api` (JSON-linjer) |
| Helbred | `https://cde.example.org/readyz`; siden Indstillinger i administrationen |
| Sikkerhedskopier | hver nat til volumen `backups` af tjenesten `backup`; se [backup.md](backup.md) |
| Tilbagekald adgang ved en hændelse | `docker compose ... exec api opencde revoke --all` (eller `--user`, `--client`) |

## 4. Tjekliste for hærdning

- Databasen udgives ikke; kun Caddy lytter på værten. At udgive Bolt-porten
  til grafklienter er et bevidst skridt med sin egen overlejring og TLS, se
  [bolt.md](bolt.md); en åben port giver fuld databaseadgang til den, der har
  kodeordet.
- API-containeren er skrivebeskyttet, kører uden root og uden capabilities;
  lageret er en navngivet volume.
- `OPENCDE_TRUSTED_PROXIES` dækker compose-netværket, så klientadressen i
  revisionsloggen er den rigtige; lad den være, medmindre du ændrer netværket.
- Administrationen kan holdes til interne adresser. Tilføj dette til site-blokken i `Caddyfile`, og tilpas adresserne; `/admin/api` følger med, så Dataspatiums administration kun virker fra de adresser:

  ```caddyfile
  @admin_outside {
  	path /admin /admin/*
  	not remote_ip 10.0.0.0/8 192.168.0.0/16
  }
  respond @admin_outside 404
  ```

- API'et tillader 600 forespørgsler i minuttet per adresse. Alle bag samme adresse deler dem, så hæv grænsen for et stort kontor, for eksempel `OPENCDE_RATE_LIMIT_API=3000/minute`. Det samme gælder de 10 loginforsøg i minuttet, `OPENCDE_RATE_LIMIT_SIGN_IN=60/minute`; ventetiden per brugernavn stopper gæt alligevel.
- Behold `OPENCDE_DYNAMIC_CLIENT_REGISTRATION=false`, medmindre du har brug for
  selvbetjent klientregistrering (Foundation API 2.2.3); registrér klienter i
  `/admin`.
- Rotér den hemmelige nøgle og databasekodeordet efter din politik, genstart, og log ind igen.
- Kør `testssl.sh https://cde.example.org`, og tjek
  <https://observatory.mozilla.org> efter den første start.
- Firewall: tillad kun 22 (fra dine adresser), 80 og 443.

## En lille server

Med Memgraph på en maskine med 4 GB hukommelse tilføjer du
`docker/compose.small.yaml` efter `docker/compose.memgraph.yaml`. Den sætter
et loft for Memgraph og en grænse for graf- og api-containerne, så en model,
der er for stor til maskinen, mislykkes alene i stedet for at tage maskinens
hukommelse. Vælg Memgraph, når du sætter serveren op: Memgraph læser ikke
Neo4j's data.

```sh
# .env: COMPOSE_FILE=docker/compose.yaml:docker/compose.prod.yaml:docker/compose.memgraph.yaml:docker/compose.small.yaml
docker compose up -d
```

| Grænse | Til 4 GB | I `.env` |
|---|---|---|
| Memgraphs loft: grafen og dens forespørgsler | 1536 MiB | `OPENCDE_MEMGRAPH_MEMORY_MIB` |
| Grafcontaineren | 2048 MiB | `OPENCDE_GRAPH_CONTAINER_MIB` |
| Api-containeren: serveren og dens IFC-arbejdsproces | 1024 MiB | `OPENCDE_API_CONTAINER_MIB` |
| Tilbage til systemet, Caddy og sikkerhedskopieringen | omkring 1 GB | |

Sådan vælger du dem til en anden maskine:

- Lad omkring 1 GB være til systemet, og del resten mellem containerne,
  omkring to dele til grafen og én til api'et.
- Hold grafcontaineren 512 MiB over Memgraphs loft: Memgraph bruger noget
  hukommelse, den ikke tæller, og containeren tæller cachen for dens filer.
- Memgraph holdt de målte modeller i 2 til 12 gange størrelsen af deres filer,
  for hver version, den beholder.
- Serveren bruger omkring 150 MiB i hvile og 350 MiB, mens den importerer en
  model med 40.000 entiteter. IFC-arbejdsprocessen kræver 10 til 20 gange
  størrelsen af en stor fil, så 1024 MiB rækker til IFC-filer op til omkring
  40 MB.
- Hold `OPENCDE_IFC_DB_MEMORY_MB` (4096 som standard) mindst 512 over
  api-containerens grænse. Den begrænser arbejdsprocessens adresserum, som
  ligger omkring 0,5 GiB over den hukommelse, arbejdsprocessen bruger, og en
  arbejdsproces, der stoppes dér, kan hænge, til dens tidsgrænse er nået. Ved
  containerens grænse stopper kernen arbejdsprocessen, den største proces i
  containeren, og importen mislykkes.
- Udelad `docker/compose.workcopy.yaml`: arbejdskopien er en anden Memgraph,
  uden loft.

Hvad Memgraph gør ved sit loft, står i
[local.md](local.md#memgraph-i-stedet-for-neo4j). Overlejringen er til
Memgraph: med Neo4j begrænser `NEO4J_HEAP` og `NEO4J_PAGECACHE` databasen
(512m og 256m som standard, omkring 1 GB til containeren), hvilket passer i
4 GB, som det er.
