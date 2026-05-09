# Polder

> Wie regeert Nederland, in YAML, dagelijks bijgewerkt.

Een git-versioned, CC0-gelicenseerde dataset van alle Nederlandse overheidsorganisaties, posten, personen en mandaten. Source-of-truth in YAML, gevalideerd met JSON Schema, gepubliceerd als Datasette plus Frictionless Data Package. Onderhouden door deterministische fetchers waar mogelijk, en Claude Code skills waar LLM-werk onontkoombaar is (Staatscourant-NLP, organogram-OCR, entity resolution).

## Context voor de AI-agent

### Waarom dit project

Nederland heeft geen geconsolideerd, machine-leesbaar register van wie waar zit in de overheid met termijn-historie. ROO (KOOP) dekt organisaties tot directieniveau en bestuurders tot SG/DG, maar geen termijn-historie en geen ABD-managers. Tweede Kamer OData is uitstekend, Eerste Kamer heeft niets. ABD-management onder de TMG heeft alleen jaarverslagen in PDF. Staatscourant publiceert benoemingen-KB's als vrije tekst zonder gestructureerde feed. Open State Foundation's Allmanak fuseert al veel, maar is een hosted website-met-database, niet een git-versioned dataset met full history en PR-flow. De Sdu-Staatsalmanak kost €419 per editie en is niet open.

Polder vult vier specifieke gaten:

1. Een Popolo-achtig persoon-post-organisatie graafmodel met expliciete `start_date` en `end_date` per mandaat.
2. Eerste Kamer en ABD-management onder TMG.
3. Een NLP-pipeline op de Staatscourant-feed die KB's parst naar gestructureerde Membership-proposals.
4. Een persistente identifier-crosswalk tussen OIN, KvK, RSIN, Wikidata-Q, TK-persoonId, ROO-id, TOOI-URI en eigen stable slugs.

### Filosofie

- **Git-as-database**. Alle data is YAML in `data/`, gecommit per dag. Volledige history via git blame. Bijdragers reviewen via PR's en GitHub-blames niveau.
- **YAML, geen JSON-LD**. Plain YAML als source-of-truth. JSON Schema 2020-12 voor validatie. Optionele afgeleide JSON-LD-export voor wie het wil.
- **Popolo conceptueel**. Person, Organization, Post, Membership, Area, Event als classes. Veldnamen volgen de Popolo-spec waar redelijk.
- **Deterministisch waar mogelijk, LLM waar nodig**. Fetchers zijn gewoon Python. LLM-werk gebeurt via Claude Code skills met harde guardrails (quote-or-die, two-source rule, confidence-gated auto-merge).
- **Geen records ooit verwijderen**. Bij opheffing of mutatie wordt `valid_until` gezet. Historie is permanent.
- **Geen BSN, ooit.** Geen privé-adressen, geboortedata alleen als jaartal voor disambiguatie.
- **Provenance per record minimaal, per high-stakes veld optioneel**. Default `sources[]` op record-niveau. Inline `{value, source_url, retrieved, confidence}` alleen waar bronnen tegenspreken of waar accuracy kritiek is.

### Doelpubliek en gebruik

Onderzoekers, journalisten, civic-tech ontwikkelaars, ambtenaren die een referentie zoeken, Wikidata-gemeenschap die kan importeren, KOOP/OSF die mogelijk willen integreren. Niet voor consumenten direct (daar is de Allmanak-website voor).

## Architectuur

### Repo skeleton

