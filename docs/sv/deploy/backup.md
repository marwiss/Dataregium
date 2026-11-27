# Säkerhetskopiering och återställning

`opencde backup KATALOG` skriver en mapp `KATALOG/opencde-backup-<UTC-tid>/` med

| Fil | Innehåll |
|---|---|
| `nodes.jsonl.gz` | varje nod: etiketter och egenskaper, ett JSON-objekt per rad |
| `relationships.jsonl.gz` | varje relation, med start- och slutnod angivna genom sina unika nycklar |
| `store.jsonl.gz` | med PostgreSQL: varje tabellrad med serverns egna data, ett JSON-objekt per rad |
| `storage.tar.gz` | lagringskatalogen: dokument, snapshots, bitmaps, snippets |
| `git/<projektets id>.bundle` | med `OPENCDE_GIT_ENABLED`: varje projekts repo, alla refs |
| `manifest.json` | format, tid, serverversion, dialekt, antal, SHA-256 för varje fil |

Noder identifieras med schemats unika nycklar (`id`, `guid`, `project_id`, …),
aldrig med databasinterna id:n, så en säkerhetskopia tagen från Neo4j kan
återställas i Memgraph och tvärtom (ADR 0006). Databasen fortsätter köra under
säkerhetskopieringen; en kopia tagen medan uppladdningar pågår kan sakna
delarna av de uppladdningarna, vilket klienterna helt enkelt gör om.

Med serverns egna data i PostgreSQL (`OPENCDE_STORE_URL`), som `opencde init`
och Compose-filerna ställer in det, ligger dess rader i `store.jsonl.gz`, varje
rad `{"table": ..., "row": {...}}`, tabell efter tabell i ordningen för deras
främmande nycklar och alla från samma ögonblicksbild av databasen.
`nodes.jsonl.gz` och `relationships.jsonl.gz` håller då bara IFC-DB:s modeller,
och ingenting på en server utan graf (nivå 1 och 2). `manifest.json` anger
`postgres` i `store_dialect`, antalet rader i `rows`, databasens senaste
migrering i `store_migration` och `none` i `dialect` när det inte finns någon
graf. En server som har ett repo per projekt (`OPENCDE_GIT_ENABLED`) skriver
vart och ett som en bundle efter raderna, så att det rymmer varje commit de
nämner, med sin SHA-256 bland de andra och antalet i `repos`; återställningen
prövar var och en och gör projektets repo av den, och vägrar en kopia med repon
om inte `OPENCDE_GIT_ENABLED` är satt.

Med serverns egna data i en egen graf (`OPENCDE_STORE_GRAPH_URI`,
[ifc-db.md](../ifc-db.md)) håller en säkerhetskopia båda graferna i samma
filer, IFC-DB:s noder från IFC-grafen och resten från lagret, och
`manifest.json` anger lagrets dialekt i `store_dialect`. En återställning
lägger varje nod där dess etikett hör hemma, vilka grafer kopian än kom från,
så en kopia av en databas återställs i två och tvärtom; den vägrar om inte
båda är tomma eller `--force` ges, och utelämnar en relation mellan de två
slagen, som bara en databas från före ADR 0027 kan hålla.

## Återställning

```sh
opencde restore KATALOG/opencde-backup-20261105T031700Z           # till en tom databas
opencde restore KATALOG/opencde-backup-20261105T031700Z --force   # ersätt nuvarande innehåll
```

`restore` kontrollerar kontrollsummorna, vägrar en icke-tom databas utan
`--force`, återskapar noder och relationer i omgångar och packar upp filerna i
lagringskatalogen. Sessioner och tokens återställs också; kör
`opencde revoke --all` efteråt om kopian är gammal eller kommer från en annan
server.

Till PostgreSQL går en kopia av PostgreSQL in i de tomma tabellerna i en
databas utan någon migrering efter kopians: `restore` migrerar den upp till
kopians migrering, lägger tillbaka raderna i en transaktion och kör sedan de
senare migreringarna över dem. En grafs kopia återställs också i PostgreSQL:
serverns egna noder och relationer blir rader, och IFC-DB:s noder går till
grafen. Det som saknar plats i tabellerna vägras, allt i ett meddelande, och
ingen rad går in. En kopia som håller IFC-DB:s modeller kräver en server med
graf (nivå 3), och en kopia av PostgreSQL återställs inte i en graf.

