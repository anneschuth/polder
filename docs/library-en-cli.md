# Polder library en CLI

Ontwerp voor een Python library en CLI bovenop de Polder-dataset. Dit document beschrijft het ontwerp, niet de implementatie. Implementatie volgt na week 6 van de MVP-roadmap.

## 1. Doel en scope

Polder is YAML in git. Dat is goed voor versionering en review, maar slecht voor consumenten. Een onderzoeker die wil weten welke ministers tussen 2015 en 2020 ook Kamerlid waren, wil geen 350 YAML-files parsen, refs resolven en datums vergelijken. De library lost dat op door één import en een handvol queries.

Doelgebruikers:

- Onderzoekers (politicologen, bestuurskundigen) die analyses willen draaien.
- Journalisten die deeplinks, achtergronden en historie willen ophalen.
- Civic devs die Polder als databron in een eigen app of dashboard willen gebruiken.
- Ambtenaren die een referentie zoeken zonder Sdu-Staatsalmanak-abonnement.

Wat NIET in scope is:

- Mutaties van de dataset. Dat is het werk van de fetcher-pipeline en LLM-skills. De library is read-only.
- Een gehoste API. Wie dat wil, draait Datasette op de gebouwde SQLite. De library blijft een lokaal Python-object.
- Vertaling van velden naar andere talen. Veldnamen volgen het YAML-schema 1-op-1.

## 2. Twee artefacten, één codebase

Library en CLI delen één Python-package en één pyproject. Geen split.

```
src/polder/
├── lib/                    # publieke API
│   ├── __init__.py         # re-export Polder, repos, models
│   ├── core.py             # Polder-klasse, loaders
│   ├── repos.py            # OrgRepo, PersoonRepo, PostRepo, MandaatRepo
│   ├── models.py           # pydantic v2 models, gegenereerd uit JSON Schema
│   ├── filters.py          # predicate-helpers (active_on, classification, etc.)
│   └── export.py           # to_pandas, to_polars, to_sqlite
├── cli/
│   ├── __init__.py
│   ├── main.py             # typer-app
│   ├── list_cmd.py
│   ├── show_cmd.py
│   ├── export_cmd.py
│   ├── query_cmd.py
│   └── render.py           # rich-tabellen, JSON/CSV-output
├── fetchers/               # blijft staan, interne tooling
├── build/                  # blijft staan
├── validate.py             # blijft staan
└── diff.py                 # blijft staan
```

`pyproject.toml` krijgt:

```toml
[project.scripts]
polder = "polder.cli.main:app"
```

Alles onder `src/polder/lib/` is publieke API met semver-garantie. Alles onder `fetchers/`, `build/`, `validate.py` en `diff.py` is intern en mag breken zonder major-bump.

## 3. Library API

### 3.1 Centrale klasse

```python
from polder import Polder
from datetime import date

p = Polder.local("./polder")
p = Polder.from_git("anneschuth/polder", ref="v0.1.0")
p = Polder.from_release("0.1.0")
```

Drie loaders, elk met dezelfde returntype. `local` opent een lokaal pad, `from_git` cloned shallow naar `~/.cache/polder/<ref>`, `from_release` haalt een release-tarball van GitHub Releases. De cache is reusable en wordt door TTL ingevalideerd, niet door checksum.

### 3.2 Pydantic-models

Pydantic v2, gegenereerd uit `schemas/*.schema.json` met `datamodel-code-generator`. Build-time stap, niet runtime. De gegenereerde `models.py` wordt gecommit zodat consumenten geen build-tooling nodig hebben.

```python
from polder import Organisatie, Persoon, Post, Mandaat, Event
```

Geen handmatige duplicatie tussen JSON Schema en Pydantic. Wijziging in schema, regenereer, commit. Schema-breaking change = library MAJOR-bump.

### 3.3 Repositories

Vier repos op `Polder`:

```python
p.organisaties   # OrgRepo
p.personen       # PersoonRepo
p.posten         # PostRepo
p.mandaten       # MandaatRepo
```

Elke repo biedt:

```python
repo.all() -> Iterator[Model]
repo.get(id: str) -> Model               # raises NotFound
repo.find(id: str) -> Model | None
repo.where(predicate: Callable) -> Iterator[Model]
repo.with_identifier(kind, value) -> Model | None
```

Repo-specifieke methods waar nuttig:

```python
p.organisaties.by_type("ministerie")
p.organisaties.by_classification("gemeente")
p.organisaties.active_on(date(2024, 1, 1))

p.personen.with_classification("bewindspersoon", on_date=date.today())
p.personen.current()                                # iedereen met ten minste één lopend mandaat

p.posten.at_organization("org:min-bzk")
p.posten.by_classification("abd-tmg")

p.mandaten.during(date(2024, 1, 1), date(2024, 12, 31))
p.mandaten.at_organization("org:min-bzk")
p.mandaten.for_post("post:sg-min-bzk")
p.mandaten.for_person("person:jansen-jp-1965")
```

