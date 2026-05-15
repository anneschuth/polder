# Polder

> **P**ublieke **O**verheid: **L**eden, **D**ienstverbanden, **E**enheden, **R**ollen. Wie regeert Nederland, in YAML, dagelijks bijgewerkt.

Polder is een git-versioned, CC0-gelicenseerde dataset van Nederlandse overheidsorganisaties, posten, personen en mandaten. De source-of-truth is plain YAML, gevalideerd met JSON Schema, en gepubliceerd als Datasette plus Frictionless Data Package. Een GitHub Actions workflow draait dagelijks fetchers, opent een PR en logt elke wijziging als git commit. History is permanent.

## Waarom

Nederland heeft geen geconsolideerd, machine-leesbaar register van wie waar zit in de overheid mét termijn-historie. ROO (KOOP) dekt organisaties tot directieniveau en bestuurders tot SG/DG, maar geen historie en geen ABD-managers. Tweede Kamer OData is uitstekend; Eerste Kamer publiceert niets in een bruikbaar formaat. ABD-management onder de TMG zit alleen in PDF-jaarverslagen. Staatscourant publiceert benoemings-KB's als vrije tekst zonder gestructureerde feed. Open State Foundation's Allmanak fuseert al veel, maar is een hosted website-met-database, geen git-versioned dataset met PR-flow. De Sdu-Staatsalmanak kost €419 per editie en is niet open.

Polder vult vier specifieke gaten:

1. Een Popolo-achtig persoon-post-organisatie graafmodel met expliciete `start_date` en `end_date` per mandaat.
2. Eerste Kamer en ABD-management onder TMG.
3. Een NLP-pipeline op de Staatscourant-feed die KB's parst naar gestructureerde Membership-proposals.
4. Een persistente identifier-crosswalk tussen OIN, KvK, RSIN, Wikidata-Q, TK-persoonId, ROO-id, TOOI-URI en eigen stable slugs.

## Datamodel