```
polder/
├── data/
│   ├── organisaties/
│   │   ├── ministeries/        # YAML per ministerie + onderdelen
│   │   ├── zbo/
│   │   ├── agentschappen/
│   │   ├── rwt/
│   │   ├── hoge-colleges/
│   │   ├── gemeenten/          # 342 files
│   │   ├── provincies/         # 12 files
│   │   ├── waterschappen/      # 21 files
│   │   ├── gemeenschappelijke-regelingen/
│   │   ├── adviescolleges/
│   │   ├── inspecties/
│   │   ├── rechterlijke-macht/
│   │   ├── politie-om/
│   │   └── caribisch-nederland/
│   ├── personen/
│   │   ├── current/            # YAML per persoon, actieve mandaten
│   │   └── historisch/         # mandaten allemaal beëindigd
│   ├── posten/                 # functies los van zittende persoon
│   ├── mandaten/               # persoon × post × periode (kan ook inline in personen/)
│   └── _staging/               # LLM-proposals, niet auto-merged
├── schemas/
│   ├── organisatie.schema.json
│   ├── persoon.schema.json
│   ├── post.schema.json
│   ├── mandaat.schema.json
│   └── event.schema.json
├── src/
│   ├── fetchers/
│   │   ├── roo.py
│   │   ├── tk_odata.py
│   │   ├── ek_scrape.py
│   │   ├── logius_cor.py
│   │   ├── koop_sru.py
│   │   ├── wikidata_sparql.py
│   │   ├── allmanak.py
│   │   ├── open_raadsinformatie.py
│   │   ├── ar_rwt.py           # Algemene Rekenkamer RWT-lijst
│   │   ├── abd_organogrammen.py
│   │   └── kiesraad.py
│   ├── diff.py                 # YAML diff-engine, output naar diff.json
│   ├── validate.py             # JSON Schema validatie + custom checks
│   ├── build/
│   │   ├── to_sqlite.py        # voor Datasette
│   │   ├── to_csv.py           # Frictionless Data Package
│   │   ├── to_jsonld.py        # optionele afgeleide
│   │   └── to_datapackage.py
│   └── llm/
│       └── orchestrate.py      # roept skills aan, schrijft naar _staging
├── .claude/
│   ├── CLAUDE.md               # dit document, ingekort tot project-context
│   └── skills/
│       ├── parse-staatscourant/
│       │   └── SKILL.md
│       ├── entity-resolution/
│       │   └── SKILL.md
│       ├── parse-organogram/
│       │   └── SKILL.md
│       └── review-pr-diff/
│           └── SKILL.md
├── .github/
│   └── workflows/
│       ├── daily-update.yml
│       ├── validate.yml
│       └── publish.yml
├── docs/
│   ├── datamodel.md
│   ├── bronnen.md
│   └── avg-grenzen.md
├── datapackage.json            # Frictionless metadata voor data.overheid.nl
├── README.md
├── LICENSE-DATA                # CC0
├── LICENSE-CODE                # MIT
└── pyproject.toml
```

### Datamodel

**Organisatie** (voorbeeld `data/organisaties/ministeries/min-bzk.yaml`):

```yaml
id: org:min-bzk
type: ministerie
identifiers:
  oin: "00000001003214345000"
  tooi: https://identifier.overheid.nl/tooi/id/ministerie/mnre1034
  wikidata: Q1727053
  roo_id: "9632"
  kvk: null
classification: ministerie
parent_id: org:rijksoverheid
names:
  - value: Ministerie van Binnenlandse Zaken en Koninkrijksrelaties
    abbr: BZK
    valid_from: 2010-10-14
contact:
  website: https://www.rijksoverheid.nl/ministeries/ministerie-van-binnenlandse-zaken-en-koninkrijksrelaties
  bezoekadres: Turfmarkt 147, 2511 DP Den Haag
valid_from: 1798-08-12
valid_until: null
sources:
  - id: roo
    url: https://organisaties.overheid.nl/9632/
    retrieved: 2026-05-08
    fields: [names, classification, parent_id, contact]
```

**Persoon met mandaten inline** (voorbeeld `data/personen/current/jansen-jp-1965.yaml`):

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

**Post** (voorbeeld `data/posten/sg-min-bzk.yaml`):

```yaml
id: post:sg-min-bzk
organization_id: org:min-bzk
label: Secretaris-Generaal
classification: abd-tmg
seat_count: 1
valid_from: 1962-01-01
valid_until: null
```

### Identifier-strategie

- **Organisaties**: OIN als beschikbaar (Logius COR), anders eigen slug zoals `org:gemeente-utrecht`. Altijd TOOI-URI, Wikidata-Q en KvK/RSIN waar bekend.
- **Personen**: eigen stable slug `person:jansen-jp-1965` (familienaam + initialen + geboortejaar voor disambiguatie). Plus Wikidata-Q en TK-persoonId.
- **Posten**: eigen slug `post:sg-min-bzk` of `post:burgemeester-utrecht`.
- **Mandaten**: UUIDv7 (lexicografisch sorteerbaar op tijd), met expliciete `start_date` en `end_date`.

### Validatie-regels (afdwingen in `src/validate.py`)

1. Elk YAML-record valideert tegen het bijbehorende JSON Schema (`additionalProperties: false`).
2. Alle `*_id` referenties moeten resolven naar een bestaand record.
3. Geen overlappende mandaten op een single-seat post (waarschuwing, geen error, want kan in interim-periode voorkomen).
4. Geen records met `valid_until` in toekomst zonder bron.
5. Elk record heeft minstens één entry in `sources[]`.
6. Geboortedata alleen als jaartal (geen maand/dag), enforced.
7. Geen BSN-achtige patterns in tekstvelden (regex check op 9-cijferige reeksen die als BSN kunnen worden geïnterpreteerd).

