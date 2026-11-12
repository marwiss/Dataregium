# Produktion på egen server (Docker Compose + Caddy)

En Linux-värd (2 vCPU och 4 GB RAM räcker gott i början), Docker med
compose-tillägget, ett DNS-namn som pekar på den, portarna 80 och 443 öppna.
Caddy hämtar och förnyar TLS-certifikatet; API:t, databasen och
säkerhetskopieringsjobbet körs på ett privat nät.

## 1. Förbered

```sh
git clone https://github.com/marwiss/Dataregium.git /opt/dataregium
cd /opt/dataregium
uv run opencde init --public-url https://cde.example.org
```

Utan `--public-url` frågar `opencde init` efter värdena och skriver sedan ut kommandona som kommer härnäst, även för administratören och den första klienten.

`init` skriver `.env` med `OPENCDE_ENVIRONMENT=production`, en hemlig nyckel,
ett databaslösenord och `OPENCDE_DOMAIN`. Lägg till
`OPENCDE_ACME_EMAIL=du@example.org` så att Let's Encrypt kan nå dig om
certifikaten. Håll `.env` privat (den är git-ignorerad); det är den enda filen
med hemligheter.

Utan uv på servern: kopiera `.env.example` till `.env` och fyll i värdena för
hand (`python3 -c "import secrets; print(secrets.token_urlsafe(48))"` för den
hemliga nyckeln).

## 2. Starta

```sh
docker compose pull
docker compose up -d
docker compose exec api opencde doctor
docker compose exec api opencde user create admin --admin
```

Det sista kommandot skriver ut administratörens lösenord en gång. Logga in på
`https://cde.example.org/signin`, öppna `/admin` och skapa de projekt,
användare och OAuth2-klienter som dina program behöver.

## 3. Drift

| Uppgift | Kommando |
|---|---|
| Uppdatera till senaste image | `docker compose pull && docker compose up -d` |
| Lås en version | `OPENCDE_IMAGE_TAG=3.0.0` i `.env` (taggarna följer GitHub-releaserna) |
| Erbjud bara några API:er | `OPENCDE_APIS=["bcf-2.1"]` i `.env` för en server för bara BCF API 2.1, `["documents"]` för en dokumentserver; alla tre som standard |
| Loggar | `docker compose logs -f api` (JSON-rader) |
| Hälsa | `https://cde.example.org/readyz`; sidan Inställningar i administrationen |
| Säkerhetskopior | nattligen till volymen `backups` av tjänsten `backup`; se [backup.md](backup.md) |
| Återkalla åtkomst vid en incident | `docker compose ... exec api opencde revoke --all` (eller `--user`, `--client`) |

## 4. Checklista för härdning

- Databasen publiceras inte; bara Caddy lyssnar på värden. Att publicera
  Bolt-porten för grafklienter är ett medvetet steg med egen överlagring och
  TLS, se [bolt.md](bolt.md); en öppen port ger full databasåtkomst till den
  som har lösenordet.
- API-containern är skrivskyddad, körs utan root och utan capabilities;
  lagringen är en namngiven volym.
- `OPENCDE_TRUSTED_PROXIES` täcker compose-nätet så att klientadressen i
  revisionsloggen är den riktiga; låt den vara om du inte ändrar nätet.
- Administrationen kan hållas till interna adresser. Lägg till det här i webbplatsblocket i `Caddyfile` och anpassa adresserna; `/admin/api` följer med, så Dataspatiums administration fungerar bara från de adresserna:

  ```caddyfile
  @admin_outside {
  	path /admin /admin/*
  	not remote_ip 10.0.0.0/8 192.168.0.0/16
  }
  respond @admin_outside 404
  ```

- API:et tillåter 600 anrop i minuten per adress. Alla bakom samma adress delar på dem, så höj gränsen för ett stort kontor, till exempel `OPENCDE_RATE_LIMIT_API=3000/minute`. Detsamma gäller de 10 inloggningsförsöken i minuten, `OPENCDE_RATE_LIMIT_SIGN_IN=60/minute`; väntetiden per användarnamn stoppar gissningar ändå.
- Behåll `OPENCDE_DYNAMIC_CLIENT_REGISTRATION=false` om du inte behöver
  självbetjänad klientregistrering (Foundation API 2.2.3); registrera klienter
  i `/admin`.
- Rotera den hemliga nyckeln och databaslösenordet enligt din policy, starta om och logga in igen.
- Kör `testssl.sh https://cde.example.org` och kontrollera
  <https://observatory.mozilla.org> efter första starten.
- Brandvägg: tillåt bara 22 (från dina adresser), 80 och 443.

## En liten server

Med Memgraph på en maskin med 4 GB minne lägger du till
`docker/compose.small.yaml` efter `docker/compose.memgraph.yaml`. Den sätter
ett tak för Memgraph och en gräns för graf- och api-containrarna, så att en
modell som är för stor för maskinen misslyckas ensam i stället för att ta
maskinens minne. Välj Memgraph när du sätter upp servern: Memgraph läser inte
Neo4j:s data.

```sh
# .env: COMPOSE_FILE=docker/compose.yaml:docker/compose.prod.yaml:docker/compose.memgraph.yaml:docker/compose.small.yaml
docker compose up -d
```

| Gräns | För 4 GB | I `.env` |
|---|---|---|
| Memgraphs tak: grafen och dess frågor | 1536 MiB | `OPENCDE_MEMGRAPH_MEMORY_MIB` |
| Grafcontainern | 2048 MiB | `OPENCDE_GRAPH_CONTAINER_MIB` |
| Api-containern: servern och dess IFC-arbetsprocess | 1024 MiB | `OPENCDE_API_CONTAINER_MIB` |
| Kvar åt systemet, Caddy och säkerhetskopieringen | ungefär 1 GB | |

Så väljer du dem för en annan maskin:

- Lämna ungefär 1 GB åt systemet och dela resten mellan containrarna, ungefär
  två delar åt grafen och en åt api:t.
- Håll grafcontainern 512 MiB över Memgraphs tak: Memgraph använder en del
  minne som den inte räknar, och containern räknar cachen för dess filer.
- Memgraph höll de uppmätta modellerna på 2 till 12 gånger storleken på deras
  filer, för varje version den behåller.
- Servern använder ungefär 150 MiB i vila och 350 MiB medan den importerar en
  modell med 40 000 entiteter. IFC-arbetsprocessen behöver 10 till 20 gånger
  storleken på en stor fil, så 1024 MiB räcker till IFC-filer upp till ungefär
  40 MB.
- Håll `OPENCDE_IFC_DB_MEMORY_MB` (4096 som standard) minst 512 över
  api-containerns gräns. Den begränsar arbetsprocessens adressrymd, som ligger
  ungefär 0,5 GiB över det minne arbetsprocessen använder, och en arbetsprocess
  som stoppas där kan hänga tills dess tidsgräns har gått ut. Vid containerns
  gräns stoppar kärnan arbetsprocessen, den största processen i containern, och
  importen misslyckas.
- Utelämna `docker/compose.workcopy.yaml`: arbetskopian är en andra Memgraph,
  utan tak.

Vad Memgraph gör vid sitt tak står i
[local.md](local.md#memgraph-i-stället-för-neo4j). Överlagringen är till för
Memgraph: med Neo4j begränsar `NEO4J_HEAP` och `NEO4J_PAGECACHE` databasen
(512m och 256m som standard, ungefär 1 GB för containern), vilket ryms i 4 GB
som det är.
