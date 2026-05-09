# Polder

> Wie regeert Nederland, in YAML, dagelijks bijgewerkt.

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
| ROO | `organisaties.overheid.nl`, `api-organisaties.overheid.nl`, dagelijkse `exportOO.xml` | XML/CSV/REST/SRU | dagelijks | CC0 | alle organisatietypes, bestuurders tot SG/DG/burgemeester/dijkgraaf |
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
# dependencies installeren via uv
uv sync

# ROO-fetcher draaien
uv run polder-fetch-roo

# valideren tegen schemas
uv run polder-validate

# diff tussen _cache/ en data/ produceren
uv run polder-diff

# SQLite + Frictionless data package bouwen
uv run polder-build
```

Of via `make`:

```bash
make sync
make fetch-roo
make validate
make diff
make build
```

## Status

Bootstrap fase. ROO-fetcher is in voorbereiding, andere fetchers staan in de roadmap (zie issues en `polder-plan.md`). Datamodel-schemas en CI-workflow worden parallel opgezet. Dit is geen productie-dataset zolang de versie onder 0.1 staat.

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