## Bronnen

### Primaire feeds (deterministisch ophalen, geen LLM)

| Bron | URL/endpoint | Formaat | Update | Licentie | Dekking |
|---|---|---|---|---|---|
| ROO | `organisaties.overheid.nl`, `api-organisaties.overheid.nl`, dagelijkse `exportOO.xml` | XML/CSV/REST/SRU | dagelijks | CC0 | alle organisatietypes, bestuurders tot SG/DG/burgemeester/dijkgraaf |
| TOOI | `standaarden.overheid.nl/tooi`, URI's onder `identifier.overheid.nl/tooi/id/` | SKOS/RDF | gestaag | CC0 | URI-stelsel voor alle organisatietypes |
| TK OData | `gegevensmagazijn.tweedekamer.nl/OData/v4/2.0/` | OData v4 + Atom SyncFeed | near-realtime | open | TK-personen, fracties, commissies, vanaf 2008-09-01 |
| Logius COR | `oinregister.logius.nl`, `portaal.digikoppeling.nl/registers/corApi/` | REST | gestaag | open | OIN per organisatie |
| KOOP SRU | `repository.overheid.nl/sru` | SRU/XML | live | open | Staatscourant, KB's, sinds 2009 |
| Wikidata | `query.wikidata.org/sparql` | SPARQL | live | CC0 | Q-id crosswalks |
| Allmanak (OSF) | `rest-api.allmanak.nl/v0/` | PostgREST | gestaag | open | secundaire bron, eigen `systemid` |
| Open Raadsinformatie | `api.openraadsinformatie.nl/v1/elastic/` | Elastic + Popolo ODS | gestaag | open | 265+ gemeenten, raadsleden |
| Kiesraad | `data.overheid.nl` (zoek op authority Kiesraad) | CSV/XML | per verkiezing | open | uitslagen, kandidaatlijsten |
| Algemene Rekenkamer RWT | `www.rekenkamer.nl/onderwerpen/rwt-register` | HTML | jaarlijks | gebruik | RWT-lijst |
| Rijksfinanciën ZBO/Agentschap | jaarlijkse Excel/CSV via Min FIN | spreadsheet | jaarlijks | open | overzicht ZBO/agentschap |

### LLM-bronnen (parsing nodig, hoog risico op hallucinatie zonder guardrails)

| Bron | Type | Waarvoor | Risico |
|---|---|---|---|
| Staatscourant via KOOP SRU | XML met vrije tekst in body | benoemingen-KB's parsen | gemiddeld, met two-source rule mitigeerbaar |
| Eerste Kamer pagina's | HTML scrape | EK-leden, commissies | laag, structuur is consistent |
| Rijksoverheid.nl organogrammen | HTML + soms PDF/PNG | ABD-management onder TMG | gemiddeld, structuur varieert per ministerie |
| ABD-jaarverslagen | PDF | ABD-populatie aggregaten | laag (slechts validation, geen brondata) |

### Externe links (geen ingestion, alleen crosswalk)

- **berthub.eu/tkconv (OpenTK)**: deep-link template `https://berthub.eu/tkconv/persoon.html?nummer={tk_persoon_id}`. Voor elk TK-lid in `personen/` automatisch genereerbaar.
- **Wikidata items**: `https://www.wikidata.org/wiki/{wikidata}`.
- **Allmanak**: `https://www.allmanak.nl/cat/{cat_id}/...` waar mappable.

## Claude Code skills

Vier skills, elke onder `.claude/skills/<naam>/SKILL.md` met YAML frontmatter en heldere `description` zodat Claude Code ze on-demand laadt.

### 1. `parse-staatscourant`

**Doel.** Lees een Staatscourant-publicatie (XML met vrije besluittekst) en extract Membership-proposals.

**Input.** XML-document met KB-tekst.

**Output.** JSON-proposal met velden: `person_name`, `existing_person_id` (null als nieuw), `organization_id`, `post_id`, `role`, `start_date`, `end_date`, `decision_reference`, `confidence`, `evidence_snippet`.