Conceptueel volgt Polder de [Popolo](http://www.popoloproject.com/) spec: Person, Organization, Post, Membership, Area, Event als classes. Veldnamen volgen Popolo waar redelijk. Voorbeeld van een persoon-record:

```yaml
id: person:jansen-jp-1965
identifiers:
  wikidata: Q12345678
  tk_persoon_id: null
  abd_id: null
name:
  full: Jan Pieter Jansen
  family: Jansen
  given: Jan Pieter
  initials: J.P.
  honorifics_pre: [dr., ir.]
birth: { year: 1965 }
gender: m
mandaten:
  - id: 01HXY9ABCDEFGHJKMNPQRSTVWX
    organization_id: org:min-bzk
    post_id: post:sg-min-bzk
    role: Secretaris-Generaal
    start_date: 2022-09-01
    end_date: null
    appointment:
      decision: KB 2022-08-15
      staatscourant_url: https://zoek.officielebekendmakingen.nl/stcrt-2022-...
    sources:
      - { id: staatscourant, url: ..., retrieved: 2022-08-20 }
      - { id: roo, url: https://organisaties.overheid.nl/9632/, retrieved: 2026-05-08 }
```

Volledige schema-definities staan in `schemas/`. Validatie-regels in `src/polder/validate.py`.

## Bronnen

Primaire feeds, deterministisch opgehaald (geen LLM):

| Bron | Endpoint | Formaat | Update | Licentie | Dekking |
|---|---|---|---|---|---|
| ROO | `organisaties.overheid.nl`, `api-organisaties.overheid.nl`, dagelijkse `exportOO.xml` | XML/CSV/REST/SRU | dagelijks | CC0 | alle organisatietypes, bestuurders tot SG/DG/burgemeester/dijkgraaf. Polder is een **strict superset**: élk leaf-veld uit ROO komt in YAML terecht (verifieerbaar via `polder roo-roundtrip`). Zie [docs/roo_field_map.md](docs/roo_field_map.md). |
| TOOI | `standaarden.overheid.nl/tooi`, `identifier.overheid.nl/tooi/id/` | SKOS/RDF | gestaag | CC0 | URI-stelsel voor alle organisatietypes |
| TK OData | `gegevensmagazijn.tweedekamer.nl/OData/v4/2.0/` | OData v4 + Atom SyncFeed | near-realtime | open | TK-personen, fracties, commissies, vanaf 2008-09-01 |
| Logius COR | `oinregister.logius.nl`, `portaal.digikoppeling.nl/registers/corApi/` | REST | gestaag | open | OIN per organisatie |
| KOOP SRU | `repository.overheid.nl/sru` | SRU/XML | live | open | Staatscourant, KB's, sinds 2009 |
| Wikidata | `query.wikidata.org/sparql` | SPARQL | live | CC0 | Q-id crosswalks |
| Allmanak (OSF) | `rest-api.allmanak.nl/v0/` | PostgREST | gestaag | open | secundaire bron |
| Open Raadsinformatie | `api.openraadsinformatie.nl/v1/elastic/` | Elastic + Popolo ODS | gestaag | open | 265+ gemeenten, raadsleden |
| Kiesraad | `data.overheid.nl` | CSV/XML | per verkiezing | open | uitslagen, kandidaatlijsten |
| Algemene Rekenkamer RWT | `www.rekenkamer.nl/onderwerpen/rwt-register` | HTML | jaarlijks | gebruik | RWT-lijst |
| Rijksfinanciën ZBO/Agentschap | jaarlijkse Excel/CSV via Min FIN | spreadsheet | jaarlijks | open | overzicht ZBO/agentschap |

LLM-bronnen (parsing nodig, met guardrails):

- Staatscourant via KOOP SRU: benoemings-KB's parsen naar Membership-proposals.
- Eerste Kamer pagina's: HTML scrape voor leden en commissies.
- Rijksoverheid.nl organogrammen: HTML plus PDF/PNG voor ABD-management onder TMG.
- ABD-jaarverslagen: PDF voor populatie-aggregaten.

## AVG en juridische cut-off

Uitgangspunt is de ABRvS-doctrine sinds 31 januari 2018 (ECLI:NL:RVS:2018:314): namen mogen openbaar als de medewerker uit hoofde van functie in de openbaarheid treedt. Polder hanteert drie zones:

🟢 **Groen, publiceren standaard.** Bewindspersonen, Kamerleden TK+EK, CdK, gedeputeerden, statenleden, burgemeesters, wethouders, raadsleden, dijkgraven, DB-leden waterschap, AB-leden waterschap, voorzitters Hoge Colleges van Staat, RvB-leden ZBO's, ABD-Topmanagementgroep, rechters en raadsheren, gezaghebbers Caribisch Nederland, griffiers.

🟡 **Geel, alleen functionele gegevens en alleen reeds publiek door organisatie zelf.** ABD-managers schaal 15 en 16 op directieniveau, inspecteurs-generaal, secretarissen ZBO's en agentschappen, bestuurders en secretarissen GR's. Geen privé-contactgegevens.

🔴 **Rood, niet doen.** Beleidsmedewerkers, juristen, communicatie, handhavers, dossierbehandelaars. Altijd: privé-contactgegevens, geboortedata (alleen jaar voor disambiguatie), BSN, WNT-salarisdata buiten publicatieplicht, foto's, bijzondere persoonsgegevens.

Takedown-flow loopt via een GitHub-issue-template met een SLA van 14 dagen. Bij bedreigde ambtsdragers (Veilig Bestuur-meldingen) is opt-out op verzoek mogelijk.

## Installatie

```bash
uv sync
```

## CLI: `polder`

Alle polder-functionaliteit zit onder één entrypoint: `polder`. De volledige
referentie staat in [docs/cli.md](docs/cli.md); hier de korte versie.

```bash
uv run polder --help                # overzicht
uv run polder fetch --help          # 12 fetch-subcommands
uv run polder skill --help          # Claude Code skills

# data ophalen
uv run polder fetch roo             # ROO exportOO.xml
uv run polder fetch tk              # Tweede Kamer OData
uv run polder fetch all             # alle deterministische fetchers

# valideren, diffen, bouwen
uv run polder validate
uv run polder diff
uv run polder build all             # SQLite + CSV + datapackage
uv run polder serve                 # datasette op dist/polder.db

# Claude Code skills
uv run polder skill review-diff diff.json
uv run polder skill parse-staatscourant kb.xml
uv run polder skill parse-organogram organogram.pdf min-bzk

# resolver-output uit data/_staging/ automatisch toepassen op data/
uv run polder apply-staging data/_staging/                 # dry-run
uv run polder apply-staging data/_staging/ --apply         # echt toepassen

# pipeline
uv run polder daily-update          # fetchers + validate + diff + review
uv run polder ingest --commit --push  # vol-automatische staging-pipeline
```

De oude losse scripts (`polder-fetch-roo`, `polder-validate`, `polder-build`,
...) blijven werken voor backwards-compatibility, maar nieuwe code gebruikt
`polder <subcommand>`.

## Ingest: dagelijkse staging-pipeline

`polder ingest` draait per bron parse, resolve, apply, validate, build, commit
en push in één keer. Drempel staat op 0.85; records eronder komen op de
skip-stack en blijven in `data/_staging/` voor handmatige review.

```bash
uv run polder ingest --dry-run                        # plan tonen
uv run polder ingest --source abd-nieuws --limit 50   # 50 nieuwe nieuwsberichten
uv run polder ingest --commit --push                  # vol-automatisch
```

Idempotent: een tweede run zonder nieuwe input doet niets. Bij validate-error
stopt de pipeline en wordt er niet gecommit. Volledige uitleg in
[docs/ingest.md](docs/ingest.md).

## Lokaal draaien

Alles wat polder via GitHub Actions doet, kan ook lokaal via `polder
daily-update` of de losse `polder skill ...` commando's. Geen
`ANTHROPIC_API_KEY`-secret nodig: de skills gebruiken je lokale Claude Code
subscription. Zie [docs/lokaal-draaien.md](docs/lokaal-draaien.md) voor de
volledige uitleg en voorbeelden per skill.

## Reproduceerbaarheid

Alles draait via `uv run polder ...`. Geen losse shell-scripts, geen
ad-hoc loops, geen UI-clicks.

| Operatie | Commando |
|---|---|
| Volledige dagelijkse update | `polder daily-update` |
| Alle fetchers achter elkaar | `polder fetch all` |
| Eén fetcher | `polder fetch <bron>` |
| Skills lokaal | `polder skill <skill-naam> <input>` |
| Apply-staging dry-run | `polder apply-staging data/_staging/` |
| Apply-staging echt | `polder apply-staging data/_staging/ --apply` |
| ABD-nieuws-backfill | `polder backfill abd-nieuws --since 2024-01-01` |
| Staatscourant-backfill | `polder backfill staatscourant --since 2024-01-01` |
| Validatie | `polder validate` |
| Datasette lokaal | `polder serve` |

Alle paden in commando's zijn relatief aan de repo-root. Caches in `_cache/`,
build-output in `dist/`, beide gitignored. Records in `data/` zijn de
source-of-truth en gaan via PR's. LLM-proposals landen eerst in
`data/_staging/`, nooit direct in `data/`.

## Status

Pre-alpha. Live fetchers: ROO (~4500 organisaties inclusief organisatieonderdelen), TK OData (~800 Kamerleden vanaf 2008), EK (73 senatoren), Logius COR (OIN-crosswalk), Wikidata (Q-id-crosswalk voor ministeries, provincies, gemeenten, waterschappen), AR RWT (RWT-status), ORI (gemeentebestuurders Utrecht/Rotterdam/Nijmegen), ABD organogrammen (manifest + PDFs voor 15 ministeries). Skills v0.2.0: review-pr-diff, parse-staatscourant, parse-organogram, entity-resolution. Library + CLI klaar. Validate-CI groen op main, lokaal idem (geen secrets nodig). Dit is geen productie-dataset zolang de versie onder 0.1 staat.

## Deep-links naar externe systemen

Voor elk record met een externe identifier kun je naar de bron-systemen springen:

| Identifier | Template |
|---|---|
| `tk_persoon_id` | `https://berthub.eu/tkconv/persoon.html?nummer={tk_persoon_id}` |
| `wikidata` | `https://www.wikidata.org/wiki/{wikidata}` |
| `tooi` | resolveert direct (`https://identifier.overheid.nl/tooi/id/...`) |
| `roo_id` | `https://organisaties.overheid.nl/{roo_id}/` |
| `allmanak_id` | `https://www.allmanak.nl/cat/{allmanak_id}/` (waar mappable) |

Gebruik `polder show <id> --links` om de aanwezige identifiers met klikbare URL's getoond te krijgen.

## Verwante producten

- [Sdu-Staatsalmanak](https://www.sdu.nl/sdu-staatsalmanak): commerciële editie, gedrukt en online, €419 per jaar. Andere positionering, andere doelgroep.
- [Allmanak](https://www.allmanak.nl/) (Open State Foundation): gratis website met fusing van vergelijkbare bronnen. Polder hergebruikt Allmanak waar zinvol als secundaire bron en publiceert in een complementair formaat (git-versioned dataset versus hosted website).
- [berthub.eu/tkconv](https://berthub.eu/tkconv): TK-data en KB's vanuit een persoonlijke conversie-pipeline. Polder linkt deep naar `tkconv` voor elk TK-lid.
- [Wikidata](https://www.wikidata.org): Q-items voor organisaties en personen. Crosswalk in elk record.

## Licenties

Code staat onder de [EUPL-1.2](LICENSE) (Nederlandse versie). Data staat onder [CC0 1.0 Universal](LICENSE-DATA), publiek domein.

## Bijdragen

Pull requests welkom. Iedere wijziging in `data/` valideert tegen de schemas in CI. Voor inhoudelijke wijzigingen: open eerst een issue zodat duidelijk is welk veld uit welke bron komt. Twee-bron-regel voor nieuwe benoemingen geldt zonder uitzondering.

Verwijderen van records gebeurt nooit. Bij opheffing of einde mandaat wordt `valid_until` gezet. Historie blijft.
