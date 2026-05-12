# Polder CLI

`polder` is het ENIGE entrypoint voor de polder-toolchain. Alle subcommands
zijn dunne wrappers rond bestaande Python-functies of bash-scripts; geen
losse logica.

```bash
uv run polder --help
```

## Top-level

| Commando | Doel |
| --- | --- |
| `polder fetch <bron>` | Haal data op uit een externe bron |
| `polder validate` | JSON Schema-validatie + cross-record checks |
| `polder diff` | Vergelijk `_cache/` met `data/`, schrijf `diff.json` |
| `polder build [target]` | Bouw `dist/polder.db`, CSV's, datapackage |
| `polder list <subject>` | Lijst entiteiten (organisaties, personen, ...) |
| `polder show <id>` | Detail-view voor een enkele entiteit |
| `polder export <fmt> <out>` | Exporteer alles naar CSV of JSON |
| `polder skill <name>` | Roep een Claude Code skill aan |
| `polder daily-update` | Run de daily-update pipeline lokaal |
| `polder serve` | Start datasette op `dist/polder.db` |

Top-level opties (gelden op alle subcommands):

- `-v` / `--verbose`: zet logging op DEBUG en exporteert `POLDER_VERBOSE=1`.

## `polder fetch`

```
polder fetch roo       # ROO exportOO.xml
polder fetch tk        # Tweede Kamer OData
polder fetch ek        # Eerste Kamer scrape
polder fetch logius    # Logius CoR
polder fetch wikidata  # Wikidata SPARQL
polder fetch ar-rwt    # Algemene Rekenkamer RWT-register
polder fetch koop      # KOOP SRU (Staatscourant)
polder fetch ori       # Open Raadsinformatie
polder fetch tooi      # TOOI thesaurus
polder fetch kiesraad  # Kiesraad
polder fetch abd       # ABD-organogrammen (PDF-cache)
polder fetch all       # Alle deterministische fetchers
```

Iedere subcommand accepteert dezelfde basis-flags:

- `--cache PATH` (default `_cache`)
- `--out PATH` (per-fetcher default, zie `polder fetch <bron> --help`)
- `--limit N` (max records, voor testen)
- `--dry-run` (niets schrijven)
- `-v` / `--verbose`

`polder fetch all` doet de deterministische fetchers sequentieel. ABD en KOOP
zitten er niet in; die hebben aparte LLM-stappen via `polder skill ...`.

```
polder fetch all --fail-fast      # stop bij eerste failure
polder fetch all --limit 10       # smoke-test, weinig records
```

## `polder build`

```
polder build all          # default
polder build sqlite       # alleen dist/polder.db
polder build csv          # alleen dist/csv/*.csv
polder build datapackage  # alleen dist/datapackage.json
```

Flags: `--data-dir` (default `data`), `--dist-dir` (default `dist`).

## `polder list`

```
polder list organisaties [--type ministerie] [--format table|json|csv]
polder list personen [--current] [--classification minister]
polder list posten [--organization org:min-bzk]
polder list mandaten [--organization org:min-bzk] [--person person:...]
```

## `polder show <id>`

ID-vorm bepaalt de lookup: `org:`, `person:`, `post:` of mandaat-ID.

```
polder show org:min-bzk
polder show person:rutte-mjm-1967 --history --links
polder show post:sg-min-bzk --format yaml
```

## `polder export <fmt> <out>`

```
polder export json out/
polder export csv out/
```

Schrijft `organisaties.{json,csv}`, `personen.*`, `posten.*`, `mandaten.*` naar
`out/`.

## `polder skill`

```
polder skill review-diff diff.json [output.md]
polder skill parse-staatscourant kb.xml [output.json]
polder skill parse-organogram organogram.pdf min-bzk [output.json]
polder skill entity-resolution input.json [output.json]
```

Alle skills draaien in-process via `polder.llm.runner`, die `claude -p
--input-format stream-json` op je lokale Claude Code subscription
aanroept. Schrijven nooit direct naar `data/`, alleen naar
`data/_staging/`.