**Harde regels.**
- `evidence_snippet` MOET letterlijke substring zijn van de bron-tekst. Pre-output check: substring-test in code, faal als false.
- `confidence` als float [0, 1], met expliciete justificatie in een `confidence_reasoning`-veld.
- Schrijf nooit direct naar `data/`, alleen naar `data/_staging/staatscourant-YYYY-MM-DD.json`.

### 2. `entity-resolution`

**Doel.** Match een nieuwe persoonsverwijzing ("dr. ir. J.P. Jansen") aan een bestaande `person:*` slug, of stel een nieuwe slug voor.

**Input.** Naam-string, optionele context (organisatie, datum), lijst van kandidaat-personen uit `data/personen/`.

**Output.** `{ matched_id: "person:jansen-jp-1965", confidence: 0.93, reasoning: "..." }` of `{ matched_id: null, proposed_id: "person:jansen-jp-1965", reasoning: "..." }`.

**Harde regels.**
- Embedding-similarity plus name-distance plus geboortejaar-check, niet alleen name-match.
- Als meerdere kandidaten boven 0.7 confidence, output alle kandidaten en zet final confidence op 0.5 om review te forceren.

### 3. `parse-organogram`

**Doel.** Vision-tool op een PDF of PNG van een organogram, extract organisatie-hierarchie en bemenste posten.

**Input.** Pad naar afbeelding of PDF.

**Output.** Lijst van proposals: orgaanstructuur (parent → child) plus eventueel personen-op-posten.

**Harde regels.**
- Output ALTIJD met `bron_pagina_nummer` en `bron_url`.
- Personen-extracties krijgen automatisch confidence ≤ 0.85 (vision is foutgevoelig).
- Schrijf naar `data/_staging/organogram-{ministerie}-{datum}.json`.

### 4. `review-pr-diff`

**Doel.** Genereer een Nederlandstalige PR-summary uit een diff.json, gegroepeerd per organisatie, met flags voor low-confidence wijzigingen.

**Input.** `diff.json` met alle wijzigingen van een dagelijkse run.

**Output.** Markdown PR-body met secties per organisatie en een tabel met confidence-scores.

**Harde regels.**
- Niet zelf data wijzigen, alleen samenvatten.
- Confidence < 0.95 of bemenst veld op "rood"-niveau (zie AVG-sectie) → expliciet flaggen.

## Workflow

### Daily update (GitHub Actions, cron 05:17 UTC)

1. Parallel deterministische fetchers draaien (`src/fetchers/*.py`), output naar `_cache/`.
2. `src/diff.py` vergelijkt `_cache/` met `data/`, schrijft `diff.json` en `proposals.json`.
3. Voor elke nieuwe Staatscourant-KB roept `src/llm/orchestrate.py` de `parse-staatscourant` skill aan, schrijft naar `data/_staging/`.
4. `src/validate.py` valideert het volledige resultaat tegen alle schemas.
5. `peter-evans/create-pull-request@v6` opent PR. Label-keuze:
   - Alle wijzigingen confidence ≥ 0.95 EN geen veld op "rood"-lijst → label `auto-merge`.
   - Anders → label `needs-review`.
6. `review-pr-diff` skill genereert PR-body.
7. Een aparte workflow merget `auto-merge` PR's na green CI (alle schemas valid, geen referentiële integriteit broken).

### Hallucinatie-mitigaties als harde regels

1. **No-claim-without-source**: schema enforced, elke value in een record heeft een entry in `sources[]`.
2. **Quote-or-die**: LLM-proposals bevatten `evidence_snippet` als letterlijke substring van bron, gevalideerd in code.
3. **Two-source rule** voor benoemingen: Staatscourant plus minstens één andere bron, OF Staatscourant met confidence ≥ 0.98 EN human-on-the-loop window van 7 dagen voor merge.
4. **Confidence per claim**: numeriek, met `confidence_reasoning` als string.
5. **Diff-only mode**: LLM krijgt alleen de delta plus relevante bestaande records, nooit een open "vul aan naar beste vermogen"-prompt.
6. **No deletes ever**: opheffing of einde mandaat = `valid_until` zetten, record blijft.

## AVG en juridische cut-off

ABRvS-doctrine sinds 31 januari 2018 (ECLI:NL:RVS:2018:314): namen mogen openbaar als de medewerker "uit hoofde van functie in de openbaarheid treedt".

**🟢 Groen — publiceren standaard.**
Bewindspersonen, Kamerleden TK+EK, CdK, gedeputeerden, statenleden, burgemeesters, wethouders, raadsleden, dijkgraven, DB-leden waterschap, AB-leden waterschap, voorzitters Hoge Colleges van Staat, RvB-leden ZBO's, ABD-Topmanagementgroep, rechters en raadsheren, gezaghebbers Caribisch Nederland, griffiers.

