---
name: lookup-person
description: Resolve een persoon-proposal die de code-only resolver niet kon matchen. Bedoeld voor de drie buckets uit `polder resolve` waar persoon-resolution faalt: `no_match` (familie niet in data/), `ambiguous_family` (meerdere kandidaten zelfde familienaam), en `family_initials_no_year` (match maar geen birth_year). Gebruik wanneer de gebruiker zegt 'lookup persoon', 'vind persoon', 'enrich proposal persoon', of een resolved-proposal aanlevert met `resolution_confidence.person < 0.85`.
version: 0.1.0
triggers:
  - lookup persoon
  - vind persoon polder
  - enrich proposal persoon
  - resolve no-match person
  - disambigueer persoon
---

# lookup-person

## Doel

De code-only resolver in `polder resolve` lost ~60% van persoon-matches op via
deterministische family+initials+birth_year-matching. Voor de rest blijft één
van drie problemen over:

1. **`no_match`** — familienaam komt niet voor in `data/personen/`. Persoon is
   nieuw voor polder; we moeten een birth_year vinden om een stabiele slug
   `person:<family>-<initials>-<year>` te kunnen maken.
2. **`ambiguous_family`** of `family_unique` met meerdere kandidaten — meerdere
   bestaande records hebben dezelfde familienaam (Bakker, Jansen, van Dam). We
   moeten kiezen of zeggen "geen van deze, nieuwe persoon".
3. **`family_initials_no_year`** — match op family+initials is sterk, maar
   birth_year ontbreekt zodat de match niet boven 0.95 confidence komt.

Deze skill resolved één proposal-record per call. Output is een patch die
`polder resolve --enrich-llm` terug in het `.resolved.json`-bestand merget.

## Input

JSON-payload met:

```json
{
  "mode": "no_match" | "ambiguous_family" | "year_fill",
  "proposal": {
    "person_name": "Esther van Deursen",
    "role": "directeur Toezicht mbo bij de Inspectie van het Onderwijs",
    "organization_id": "org:inspectie-onderwijs-min-ocw",
    "organization_chain": [...],
    "abd_nieuws_url": "https://...",
    "staatscourant_url": null,
    "evidence_snippet": "Esther Deursen wordt met ingang van 12 maart 2018 directeur..."
  },
  "candidates": [   // alleen voor ambiguous_family + year_fill
    {"id": "person:bakker-m-7766715", "name": {"full": "Margriet Bakker", ...}, "birth": {"year": 1965}, "mandaten": [...]},
    {"id": "person:bakker-w-6188553", "name": {"full": "Wim Bakker", ...}, "birth": null, "mandaten": [...]}
  ]
}
```

## Output

JSON met de patch voor `.resolved.json`:

```json
{
  "outcome": "matched_existing" | "create_new" | "no_match",
  "chosen_person_id": "person:bakker-m-7766715",   // null als create_new of no_match
  "new_person": {                                   // alleen bij create_new
    "name": {"family": "Deursen", "given": "Esther", "tussenvoegsel": "van"},
    "birth_year": 1972,
    "wikidata_qid": "Q12345678"   // optioneel
  },
  "confidence": 0.92,
  "confidence_reasoning": "Wikidata Q12345678 'Esther van Deursen' heeft P39 'directeur bij Inspectie van het Onderwijs' tussen 2018-2024, matched op rol én organisatie. Familienaam, voornaam en jaartal komen overeen.",
  "evidence_snippet": "Esther van Deursen (geboren 1972) is sinds 2018 directeur Toezicht mbo bij de Inspectie van het Onderwijs",
  "evidence_source_url": "https://nl.wikipedia.org/wiki/Esther_van_Deursen"
}
```

## Stappen voor de LLM

### Mode 1: `no_match` — onbekende familie

1. **Wikidata SPARQL eerst, gratis en snel.** Roep aan:

   ```bash
   uv run polder lookup wikidata --name "Esther van Deursen" --role "directeur Toezicht mbo" --org "OCW"
   ```

   Output is JSON met kandidaten `{qid, label, birth_year, description}`.

2. **Filter kandidaten op context-match.** Voor elke kandidaat met `birth_year`
   plausibel (18-80 jaar oud op datum van het mandaat) én een `description` of
   `label` die de organisatie/rol noemt, accepteer met `confidence ≥ 0.90`.

3. **Bij meerdere plausibele Wikidata-kandidaten** of bij `birth_year: null`:
   ga naar Web-fetch.