`polder backfill abd-nieuws` en `polder backfill staatscourant` draaien een
skill op alle reeds gedownloade cache-input. Handig na een schema- of
skill-tweak om de hele historie opnieuw door de nieuwste skill te halen.

## `polder resolve`

```
polder resolve                                # data/_staging/, code-only
polder resolve --overwrite                    # overschrijf .resolved.json
polder resolve --enrich-wikidata              # vul birth_year via Wikidata
polder resolve data/_staging/abd-2024.json    # losse file
```

Code-only resolver voor `data/_staging/*.json`. Schrijft per input een
`.resolved.json`-companion met `resolved_organization_id`,
`resolved_post_id`, `resolved_person_id`, `resolution_confidence` en
`merge_recommendation`. Vervangt de dure `resolve-staging-proposals`
LLM-skill voor het overgrote deel van de proposals.

Strategie persoon-matching, in volgorde van strikt naar laks:
`family+initials+birth_year` (0.98), `family+given` (0.92),
`family+initials` zonder jaar (0.88), `family unique` (0.70). Onbekende
posten worden `creatable_from_role` (0.85) als de role op een schema-
classification mapt. Onbekende personen met family-niet-in-data zijn
`creatable_new_person` (0.85) mits er een birth_year is.

`--enrich-wikidata` opent een Wikidata-reconciliation-lookup voor
`no_match`-personen om alsnog een birth_year op te halen. Strict-filter:
één plausibele kandidaat, leeftijd 18-80, naam-match op family + given.
Standaard uit zodat de basisrun snel en offline blijft.

## `polder apply-staging`

```
polder apply-staging                          # data/_staging/, dry-run
polder apply-staging --apply                  # echt schrijven
polder apply-staging --only-high-confidence   # alleen >= 0.95
polder apply-staging --skip-persons           # alleen orgs en posts
```

Past de `.resolved.json`-output toe op `data/`. Vereisten voor auto-merge:
`merge_recommendation == "auto-merge"`, classifiable role, publieke
http(s) bron-URL, `start_date` aanwezig, chain consistent met
`organization_id`, en (bij name-based lookup op chain-entries) de
canonical parent in data/ overeenkomt met de chain-parent. Twee proposals
voor dezelfde nieuwe persoon (gecombineerde functie) leveren één
persoon-record op met meerdere mandaten.

## `polder daily-update`

Spiegelt `.github/workflows/daily-update.yml` lokaal: run alle deterministische
fetchers, dan `polder validate`, `polder diff`, en de review-pr-diff skill.
Geen commits, geen PR; Anne reviewt zelf.

## `polder serve`

```
polder serve                              # dist/polder.db op :8001
polder serve --port 8080 --host 0.0.0.0
polder serve --db /pad/naar/db.db --metadata /pad/naar/metadata.json
```

## Backwards-compatible scripts

De oude entrypoints blijven werken; ze zijn equivalent aan een `polder`-
subcommand:

| Oud | Nieuw |
| --- | --- |
| `polder-fetch-roo` | `polder fetch roo` |
| `polder-fetch-tk-odata` | `polder fetch tk` |
| `polder-fetch-ek-scrape` | `polder fetch ek` |
| `polder-fetch-logius-cor` | `polder fetch logius` |
| `polder-fetch-wikidata` | `polder fetch wikidata` |
| `polder-fetch-ar-rwt` | `polder fetch ar-rwt` |
| `polder-fetch-koop` | `polder fetch koop` |
| `polder-fetch-ori` | `polder fetch ori` |
| `polder-fetch-tooi` | `polder fetch tooi` |
| `polder-fetch-kiesraad` | `polder fetch kiesraad` |
| `polder-fetch-abd` | `polder fetch abd` |
| `polder-validate` | `polder validate` |
| `polder-diff` | `polder diff` |
| `polder-build` | `polder build` |

Voor nieuwe code: gebruik `polder <subcommand>`. De oude scripts mogen blijven
in CI en in scripts die er al naar verwijzen.
