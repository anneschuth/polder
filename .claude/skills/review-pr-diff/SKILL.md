---
name: review-pr-diff
description: Genereer Nederlandstalige PR-summary uit diff.json, gegroepeerd per organisatie, met confidence-flags. Gebruik wanneer de gebruiker zegt 'review diff', 'maak PR-body', 'samenvatting diff', 'genereer PR-summary', or in English 'review diff', 'generate PR body', 'summarise daily run', of in CI na een dagelijkse fetcher-run.
version: 0.1.0
---

# review-pr-diff

## Doel

Lees een `diff.json` van een dagelijkse fetcher-run en produceer een Nederlandstalige PR-body. Per organisatie een sectie met de wijzigingen, plus een aparte sectie met flags voor lage confidence en velden op rood-niveau.

## Input

- Pad naar `diff.json` met alle wijzigingen van een run. Elke wijziging heeft minstens `organization_id`, `change_type` (add, update, remove), `entity` (person, post, membership), `confidence`, `field_path`, `evidence_snippet`, `bron_url`.

## Output

Markdown-string die direct als PR-body bruikbaar is. Twee verplichte secties:

- `## Wijzigingen vandaag` met sub-secties per organisatie.
- `## Vlaggen` met een tabel van wijzigingen die expliciete review nodig hebben.

## Harde regels

1. Niet zelf data wijzigen. Alleen samenvatten wat in `diff.json` staat.
2. Wijzigingen met `confidence` onder 0.95 of velden die in de AVG-sectie van `.claude/CLAUDE.md` als rood gemarkeerd staan komen verplicht in de Vlaggen-tabel.
3. Schrijf in het Nederlands. Korte zinnen, varieer ritme, geen em-dashes.
4. Per wijziging een directe link naar `bron_url` indien aanwezig.

## Voorbeeld output

```markdown
## Wijzigingen vandaag

### Ministerie van BZK
- Nieuwe benoeming: P. de Vries als directeur Bestuur en Organisatie per 1 juni 2026. Bron: Stcrt. 2026, 12345.
- Vertrek: J. Bakker, einde lidmaatschap raad van bestuur per 31 mei 2026.

### Ministerie van Financien
- Wijziging post-omschrijving op `post:fin-cio`.

## Vlaggen

| Organisatie | Wijziging | Confidence | Reden |
|---|---|---|---|
| BZK | Nieuwe benoeming P. de Vries | 0.91 | Onder drempel 0.95 |
| FIN | Geboortedatum-update J. Smit | 0.97 | Veld op rood-niveau (AVG) |
```

## Status

Actief. Eerste skill die in CI draait.
