# Bijdragen aan Polder

Pull requests welkom. Polder is een git-versioned dataset, dus elke wijziging in `data/` is een commit met een spoor terug naar bron en context.

## Werkwijze

Open eerst een issue voor inhoudelijke voorstellen. Voor een feitelijke correctie volstaat het data-bug-formulier. Voor takedown-verzoeken is er een apart AVG-formulier met een respons-termijn van veertien dagen.

Werk in een feature-branch en open een PR tegen `main`. Houd PR's klein en gericht op één onderwerp.

## Branchnamen

- `feature/<korte-omschrijving>` voor nieuwe features of fetchers
- `fix/<korte-omschrijving>` voor bugs in code of build
- `data/<korte-omschrijving>` voor handmatige data-correcties

## Padconventies

YAML-records onder `data/` volgen de Popolo-classes:

- `data/organizations/<slug>.yml`
- `data/people/<slug>.yml`
- `data/posts/<org-slug>/<post-slug>.yml`
- `data/memberships/` voor losse mandaat-records waar dat nodig is

Slugs zijn lowercase, kebab-case, ASCII. Stable id's volgen het patroon `person:jansen-jp-1965` of `org:min-bzk`.

## Validatie

Elke PR met wijzigingen in `data/` moet door schema-validatie:

```bash
uv run polder validate
```

CI draait dezelfde validatie. Een rode CI is een blocker.

## AVG

Polder respecteert de AVG-cut-off uit `docs/avg-grenzen.md`. Toevoegingen van personen vallen in de groene, gele of rode zone. Rood publiceren we niet. Geel alleen functionele gegevens, alleen reeds publiek door de organisatie zelf. Twijfel over de zone, vraag het in het issue voor je een PR opent.

## Tests

```bash
uv run pytest
```

Voor wijzigingen in fetchers of parsers: voeg een test toe met een fixture in `tests/fixtures/`. Geen synthetische LLM-output als fixture, gebruik een echte response-snippet.

## Code style

Python 3.12 of nieuwer. Type hints overal. Ruff voor formatting en linting:

```bash
uv run ruff format src tests
uv run ruff check src tests
```

Geen onnodige abstracties, geen frameworks zonder duidelijke meerwaarde.

## Commits

- Schrijf commit-messages in het Nederlands of het Engels, consistent binnen één PR.
- Eén logische wijziging per commit.
- Vermeld geen AI-co-auteurs in commit-messages, PR-beschrijvingen of release-notes.
- Hooks niet skippen (`--no-verify` is uit den boze tenzij Anne erom vraagt).

## Verwijderen

Records worden nooit verwijderd. Bij opheffing of einde mandaat zet je `valid_until` of `end_date`. Historie blijft.

## Twee-bron-regel

Nieuwe benoemingen en mandaatwijzigingen vereisen twee onafhankelijke bronnen. Eén bron is genoeg voor een correctie van een tikfout, een spelfout of een ontbrekende identifier.

## Contact

Vragen die niet in een issue passen: anne.schuth@gmail.com.