Predicates zijn gewone callables zodat `where(lambda m: m.role.startswith("Plv"))` werkt zonder DSL te leren.

### 3.4 Lazy loading en caching

Repos parsen bij eerste aanroep, niet bij `Polder.local()`. Dat houdt opstarten goedkoop. Eenmaal geparsed blijft het in een dict in-memory. Geen LRU, want de hele dataset past ruim in geheugen (geschat <100MB voor v1.0). Wel een `Polder.reload()` om in een notebook bij te werken zonder Python-restart.

### 3.5 Relationship-traversal

Pydantic-models krijgen attributen die door de repo worden gevuld via een terug-referentie naar `Polder`. Geen ORM, geen lazy proxies; gewoon properties die bij first-access opzoeken.

```python
persoon.current_mandaten()                  # filter mandaten waar end_date is None
mandaat.organisatie                         # Organisatie via organization_id
mandaat.post                                # Post via post_id
mandaat.persoon                             # back-reference
post.huidige_houder()                       # Persoon | None
post.history()                              # list[Mandaat] gesorteerd op start_date
gemeente.huidige_burgemeester()             # convenience: post:burgemeester-<slug>
ministerie.huidige_sg()
```

De convenience-methods zijn opt-in en alleen op concrete subtypes gedefinieerd. `Organisatie.huidige_burgemeester()` bestaat alleen op organisaties met `type == "gemeente"`. Een Pydantic discriminator op `type` lost dat op.

### 3.6 Identifier-lookup

Crosswalk via `with_identifier`:

```python
p.organisaties.with_identifier("wikidata", "Q1727053")
p.organisaties.with_identifier("oin", "00000001003214345000")
p.personen.with_identifier("tk_persoon_id", "12345")
```

Achterliggend een dict per identifier-kind, eenmalig opgebouwd.

### 3.7 Export

```python
df = p.to_pandas("organisaties")
lf = p.to_polars("personen", flatten_mandaten=True)
db = p.to_sqlite("/tmp/polder.db")          # roept build/to_sqlite intern aan
```

`to_pandas` en `to_polars` flattenen geneste velden naar kolommen op een voorspelbare manier (`identifiers.wikidata` wordt `identifiers_wikidata`). `to_sqlite` is een dunne wrapper rond `src/polder/build/to_sqlite.py` zodat library-gebruikers en de CLI dezelfde build-output krijgen.

### 3.8 Bron-traceerbaarheid

```python
gemeente.sources                            # list[Source]
mandaat.evidence_url                        # appointment.staatscourant_url als shortcut
mandaat.confidence                          # float | None
```

Provenance is first-class in het datamodel; de library exposeert het zonder transformatie.

## 4. CLI

`typer` boven `click`. Reden: `typer` leunt op type-hints, dat past bij een pydantic-codebase en geeft gratis `--help`. `click` is volwassener maar voegt boilerplate toe die hier niets oplost.

`[project.scripts] polder = "polder.cli.main:app"`.

### 4.1 Subcommands

```
polder list organisaties [--type ministerie] [--active-on 2024-01-01]
polder list personen [--classification kamerlid] [--current]
polder list posten [--organization org:min-bzk]
polder list mandaten [--during 2024-01-01:2024-12-31]

polder show org:gemeente-utrecht
polder show person:rutte-mp-1967 --history
polder show post:sg-min-bzk --history

polder query "SELECT * FROM mandaten WHERE start_date > '2024-01-01'"

polder export sqlite ./out/polder.db
polder export csv ./out/
polder export datapackage ./dist/

polder pull [--ref main]
polder validate [./data]
polder diff <ref1> <ref2>
polder serve [--port 4321]                # lokale site (organogram)
polder serve db [--port 8001]             # datasette
```

`pull` cloned of fetcht een release naar de lokale cache. `validate` is een wrapper rond `polder.validate`. `diff` toont YAML-diff tussen twee refs zoals `src/diff.py` doet, geformatteerd voor terminal. `serve` bouwt SQLite-in-temp en start Datasette.

### 4.2 Output

Default rendering via `rich`-tabellen met automatische kleurdetectie. Als stdout geen tty is (pipe of redirect), valt het terug op platte tekst. Drie output-formats:

```
--format table       # default, rich
--format json
--format csv
--format yaml
```

`polder show` gebruikt een vertical-layout key-value tabel; `polder list` een horizontal table. `--history` op `show` print een tijdlijn van mandaten.

### 4.3 Globale flags

```
--data PATH          # override default data-locatie
--quiet
--verbose
--no-color
```

Standaard zoekt de CLI in volgorde: `$POLDER_DATA`, `./data` (als in een polder-checkout), `~/.cache/polder/main`. Als geen van die paden bestaat, vraagt `polder pull` impliciet aan de gebruiker.