**🟡 Geel — alleen functionele gegevens, alleen reeds publiek door organisatie zelf.**
ABD-managers schaal 15 en 16 op directieniveau, inspecteurs-generaal, secretarissen ZBO's en agentschappen, bestuurders en secretarissen GR's. Geen privé-contactgegevens.

**🔴 Rood — niet doen.**
Beleidsmedewerkers, juristen, communicatie, handhavers, dossierbehandelaars. Altijd: privé-contactgegevens, geboortedata (alleen jaar voor disambiguatie), BSN, WNT-salarisdata buiten publicatieplicht, foto's, bijzondere persoonsgegevens.

**Practicalia.**
- DPIA en verwerkingsregister bij start (>1M persoonsgegevensrecords).
- Takedown-flow van max 14 dagen via GitHub-issue-template.
- Bij bedreigde ambtsdragers (bv. Veilig Bestuur-meldingen) opt-out op verzoek.
- Geen aggregatie met sociale-media-data, geen profielbouw.

## MVP-roadmap

### Week 1 — Skeleton

- [ ] Repo aanmaken op `github.com/anneschuth/polder` (later eventueel migreren naar `github.com/openpolder/polder` of community-org).
- [ ] `pyproject.toml` met dependencies: `httpx`, `pyyaml`, `jsonschema`, `pydantic`, `tkapi`, `rdflib`, `frictionless`, `datasette`.
- [ ] JSON Schemas in `schemas/` voor Organisatie, Persoon, Post, Mandaat, Event.
- [ ] README met scope, datamodel, bronnen, AVG-cut-off.
- [ ] LICENSE-DATA (CC0) en LICENSE-CODE (MIT).
- [ ] CI-pipeline `.github/workflows/validate.yml` die alle YAML in `data/` valideert.

### Week 2 — Eerste fetcher

- [ ] `src/fetchers/roo.py`: download dagelijks `exportOO.xml`, parse naar YAML in `data/organisaties/`.
- [ ] Mapping ROO-id naar eigen `org:`-slug, plus opname OIN, TOOI-URI, Wikidata-Q, KvK waar bekend (laat dat laatste eerst leeg).
- [ ] `src/diff.py`: YAML-aware diff die alleen content-changes detecteert, niet whitespace.
- [ ] Eerste commit met ROO-data: alle ministeries, agentschappen, ZBO's, gemeenten, provincies, waterschappen op organisatie-niveau (geen onderdelen).
- [ ] Acceptatiecriterium: `make validate` slaagt, ~3000 organisatie-records gecommit.

### Week 3 — TK OData

- [ ] `src/fetchers/tk_odata.py` op basis van `tkapi`-library.
- [ ] Pull alle Persoon, PersoonNevenfunctie, PersoonLoopbaan, Fractie, FractieZetel, Commissie, CommissieZetel.
- [ ] Schrijf naar `data/personen/current/` met `tk_persoon_id` als external_id.
- [ ] Mandaten als TK-Kamerlid genereren met start/einde uit FractieZetel-data.
- [ ] Genereer in README de tkconv-deeplink-template.
- [ ] Acceptatiecriterium: 150 huidige TK-leden plus historische TK-leden vanaf 2008-09-01.

### Week 4 — Crosswalk

- [ ] `src/fetchers/logius_cor.py`: pull OIN-register, koppel aan ROO-records via match op naam + KvK.
- [ ] `src/fetchers/wikidata_sparql.py`: SPARQL-query voor Q-id's van alle ministeries, gemeenten, provincies, waterschappen, en alle huidige TK-leden.
- [ ] Schrijf identifier-crosswalk weg in elk record.
- [ ] Acceptatiecriterium: ≥80% van organisaties heeft Wikidata-Q, ≥95% heeft OIN, 100% van TK-leden heeft Wikidata-Q en TK-persoonId.

### Week 5 — Daily workflow + review-skill

- [ ] `.github/workflows/daily-update.yml` met cron 05:17 UTC.
- [ ] `peter-evans/create-pull-request@v6` voor PR-creatie.
- [ ] `.claude/skills/review-pr-diff/SKILL.md` met instructies en voorbeelden.
- [ ] Eerste skill-aanroep via `anthropics/claude-code-action@v1` met `--allowedTools Read`.
- [ ] Geen LLM-writes nog, alleen review-summaries op deterministische diffs.
- [ ] Acceptatiecriterium: PR's verschijnen automatisch met Nederlandstalige summary.

