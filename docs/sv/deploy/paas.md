# Självhostade plattformar (Coolify, Dokploy, Portainer)

De här plattformarna läser en compose-fil, ger den en domän med automatisk TLS
och erbjuder ett webbgränssnitt för loggar, omstarter och uppdateringar. De är
ett bekvämt sätt att köra servern när du redan använder en av dem.

## Compose-fil

Använd `docker/compose.yaml` tillsammans med `docker/compose.prod.yaml` **utan tjänsten
`proxy`**: plattformens egen proxy (Traefik i Coolify och Dokploy) terminerar
TLS. Enklast är en liten överlagringsfil bredvid dem:

```yaml
# docker/compose.paas.yaml
services:
  proxy:
    profiles: ["disabled"]
  api:
    labels:
      # Dokploy / Coolify läser dessa för att dirigera domänen till containern.
      traefik.enable: "true"
      traefik.http.services.opencde.loadbalancer.server.port: "8000"
```

och driftsätt `docker/compose.yaml docker/compose.prod.yaml docker/compose.paas.yaml` som en stack.

## Miljö

Sätt variablerna från `.env.example` i plattformens miljöredigerare. Minimum
för produktion i nivå 2, den nivå som `opencde init` skriver som standard:

```
OPENCDE_ENVIRONMENT=production
OPENCDE_PUBLIC_URL=https://cde.example.org
OPENCDE_TIER=bim-gis
OPENCDE_SECRET_KEY=<48 slumpmässiga tecken>
OPENCDE_STORE_PASSWORD=<24 slumpmässiga tecken>
OPENCDE_TRUSTED_PROXIES=["172.16.0.0/12", "10.0.0.0/8", "192.168.0.0/16"]
```

Serverns egna data finns i PostgreSQL, stackens tjänst `store`: Compose ger
API:t dess adress i `OPENCDE_STORE_URL`, och dess lösenord är
`OPENCDE_STORE_PASSWORD`. `OPENCDE_STACK_STORE_URL` anger en annan PostgreSQL,
som plattformens egen, utan lösenordet. Imagen behöver extrat `postgres`, som
`docker/Dockerfile` installerar som standard
(`OPENCDE_EXTRAS="--extra postgres --extra ifc-db"`); en image utan
IfcOpenShell byggs med `--build-arg OPENCDE_EXTRAS="--extra postgres"`.
`OPENCDE_GRAPH_PASSWORD` behövs bara med en graf: i nivå 3, där
`COMPOSE_PROFILES=graph` startar Neo4j för IFC-DB:s modeller.

`OPENCDE_DOMAIN` behövs bara av Caddy och kan utelämnas.

## Att tänka på

- Håll plattformen själv uppdaterad: Coolify publicerade till exempel
  rättningar för allvarliga sårbarheter i januari 2026. Plattformens
  administrationsgränssnitt ska inte gå att nå från det öppna internet.
- PostgreSQL:s volym och lagringsvolymen är datat, och i nivå 3 även grafens
  volym; ta med dem i plattformens säkerhetskopiering, och behåll även volymen
  för den nattliga tjänsten `backup`.
- Första administratören: öppna en konsol på tjänsten `api` och kör
  `opencde user create admin --admin`.