## 5. Versionering en releases

- Library volgt SemVer. Schema-breaking change = MAJOR. Toevoegen van een repo-method = MINOR. Bug fix in een filter = PATCH.
- Data volgt CalVer (`YYYY.MM.DD`) als snapshot-tags op de repo. Daily commits zijn niet getagd; alleen cherry-picked snapshots.
- Library en data zijn ontkoppeld. Een `polder` 1.4.2 kan tegen een `2026.05.09` snapshot draaien. Compatibility-matrix in de README.

## 6. Distributie

PyPI publish via `gh workflow` getriggerd op een release-tag (`v0.1.0`). Workflow draait `uv build` en `uv publish` met een trusted publisher.

```bash
pip install polder
uv add polder
```

Library bevat geen data. `Polder.local()` op een leeg pad faalt expliciet met instructie om `polder pull` te draaien.

Optioneel een extra:

```bash
pip install polder[data]
```

Die installeert een snapshot van de dataset als package-data. Bedoeld voor offline gebruik en voor CI van downstream-projecten die geen netwerk willen. Snapshot-frequentie: bij elke MAJOR en MINOR library-release.

## 7. Tests en kwaliteit

- `pytest` met coverage-target 90% op `src/polder/lib/`. CLI-coverage 70% (de rest is render-detail).
- Snapshot-fixture in `tests/fixtures/mini-polder/` met 10 organisaties, 10 personen, 5 posten en 20 mandaten die alle classifications dekken. Dezelfde fixture voor library- en CLI-tests.
- `mypy --strict` over `src/polder/lib/`. CLI mag laxer wegens decorator-magie van typer.
- Doctests in elke publieke method. `pytest --doctest-modules` als CI-stap.
- Performance-budget:
  - Parsing van 100k records onder 5 seconden op een M1.
  - Repo-query gemiddeld onder 100ms na warmup.
  - CLI cold-start onder 300ms (lazy imports nodig, met name `polars` en `pandas` achter `import` binnen functies).

Performance-tests in `tests/perf/` met `pytest-benchmark`, niet in elke run, wel wekelijks via een aparte workflow.

## 8. Documentatie

- README krijgt een sectie "Voor consumenten" met installatie en een minimaal voorbeeld dat in onder 10 regels werkt.
- API-docs via `mkdocs-material` plus `mkdocstrings`, gehost op GitHub Pages onder `polder.anneschuth.nl/api/`.
- `docs/voorbeelden/` met Jupyter-notebooks:
  - `wie-was-burgemeester-utrecht-sinds-2010.ipynb`
  - `ministers-tegelijk-kamerlid.ipynb`
  - `geboortejaar-distributie-hoge-colleges.ipynb`
  - `crosswalk-naar-wikidata.ipynb`
- Notebooks draaien in CI via `nbmake` op de fixture-data, zodat ze niet verouderen.

## 9. Implementatie-roadmap

Week 7, na MVP-roadmap:

- Pydantic-models genereren uit JSON Schemas, `datamodel-code-generator` integreren in build (1 dag).
- `Polder` plus 4 repos met basis methods `all`, `get`, `where`, `with_identifier` (2 dagen).
- CLI met 5 kerncommands `list`, `show`, `export`, `validate`, `pull` (2 dagen).
- Tests, doctests, README-sectie (2 dagen).
- Release v0.1.0 op PyPI.

Week 8 en 9:

- Pandas en Polars export.
- `polder query` met SQLite-in-memory.
- `polder serve` met Datasette embed.
- Notebooks en `mkdocs`-site.
- Release v0.2.0.

Week 10 en later:

- Convenience-methods (`huidige_burgemeester`, `huidige_sg`, etc.) op specifieke org-types.
- `Polder.from_release` met release-tarball download.
- Release v1.0.0 zodra de publieke API stabiel aanvoelt en er minstens twee externe consumenten zijn.

## 10. Open beslissingen

- Pydantic models: handmatig of via `datamodel-code-generator`? Voorlopige keuze: code-gen, omdat handmatig dupliceren binnen een maand uit sync loopt.
- typer of click? Voorlopige keuze: typer, omdat de codebase al pydantic-zwaar is en type-hints daar natuurlijk passen.
- Packaging: één package `polder` voor library plus fetchers, of split tussen `polder` (consumer) en `polder-pipeline` (interne tooling)? Voorlopige keuze: één package met `[project.optional-dependencies]` voor `pipeline`-tooling, zodat consumenten geen `lxml`, `tkapi` of `frictionless` hoeven te installeren.
- PyPI-naam: `polder`, `polder-data` of `polder-nl`? `polder` is kort en mogelijk vrij; reserveer eerst, anders `polder-nl`.

## Beslissingen die nog open liggen

- Pydantic models handmatig of code-gen.
- typer of click voor de CLI.
- Eén package met optional-deps of split-package consumer versus pipeline.
