# Rekstur á eigin þjóni (Docker Compose + Caddy)

Einn Linux-hýsill (2 vCPU og 4 GB vinnsluminni er ríflegt til að byrja með),
Docker með compose-viðbótinni, DNS-heiti sem vísar á hann, gáttir 80 og 443
opnar. Caddy sækir og endurnýjar TLS-skírteinið; API-ið, gagnagrunnurinn og
öryggisafritunarverkið keyra á lokuðu neti.

## 1. Undirbúa

```sh
git clone https://github.com/marwiss/Dataregium.git /opt/dataregium
cd /opt/dataregium
uv run opencde init --public-url https://cde.example.org
```

Án `--public-url` spyr `opencde init` um gildin og prentar síðan skipanirnar sem koma næst, einnig fyrir stjórnandann og fyrsta biðlarann.

`init` skrifar `.env` með `OPENCDE_ENVIRONMENT=production`, leynilykli,
lykilorði gagnagrunnsins og `OPENCDE_DOMAIN`. Bættu við
`OPENCDE_ACME_EMAIL=you@example.org` svo að Let's Encrypt geti náð í þig vegna
skírteina. Haltu `.env` leyndri (git hunsar hana); hún er eina skráin með
leyndarmálum.

Án uv á þjóninum: afritaðu `.env.example` í `.env` og fylltu gildin inn
handvirkt (`python3 -c "import secrets; print(secrets.token_urlsafe(48))"`
fyrir leynilykilinn).

## 2. Ræsa

```sh
docker compose pull
docker compose up -d
docker compose exec api opencde doctor
docker compose exec api opencde user create admin --admin
```

Síðasta skipunin prentar lykilorð stjórnandans einu sinni. Skráðu þig inn á
`https://cde.example.org/signin`, opnaðu `/admin` og búðu til þau verkefni,
notendur og OAuth2-biðlara sem forritin þín þurfa.

## 3. Rekstur

| Verk | Skipun |
|---|---|
| Uppfæra í nýjasta image | `docker compose pull && docker compose up -d` |
| Festa útgáfu | `OPENCDE_IMAGE_TAG=3.0.0` í `.env` (merkin fylgja GitHub-útgáfunum) |
| Bjóða aðeins sum forritaskil | `OPENCDE_APIS=["bcf-2.1"]` í `.env` fyrir þjón sem býður aðeins BCF API 2.1, `["documents"]` fyrir skjalaþjón; öll þrjú sjálfgefið |
| Atvikaskrár | `docker compose logs -f api` (JSON-línur) |
| Heilsa | `https://cde.example.org/readyz`; síðan Stillingar í stjórnuninni |
| Öryggisafrit | á hverri nóttu í geymsluna `backups` af þjónustunni `backup`; sjá [backup.md](backup.md) |
| Afturkalla aðgang í öryggisatviki | `docker compose ... exec api opencde revoke --all` (eða `--user`, `--client`) |

## 4. Gátlisti fyrir herðingu

- Gagnagrunnurinn er ekki birtur; aðeins Caddy hlustar á hýslinum. Að birta
  Bolt-gáttina fyrir grafbiðlara er meðvitað skref með eigin yfirlagi og TLS,
  sjá [bolt.md](bolt.md); opin gátt gefur þeim sem hefur lykilorðið fullan
  aðgang að gagnagrunninum.
- API-gámurinn er skrifvarinn, keyrir ekki sem root og án capabilities;
  geymslan er nefnd gagnageymsla.
- `OPENCDE_TRUSTED_PROXIES` nær yfir compose-netið svo að vistfang biðlarans í
  endurskoðunarskránni sé hið rétta; láttu það vera nema þú breytir netinu.
- Stjórnunina má takmarka við innri vistföng. Bættu þessu við vefsvæðisblokkina í `Caddyfile` og aðlagaðu vistföngin; `/admin/api` fylgir með, svo að stjórnun Dataspatium virkar aðeins frá þeim vistföngum:

  ```caddyfile
  @admin_outside {
  	path /admin /admin/*
  	not remote_ip 10.0.0.0/8 192.168.0.0/16
  }
  respond @admin_outside 404
  ```