4. **Web-search voor de naam + organisatie**:

   ```
   WebSearch: "Esther van Deursen" Inspectie Onderwijs directeur
   ```

   Open de relevante hits (ABD-site, rijksoverheid.nl, Wikipedia, professionele
   bio's). **Niet** LinkedIn-profielen openen (paywall, privacy-bezwaren).

5. **Quote-or-die op birth_year**. De `evidence_snippet` moet een letterlijke
   substring zijn van de inhoud van `evidence_source_url` die de geboortejaar
   expliciet noemt of waar de leeftijd staat plus een datum waarop het
   geschreven is.

6. **Output `create_new` met `confidence ≥ 0.90`** als zowel Wikidata of een
   andere autoritatieve bron een birth_year geeft. Anders `no_match` met
   `confidence: 0.0`.

### Mode 2: `ambiguous_family` — kies of zeg "geen"

1. **Roep `polder show person:<id>` voor elke kandidaat** om de mandaten te
   zien:

   ```bash
   uv run polder show person:bakker-m-7766715 --history
   uv run polder show person:bakker-w-6188553 --history
   ```

2. **Match-criteria, in volgorde van kracht:**
   - Bestaand mandaat op `organization_id` of een zuster-org → zeer waarschijnlijk dezelfde persoon (confidence 0.95).
   - Mandaat op een vergelijkbare rol-classificatie (allemaal "directeur" in
     ministeries) plus voornaam-match in `name.given` of `name.full` → confidence 0.90.
   - Alleen familienaam matched, geen verdere context → confidence 0.50, output `no_match`.

3. **Bij twijfel: roep Wikidata aan** (zoals Mode 1 stap 1) om de proposal-naam
   te verifiëren tegen wat Wikidata zegt over deze rol+organisatie. Als
   Wikidata een andere geboortedatum heeft dan een kandidaat → andere persoon.

4. **Output `matched_existing` met `chosen_person_id`** als één kandidaat
   sterk wint. Anders `create_new` (als Wikidata een birth_year geeft) of
   `no_match` (als alles onduidelijk blijft).

### Mode 3: `year_fill` — birth_year aanvullen

1. **`polder show person:<id> --history`** voor de partial-match kandidaat.
2. **`polder lookup wikidata --name "<name>"`**, kruis op de identifiers van
   de kandidaat (TK-id, Wikidata-qid in `identifiers`-blok) als die er zijn.
3. **Web-fetch** voor een autoritatieve bron met geboortejaar of voldoende
   leeftijdscontext.
4. Output: `matched_existing` met `chosen_person_id = <id>` en `new_person.birth_year`
   ingevuld zodat resolve het kan terugschrijven naar het bestaande record.

## Beschikbare tools voor de LLM

- **Bash** voor `polder` CLI-calls:
  - `polder show <id> --history --format json` — bestaande persoon-context
  - `polder search "<query>" --type person --limit 10` — ripgrep over data/personen/
  - `polder lookup wikidata --name "<x>" [--role <x>] [--org <x>]` — Wikidata-SPARQL met fallback
- **WebFetch** voor Wikipedia, rijksoverheid.nl, ABD-website, gemeente-sites.
- **WebSearch** voor de eerste hits op de naam.

Geen LinkedIn. Geen sociale media. Geen privé-bronnen. Geen paywall-content.

## Harde regels

1. **Quote-or-die op `birth_year`.** Je geeft alleen een birth_year terug als
   je een `evidence_snippet` hebt dat een letterlijke substring is van
   `evidence_source_url`. Geen paraphrase, geen "afgeleid uit context".
2. **Geen geboortedag of -maand.** Alleen `birth_year`. Schema enforced.
3. **Confidence eerlijk.** `confidence ≥ 0.95` betekent: "ik durf hier op te
   gokken dat dit auto-merge-kwaliteit is". Lager = `needs-review`.
4. **Bij twijfel: `no_match`.** Niet gokken. Een no_match is veel minder kostbaar
   dan een verkeerde merge.
5. **Bij privacy-grens (AVG-rood, BSN, prive-adres in een bron): direct
   afbreken**, output `no_match` met reason `"avg_block"`.
6. **Output alleen geldige JSON.** Geen extra tekst, geen markdown-fences, één
   JSON-object per call.

## Voorbeeld

Zie `example_input.json` en `example_output.json`.

## Status

Actief, versie 0.1.0. Eerste gebruiker is `polder resolve --enrich-llm`.
