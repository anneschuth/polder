# Datasette deployen

Polder publiceert de SQLite-build als een publieke Datasette-instantie zodat gebruikers direct kunnen zoeken, filteren en facetten over organisaties, personen, posten en mandaten zonder de hele dataset te clonen.

## Wat is Datasette

[Datasette](https://datasette.io/) is een open-source tool van Simon Willison die SQLite-databases publiceert als een doorzoekbare website plus JSON-API. Elke tabel krijgt automatisch facetten, full-text search, een SQL-console en grafiekjes via plugins. Polder gebruikt Datasette als read-only frontend op `dist/polder.db`.

## Lokaal draaien

Bouw de database en serveer met de metadata uit de root:

```bash
uv run polder-build sqlite
uv run datasette dist/polder.db -m metadata.json --port 8001
```

Voor embed in een externe pagina is CORS nodig:

```bash
uv run datasette dist/polder.db -m metadata.json --port 8001 --cors
```

De UI staat dan op `http://localhost:8001`. De JSON-API is automatisch beschikbaar op `/polder/<tabel>.json`.

## Datasette Cloud

[Datasette Cloud](https://www.datasette.cloud/) is de managed-hosting van Simon Willison. Free tier is beperkt tot een private space; Polder draait op de Starter ($9 per maand) zodra de dataset publiek live gaat.

Stappen:

1. Account aanmaken op `datasette.cloud`.
2. Nieuwe space `polder` aanmaken.
3. API-token genereren onder Settings, opslaan als GitHub secret `DATASETTE_CLOUD_TOKEN`.
4. In `publish.yml` de `simonw/datasette-cloud-push-action` uncommenten en `dist/polder.db` plus `metadata.json` pushen na elke main-merge.

Cold-start latency is een paar honderd ms, ruim voldoende voor de Polder-omvang (enkele MB).

## Self-host op Hetzner of Fly.io

Goedkoper alternatief voor controle over uptime en custom domain. Voorbeeld `docker-compose.yml`:

```yaml
services:
  datasette:
    image: datasetteproject/datasette:0.65
    ports:
      - "8001:8001"
    volumes:
      - ./dist:/data:ro
      - ./metadata.json:/metadata.json:ro
    command: >
      datasette /data/polder.db
      -m /metadata.json
      --host 0.0.0.0 --port 8001
      --cors
      --setting sql_time_limit_ms 5000
      --setting max_returned_rows 5000
    restart: unless-stopped
```

Op een Hetzner CX11 (€4 per maand) past dit ruim. Zet er een Caddy of Nginx voor met Let's Encrypt voor `https://datasette.polder.dev/`.

## Read-only versus editable

Polder publiceert read-only. De source-of-truth blijft YAML in `data/`, gebouwd via `polder-build`. Datasette draait zonder write-extensies; geen `datasette-write` plugin, geen API-endpoints met PATCH. Wijzigingen lopen via git en PR.

## Schema-evolutie

Bij elke merge naar `main` met wijzigingen in `data/**` triggert `.github/workflows/publish.yml` een nieuwe build van `dist/polder.db` en `dist/datapackage.json`. De artifacts worden geupload als release-asset (versietag is de datum) en optioneel naar Datasette Cloud gepusht. Schema-wijzigingen in `src/polder/build/to_sqlite.py` vereisen handmatig een release-tag bumpen.

Schema-versies blijven backward-compatible in de minor versies onder 1.0; tabel-renames verschijnen als major-bump met deprecation-window van twee weken.

## Custom UI via plugins

Datasette heeft een plugin-ecosysteem. Voor Polder zijn deze relevant:

- [`datasette-cluster-map`](https://github.com/simonw/datasette-cluster-map) voor kaartweergave van gemeente- en provincie-coordinaten zodra die in `organisaties.contact` zitten.
- [`datasette-search-all`](https://github.com/simonw/datasette-search-all) voor zoeken over alle tabellen tegelijk, handig bij naam-lookups die in `personen` of `posten` kunnen zitten.
- [`datasette-vega`](https://github.com/simonw/datasette-vega) voor grafieken op query-resultaten (mandaten per jaar, organisaties per type).
- [`datasette-graphql`](https://github.com/simonw/datasette-graphql) voor een GraphQL-endpoint naast REST en JSON, voor consumers die liever zo queryen.
- [`datasette-render-markdown`](https://github.com/simonw/datasette-render-markdown) voor het renderen van markdown-velden in `description`-achtige kolommen.

Plugins activeer je door ze in `pyproject.toml` of in de Docker-image bij te installeren en in `metadata.json` te configureren onder `plugins:`.