- API-ið leyfir 600 beiðnir á mínútu á hvert vistfang. Allir á bak við sama vistfang deila þeim, svo að hækkaðu mörkin fyrir stóra skrifstofu, til dæmis `OPENCDE_RATE_LIMIT_API=3000/minute`. Hið sama gildir um 10 innskráningartilraunir á mínútu, `OPENCDE_RATE_LIMIT_SIGN_IN=60/minute`; biðin á hvert notandanafn stöðvar ágiskanir hvort sem er.
- Haltu `OPENCDE_DYNAMIC_CLIENT_REGISTRATION=false` nema þú þurfir
  sjálfsafgreidda biðlaraskráningu (Foundation API 2.2.3); skráðu biðlara í
  `/admin`.
- Skiptu um leynilykilinn og lykilorð gagnagrunnsins samkvæmt þinni stefnu, endurræstu og skráðu þig inn aftur.
- Keyrðu `testssl.sh https://cde.example.org` og athugaðu
  <https://observatory.mozilla.org> eftir fyrstu ræsingu.
- Eldveggur: leyfðu aðeins 22 (frá þínum vistföngum), 80 og 443.

## Lítill þjónn

Með Memgraph á vél með 4 GB minni bætirðu `docker/compose.small.yaml` við á
eftir `docker/compose.memgraph.yaml`. Yfirlagið setur Memgraph þak og graf- og
api-gámunum mörk, svo að líkan sem er of stórt fyrir vélina mistakist eitt og
sér í stað þess að taka minni vélarinnar. Veldu Memgraph þegar þú setur
þjóninn upp: Memgraph les ekki gögn Neo4j.

```sh
# .env: COMPOSE_FILE=docker/compose.yaml:docker/compose.prod.yaml:docker/compose.memgraph.yaml:docker/compose.small.yaml
docker compose up -d
```

| Mörk | Fyrir 4 GB | Í `.env` |
|---|---|---|
| Þak Memgraph: grafinn og fyrirspurnir hans | 1536 MiB | `OPENCDE_MEMGRAPH_MEMORY_MIB` |
| Grafgámurinn | 2048 MiB | `OPENCDE_GRAPH_CONTAINER_MIB` |
| Api-gámurinn: þjónninn og IFC-vinnuferli hans | 1024 MiB | `OPENCDE_API_CONTAINER_MIB` |
| Eftir handa kerfinu, Caddy og öryggisafrituninni | um 1 GB | |

Svona velurðu þau fyrir aðra vél:

- Skildu um 1 GB eftir handa kerfinu og gefðu gámunum afganginn, um tvo hluta
  grafgámnum og einn api-gámnum.
- Hafðu grafgáminn 512 MiB yfir þaki Memgraph: Memgraph notar dálítið minni
  sem það telur ekki, og gámurinn telur með skyndiminni skránna.
- Mældu líkönin tóku 2 til 12 sinnum stærð skrárinnar sinnar í Memgraph, hver
  útgáfa sem er geymd.
- Þjónninn notar um 150 MiB í hvíld og 350 MiB meðan hann flytur inn líkan með
  40.000 einingum. IFC-vinnuferlið þarf 10 til 20 sinnum stærð stórrar skrár,
  svo að 1024 MiB duga fyrir IFC-skrár upp að um 40 MB.
- Hafðu `OPENCDE_IFC_DB_MEMORY_MB` (4096 sjálfgefið) að minnsta kosti 512 yfir
  mörkum api-gámsins. Stillingin takmarkar vistfangarými vinnuferlisins, sem er
  um 0,5 GiB yfir minninu sem vinnuferlið notar, og vinnuferli sem stöðvast þar
  getur frosið þar til tímamörk þess renna út. Við mörk gámsins stöðvar
  kjarninn vinnuferlið, stærsta ferlið í gámnum, og innflutningurinn mistekst.
- Slepptu `docker/compose.workcopy.yaml`: vinnueintakið er annað Memgraph, án
  þaks.

Hvað Memgraph gerir við þakið stendur í
[local.md](local.md#memgraph-í-stað-neo4j). Yfirlagið er fyrir Memgraph: með
Neo4j takmarka `NEO4J_HEAP` og `NEO4J_PAGECACHE` gagnagrunninn (512m og 256m
sjálfgefið, um 1 GB fyrir gáminn), sem kemst fyrir í 4 GB eins og er.