### Week 6 — Publicatie

- [ ] `src/build/to_sqlite.py`: bouw SQLite uit YAML voor Datasette.
- [ ] `src/build/to_datapackage.py`: Frictionless metadata.
- [ ] Publish-workflow naar GitHub Pages plus Datasette Cloud (of self-host).
- [ ] Aanmelden bij `data.overheid.nl` als dataset.
- [ ] Eerste blogpost op `anneschuth.nl` over Polder, met linkverwijzingen naar OSF, KOOP en berthub.eu/tkconv.
- [ ] Acceptatiecriterium: publieke Datasette online, ≥1 externe link in blogpost.

## Iteratie 2 (maand 2-5, lossere planning)

- **Maand 2**: `ek_scrape.py` voor Eerste Kamer, `abd_organogrammen.py` voor Rijksoverheid.nl per ministerie, `ar_rwt.py` voor RWT-lijst.
- **Maand 3**: `koop_sru.py` voor Staatscourant + `parse-staatscourant` skill met two-source rule en confidence-gated auto-merge.
- **Maand 4**: `entity-resolution` skill, plus `open_raadsinformatie.py` voor wethouders en raadsleden via OSF.
- **Maand 5**: outreach naar Tom Kunzler (OSF), KOOP, en Bert Hubert. Wikidata-PR's via PoliLoom-achtige workflow.

## Eerste concrete stappen voor Claude Code

Voor de allereerste sessie met Claude Code:

```bash
# 1. Setup
mkdir polder && cd polder
git init
gh repo create anneschuth/polder --public --source=. --remote=origin

# 2. Skeleton scaffolden
# Vraag Claude Code: "Bootstrap de polder-repo zoals beschreven in CLAUDE.md.
# Maak de folder-structuur, pyproject.toml, eerste JSON Schemas voor Organisatie
# en Persoon, README.md, LICENSE-DATA (CC0), LICENSE-CODE (MIT), en een minimale
# .github/workflows/validate.yml die alle YAML in data/ tegen schemas valideert."

# 3. Eerste fetcher
# Vraag Claude Code: "Implementeer src/fetchers/roo.py die exportOO.xml ophaalt
# van organisaties.overheid.nl, parset naar Organisatie-records volgens
# schemas/organisatie.schema.json, en schrijft naar data/organisaties/<type>/<slug>.yaml.
# Gebruik httpx voor downloaden, lxml voor XML-parsing, pyyaml voor schrijven.
# Validatie via jsonschema. Sla TOOI-URI's op uit het XML. Geen LLM nodig."
```

## Beslissingen die nog open liggen

- **Repo-org vs persoonlijk**: starten als `anneschuth/polder` of direct nieuwe org `openpolder` aanmaken? Suggestie: persoonlijk starten, na MVP migreren als community ontstaat.
- **OSF-outreach voor of na MVP**: voor (om dubbel werk te voorkomen) of na (om iets te laten zien)? Suggestie: korte mail naar Tom Kunzler in week 1, MVP doorbouwen ongeacht antwoord.
- **Mandaten als aparte folder of inline in personen**: technisch beide werkbaar. Inline is leesbaarder, aparte folder schaalt beter bij vele historische mandaten per persoon. Suggestie: starten inline, refactoren als personen-files >50KB worden.
- **Datasette-host**: self-host op Hetzner versus Datasette Cloud? Suggestie: GitHub Pages voor static files plus Datasette Cloud voor query-interface, gratis tier voldoende voor MVP.

## Kosten-inschatting

- **GitHub Actions**: free tier (publieke repo).
- **LLM-budget** (Claude Haiku 4.5 + occasioneel Sonnet 4.6): $30-50/maand routine, $75-150/maand bij actieve verkiezings- of formatieperioden, $200-400 eenmalig voor bulk-import.
- **Datasette Cloud**: $9/maand starter, optioneel.
- **Domein**: `polder.nl` mogelijk niet beschikbaar; `polder.dev`, `getpolder.nl`, `polder-data.nl` als fallback.

## Tagline en framing

> **Polder** — Wie regeert Nederland, in YAML, dagelijks bijgewerkt.

Voor de README. Verwijs naar de Sdu-Staatsalmanak (€419) en Allmanak (gratis website) als verwante producten met andere positionering, zonder ze als concurrent te framen.
