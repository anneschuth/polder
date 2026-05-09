# Secrets

Polder draait op een minimale set secrets. Alleen `ANTHROPIC_API_KEY` is nu actief in gebruik; de rest is roadmap.

## ANTHROPIC_API_KEY

Vereist voor de `claude-review` job in `.github/workflows/daily-update.yml`. Deze job laat `claude-code-action` een Nederlandstalige PR-body genereren op basis van `diff.json` en `proposals.json`, en post die als comment op de daily-update PR.

Anne zet de secret zelf:

```bash
gh secret set ANTHROPIC_API_KEY --repo anneschuth/polder
```

Het commando vraagt interactief om de waarde, dus de key komt niet in shell-history of transcript.

Zonder secret skipt de job (zie de `if: secrets.ANTHROPIC_API_KEY != ''` guard). Geen failure, geen falsalarm; de PR krijgt simpelweg geen Claude-comment en blijft wachten op handmatige review.

### Geschat verbruik

- Routine: $30 tot $50 per maand bij dagelijkse runs met haiku-4-5 als reviewmodel.
- Actieve periodes (verkiezingen, kabinetsformatie, grote benoemingsrondes): $75 tot $150 per maand. Diff-volume groeit en `proposals.json` uit de Staatscourant-pipeline genereert grotere PR-bodies.
- Eenmalig bij bulk-import van een nieuwe bron (EK-scrape, ABD, Wikidata-orgs in een keer): $200 tot $400 voor de eerste run.

Zet een budget-cap in de Anthropic-console voor een maand-plafond.

## Toekomstige secrets

- `LOGIUS_OIN_API_KEY`: niet nodig zolang de publieke proxy op `oinregister.logius.nl` voldoet. Pas zetten als rate-limits structureel te krap worden.
- `DATASETTE_CLOUD_TOKEN`: alleen als Polder naar Datasette Cloud (Starter, $9 per maand) gaat. Zie `docs/deploy-datasette.md`. Zonder token publiceert `publish.yml` alleen naar GitHub release-assets.
- `KOOP_SRU_API_KEY`: alleen relevant als KOOP de anonieme SRU-toegang afsluit. Vooralsnog open.

## Auditeren

```bash
gh secret list --repo anneschuth/polder
```

Geeft de namen, niet de waarden. Rotate na vermoede lek door de oude key in de Anthropic-console te revoken en opnieuw `gh secret set` te draaien.
