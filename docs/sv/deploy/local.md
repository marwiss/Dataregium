# Lokalt test (Windows, macOS, Linux)

Allt körs i Docker på din dator; ingenting går att nå utifrån.

## Krav

- Docker Desktop (Windows, macOS) eller Docker Engine med compose-tillägget (Linux)
- eller Podman 5 med `podman compose`: samma filer fungerar, se [Podman](#podman) nedan
- [uv](https://docs.astral.sh/uv/) för kommandoradsverktygen

## Steg

```sh
git clone https://github.com/marwiss/Dataregium.git
cd Dataregium
uv run opencde init --dev                                 # skriver .env med nya hemligheter
docker compose up -d --build
docker compose exec api opencde seed
```

`opencde init --dev` skriver nivå 2, BIM och GIS (`OPENCDE_TIER=bim-gis`):
PostgreSQL håller serverns egna data, och ingen graf startar. `--tier experimental`
skriver nivå 3, Experimentell, med IFC-DB påslagen och Neo4j bredvid PostgreSQL
(`COMPOSE_PROFILES=graph`), och `--tier documents` nivå 1, Dokument.

`opencde seed` skapar demonstrationsanvändare (en administratör och en
användare för var och en av rollerna manager, editor, contributor och
viewer), två projekt och en publik OAuth2-klient, och skriver ut lösenorden
en gång.

| Vad | Var |
|---|---|
| API-dokumentation | <http://127.0.0.1:8000/docs> |
| Administration | <http://127.0.0.1:8000/admin> (logga in som administratören) |
| Beredskap | <http://127.0.0.1:8000/readyz> |
| PostgreSQL | `postgresql://opencde@127.0.0.1:5442/opencde`, med `OPENCDE_STORE_PASSWORD` |
| Neo4j Browser, i nivå 3 | <http://127.0.0.1:7484> (Bolt `127.0.0.1:7697`) |
| Serverlogg | `docker compose logs -f api` |

Utan graf, som i nivå 1 och 2, svarar `/readyz`
`{"status":"ready","graph":"none","store":"ok","workcopy":"none","store_dialect":"postgres"}`,
och 503 `{"status":"not_ready","graph":"none","store":"unavailable"}` medan
PostgreSQL inte svarar. I nivå 3 är `"graph"` `"ok"` när grafen också svarar.

Utvecklingsöverlagringen publicerar PostgreSQL på 5442 och, i nivå 3, Neo4j på
7484/7697 i stället för standardportarna 5432 och 7474/7687, som en annan
PostgreSQL eller Neo4j på samma dator ofta upptar.
Ändra portarna med `OPENCDE_DEV_*` i `.env`. Två utcheckningar på samma dator
delar Compose-projektnamnet `dataregium` (`docker/compose.yaml` sätter det); ge den
andra `COMPOSE_PROJECT_NAME=dataregium-2` och andra portar i sin `.env`.

En installation som gjordes när projektet ännu hette Kontroll har sitt data i
volymer som heter `kontroll_…`. Den behåller dem med
`COMPOSE_PROJECT_NAME=kontroll` i `.env`, som går före namnet i `docker/compose.yaml`;
utan det startar nästa `up` ett nytt projekt med tomma volymer.

En installation från förut, med serverns egna data i grafen, flyttar till
PostgreSQL med en säkerhetskopia och en återställning, som
[Från grafen till PostgreSQL](backup.md#från-grafen-till-postgresql) beskriver.

## Utan Docker för servern

Kör databaserna i Docker (eller grafen i Neo4j Desktop / Memgraph Lab) och
servern från utcheckningen:

```sh
uv sync
uv run opencde doctor
uv run opencde serve --reload
```

Servern behöver PostgreSQL, och i nivå 3 grafen också; en server i nivå 3 utan
`OPENCDE_STORE_URL` håller sina egna data i grafen, som före PostgreSQL, och
behöver bara grafen. `.env` som `opencde init --dev` skriver pekar på
`postgresql://opencde@127.0.0.1:5442/opencde` och, i nivå 3, på
`bolt://127.0.0.1:7697`: utvecklingsöverlagringens portar.

## Memgraph i stället för Neo4j

```sh
# .env: COMPOSE_FILE=docker/compose.yaml:docker/compose.memgraph.yaml:docker/compose.dev.yaml
docker compose up -d --build
```

Memgraph hör som Neo4j till nivå 3: den startar med `COMPOSE_PROFILES=graph` i
`.env`, som `opencde init --dev --tier experimental` skriver.
Sätt `OPENCDE_GRAPH_DIALECT=memgraph` och `OPENCDE_GRAPH_USER=memgraph` i
`.env` när servern körs utanför Docker. Memgraph Lab (`memgraph/lab`) kan
pekas mot `127.0.0.1:7697`.

Memgraph håller grafen i minnet. `OPENCDE_MEMGRAPH_MEMORY_MIB` i `.env` sätter
ett tak (Memgraphs `--memory-limit`; `0`, standardvärdet, lämnar det åt
Memgraph: 90 % av maskinens minne, eller allt med swap). Vid taket nekar
Memgraph det som behöver mer: servern svarar 503 `unavailable`, en import som
inte får plats misslyckas utan att lämna en del av modellen kvar, och från 95 %
av taket svarar `/readyz` 503 med `"graph": "full"`.

## Test med Dataspatium

[Dataspatium](https://github.com/marwiss/Dataspatium) är webbläsarklienten
som utvecklas tillsammans med den här servern. Servera en utcheckning av den
över http (`python -m http.server 8080` i dess mapp), öppna
`http://127.0.0.1:8080/`, registrera appen som en publik klient och lägg till
servern som OpenCDE-källa i Dataspatium (*Inhämta*) med
`http://127.0.0.1:8000` och det klient-id:t:

```sh
uv run opencde client create "Dataspatium" --public --redirect-uri http://127.0.0.1:8080/oauth-callback.html
```

Med `OPENCDE_DYNAMIC_CLIENT_REGISTRATION=true` i `.env` registrerar appen sig
själv i stället. CORS tillåter alla ursprung som standard, så ingen mer
inställning behövs; `OPENCDE_REQUEST_LOG_ENABLED=true` låter `/admin/requests`
visa serversidan av appens anrop.
[Snabbstarten](../quickstart.md) har alla steg, i nivå 3, och en
felsökningstabell.

## Den lokala motorn

Dataregium kan köras på den egna datorn som motorn bakom Dataspatium för
modeller större än vad en webbläsarflik rymmer (ADR 0023). `opencde init --local`
skriver en `.env` för det, alltid i nivå 3: PostgreSQL för serverns egna data,
grafen bredvid för modellerna, och IFC-DB och MCP-ändpunkten påslagna.
`OPENCDE_ENVIRONMENT=local` får servern att hålla sig till det: den publika
adressen måste vara loopback, databasernas lösenord och hemlig nyckel krävs,
CORS-ursprungen måste namnges (`*` avvisas), ingen
databasadress får publiceras, och `opencde serve` vägrar andra gränssnitt än
loopback. En begäran vars `Host` inte är `127.0.0.1`, `localhost` eller `::1`
besvaras med `421` före allt annat, så att en sida på internet inte når motorn
genom ett eget namn som pekar på loopback. En publik sida som frågar i sin
preflight (`Access-Control-Request-Private-Network: true`) tillåts när dess
ursprung är namngivet. OAuth2 med PKCE krävs lokalt som överallt. Ursprungen
filen skriver är `null` (Dataspatium öppnad från en fil), `http://127.0.0.1:8080`
(en lokal http-server) och `https://marwiss.github.io`; ta bort `null` när
appen körs från ett http-ursprung.

```sh
uv run opencde init --local   # .env: OPENCDE_ENVIRONMENT=local, random passwords, the origins Dataspatium comes from
docker compose up -d          # the API on http://127.0.0.1:8000 only; the databases are never published
```

## Podman

Compose-filerna fungerar med Podman lika väl som med Docker: imagerna har
fullständiga namn, datat ligger i namngivna volymer, bindmonteringarna bär etiketten
`z` för SELinux, och utvecklingsöverlagringen binder portar över 1024 på 127.0.0.1.
Kör dem med `podman compose` (Podman 5) eller med kommandot `docker compose` mot
Podmans socket (`DOCKER_HOST=unix:///run/user/1000/podman/podman.sock`). Rotlös
Podman kan inte binda portarna 80 och 443 åt produktionsöverlagringen: sätt
`sysctl net.ipv4.ip_unprivileged_port_start=80` eller kör den stacken som root.
Podman är kostnadsfritt (Apache-2.0); testerna körs med Docker, och Podman ingår
inte i CI.

## Börja om från början

```sh
docker compose down -v   # tar bort volymerna: databaser och filer
```