## I Docker Compose

`docker/compose.prod.yaml` kör en tjänst `backup` som anropar `opencde backup /backups`
en gång om dagen och behåller de 14 nyaste mapparna i volymen `backups`. Den
väntar på PostgreSQL och säkerhetskopierar den med de lagrade filerna och, i
nivå 3, grafens modeller. Kopiera den volymen någon annanstans (en annan värd,
objektlagring); en säkerhetskopia på samma disk som datat är bara en halv
säkerhetskopia:

```sh
docker run --rm -v dataregium_backups:/backups -v "$PWD":/out alpine \
  tar czf /out/dataregium-backups.tgz -C /backups .
```

En installation som behållit det gamla projektnamnet har volymen som `kontroll_backups`.

För att återställa på en ny värd: starta stacken, kopiera en
säkerhetskopiemapp in i containern `api` (eller montera den) och kör
`docker compose exec api opencde restore /sökväg --force`.

## Från grafen till PostgreSQL

En installation från före PostgreSQL har serverns egna data i grafen. Den
flyttar till PostgreSQL med en säkerhetskopia och en återställning. En
säkerhetskopia som den här utgåvan skriver för med sig alla noder i serverns
egna data: de med en unik nyckel, och team, en versions publicering,
viewpointernas komponenter, provningarna, krocktesterna, sammanslagningarna och
anropsloggens poster efter de id servern gav dem. En säkerhetskopia från en
tidigare utgåva lämnar de senare och säger hur många noder den lämnade (`WARN …
nodes without a key were not exported`). Uppdatera därför först utcheckningen
och hämta den här utgåvans image (`build api` i stället för `pull api` för en
utcheckning som bygger sin egen), och skriv sedan, med den gamla `.env` och den
gamla stackens graf igång, säkerhetskopian till datavolymen, som den nya stacken
behåller, med den här utgåvans `opencde backup` i en engångscontainer. Det
kommandot behöver `OPENCDE_TIER=experimental`, eftersom den här utgåvan har
serverns egna data i en graf bara i nivå 3. Imagens startkommando är `opencde`,
så kommandot ges utan det, och containern körs som användaren 10001, som måste
kunna skriva i mappen (`/data` tillhör den). Ange utgåvan i `OPENCDE_IMAGE_TAG`,
eftersom den gamla `.env` kan ha den gamla:

```sh
OPENCDE_IMAGE_TAG=<release> docker compose pull api
OPENCDE_IMAGE_TAG=<release> docker compose run --rm --no-deps -e OPENCDE_TIER=experimental api backup /data/migration
```

Ta sedan bort `OPENCDE_STORE_GRAPH_URI` ur `.env` om
den är satt, och lägg till `OPENCDE_STORE_PASSWORD` (ett långt slumpat värde)
och `OPENCDE_TIER` i `.env`, och `COMPOSE_PROFILES=graph` för att behålla
IFC-DB:s modeller i nivå 3 (`OPENCDE_TIER=experimental`); eller flytta undan den
gamla `.env`, skriv en ny med `opencde init` (`--tier experimental` för att
behålla IFC-DB:s modeller) och för över hemligheterna, som `OPENCDE_SECRET_KEY`
och `OPENCDE_GRAPH_PASSWORD`. Starta den nya stacken och återställ kopian:

```sh
docker compose up -d
docker compose exec api opencde restore /data/migration/opencde-backup-<UTC-tid>
```

Lägg till `--force` om den säger att databasen inte är tom. En säkerhetskopia av
en graf återställs i PostgreSQL som rader, och en säkerhetskopia av PostgreSQL
återställs inte i en graf.

## Testa din återställning

En säkerhetskopia du aldrig har återställt är en förhoppning, inte en
säkerhetskopia. Integrationstestet `tests/integration/test_backup.py`
återställer en kopia i en tom databas på både Neo4j och Memgraph, och
`tests/integration/test_postgres_cli.py` återställer kopior av PostgreSQL och
av en graf i PostgreSQL; gör samma sak en gång på en testinstans när
produktionen är uppsatt.
