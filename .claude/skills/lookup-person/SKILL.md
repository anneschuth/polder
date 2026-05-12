---
name: lookup-person
description: Resolve een persoon-proposal die de code-only resolver niet kon matchen. Drie modes uit `polder resolve`: `no_match` (familie onbekend), `ambiguous_family` (meerdere kandidaten zelfde familienaam), `year_fill` (match maar birth_year ontbreekt). Gebruik wanneer de gebruiker zegt 'lookup persoon', 'vind persoon', of een resolved-proposal aanlevert met `resolution_confidence.person < 0.85`.
version: 0.3.0
triggers:
  - lookup persoon
  - vind persoon polder
  - enrich proposal persoon
  - resolve no-match person
  - disambigueer persoon
---

# lookup-person

## Doel

`polder resolve --enrich-llm` voedt jou per onresolvable proposal een
JSON-payload met:

- de proposal (naam, rol, organisatie, evidence_snippet, bron-URL)
- `candidates`: bestaande person-records die op familienaam of initialen
  lijken te matchen (uit `data/personen/`). Hun mandaten, birth,
  identifiers staan erbij. Genoeg om in 90% van de gevallen direct te
  beslissen.
- `wikidata_candidates`: top-5 Wikidata-hits op familienaam (alleen voor
  `no_match`-mode), met qid, label, birth_year, description.

Je hebt **Bash, WebFetch en WebSearch beschikbaar**, maar gebruik ze
alleen als de payload écht onvoldoende is om te beslissen. Elke tool-call
kost geld en tijd. Standaard: lees de payload, beslis, geef JSON terug.

Beschikbare CLI-tools wanneer je tools nodig hebt:

- `uv run polder show <id>` voor een bestaand record als de payload-trim
  een veld mist.
- `uv run polder lookup wikidata --name "<x>" [--role <x>] [--org <x>]`
  voor een Wikidata-lookup buiten `no_match`-mode (waar hij al gedaan is).
- `WebFetch` voor Wikipedia, rijksoverheid.nl, officielebekendmakingen,
  ABD-site. Geen LinkedIn of sociale media.

Output: precies één JSON-object in een ```json``` fence aan het eind.
Geen "Let me fetch" zonder de fetch ook echt uit te voeren.

## Input-schema

```json
{
  "mode": "no_match" | "ambiguous_family" | "year_fill",
  "proposal": {
    "person_name": "Esther van Deursen",
    "role": "directeur Toezicht mbo bij de Inspectie van het Onderwijs",
    "organization_id": "org:inspectie-onderwijs-min-ocw",
    "organization_chain": [...],
    "start_date": "2018-03-12",
    "abd_nieuws_url": "https://...",
    "staatscourant_url": null,
    "evidence_snippet": "..."
  },
  "candidates": [
    {
      "id": "person:bakker-m-7766715",
      "name": {"full": "Margriet Bakker", "family": "Bakker", "given": "Margriet", "initials": "M."},
      "birth": {"year": 1965},
      "identifiers": {"wikidata": "Q...", "tk_persoon_id": "..."},
      "mandaten": [{"post_id": "...", "organization_id": "...", "role": "...", "start_date": "...", "end_date": "..."}]
    }
  ],
  "wikidata_candidates": [
    {"qid": "Q102345678", "label": "Esther van Deursen", "birth_year": 1972, "description": "..."}
  ]
}
```

## Output-schema (verplicht)

```json
{
  "outcome": "matched_existing" | "create_new" | "no_match",
  "chosen_person_id": "person:bakker-m-7766715",
  "new_person": {
    "name": {"family": "Deursen", "given": "Esther", "tussenvoegsel": "van"},
    "birth_year": 1972,
    "wikidata_qid": "Q102345678"
  },
  "confidence": 0.92,
  "confidence_reasoning": "...",
  "evidence_snippet": "...",
  "evidence_source_url": "https://..."
}
```

- `outcome: matched_existing` → vul `chosen_person_id`. `new_person` = null.
- `outcome: create_new` → vul `new_person` met een **geldige `birth_year`**
  (int 1700-huidig jaar). Een create_new zonder birth_year is **geen
  valide outcome** — de slug-conventie person:<family>-<initials>-<year>
  vereist een jaar. Zonder birth_year: gebruik `no_match`.
- `outcome: no_match` → beide null. Vul wel `confidence_reasoning`.

`evidence_snippet` + `evidence_source_url` zijn verplicht bij confidence
≥ 0.85; ze worden caller-side gevalideerd (quote-or-die: snippet moet
letterlijk in de URL-inhoud voorkomen).

## Beslis-policy per mode

### Mode 1: `no_match`

Familienaam komt niet in `data/personen/`. `candidates` is daarom leeg.
`wikidata_candidates` heeft mogelijk hits.

Voor elke Wikidata-kandidaat:

1. **Naam-match**: family-naam in `label` én voornaam of initiaal-prefix
   in `label`. Beide vereist; alleen family is niet genoeg.
2. **Leeftijd plausibel**: 18 ≤ (huidig_jaar − birth_year) ≤ 80 op
   `start_date`. Buiten die range: andere persoon (homoniem).
3. **Rol/org-fit**: noemt `description` de organisatie, het ministerie,
   of een vergelijkbare functie? Geen verplichting maar wel een booster.

Als precies één kandidaat alle drie haalt: `create_new` met `birth_year`
en `wikidata_qid` overgenomen, confidence 0.92-0.96. Anders `no_match`
met confidence 0.0.

### Mode 2: `ambiguous_family`

Meerdere bestaande records met dezelfde familienaam. `candidates` is
gevuld met hun records inclusief mandaten.

Voor elke kandidaat:

1. **Naam-match**: voornaam of initialen van de proposal komen overeen
   met `candidate.name.given` of `candidate.name.initials`. Geen
   voornaam-match → andere persoon.
2. **Organisatie-fit**: heeft de kandidaat al een mandaat in
   `proposal.organization_id` of een gerelateerde organisatie? Sterk
   signaal (confidence 0.95+).
3. **Tijd-overlap**: kandidaat had / heeft een mandaat dat tijdsmatig
   logisch overgaat in de proposal (eindigt vóór `start_date`, of loopt
   parallel). Sterk signaal.

Eén duidelijke winnaar: `matched_existing` met confidence 0.92-0.97.
Twee kandidaten allebei plausibel: `no_match` (manual review). Geen
kandidaat past: `no_match`; mogelijk `create_new` als
`wikidata_candidates` een unieke hit oplevert.

### Mode 3: `year_fill`

`candidates` heeft één entry: de partial-match kandidaat. Birth_year is
mogelijk al gevuld (kijk naar `candidate.birth.year` én naar het slug-
nummer in `candidate.id`). Doel: bevestigen dat het dezelfde persoon is
en eventueel birth_year aanvullen.

1. Als `candidate.birth.year` aanwezig is **en** initialen/voornaam
   matchen met de proposal: `matched_existing` met confidence 0.95+.
2. Als initialen/voornaam mismatchen: `no_match` (andere persoon met
   dezelfde familienaam).
3. Als birth_year ontbreekt en geen externe bron in payload zou
   ophelderen: `matched_existing` met de kandidaat en confidence 0.88
   (best-effort), of `no_match` als de match zwak is.

## Harde regels

1. **Quote-or-die op birth_year.** `evidence_snippet` moet een letterlijke
   substring zijn van de inhoud op `evidence_source_url`. Hij wordt
   caller-side geverifieerd tegen een allowlist (Wikidata, Wikipedia,
   rijksoverheid, officielebekendmakingen).
2. **Geen geboortemaand/-dag.** Alleen `birth_year`.
3. **Bij twijfel: `no_match`.** Een false-positive merge kost veel meer
   dan een gemiste match.
4. **AVG-grens**: geen prive-adres, geen BSN, geen sociale-media-bronnen.
5. **Pure JSON-output aan het eind.** Eén JSON-object in een ```json```
   fence. Eventuele tool-calls daarvoor zijn prima.
6. **Tool-frugaal.** Bij elke tool-call vraag je je af: had ik dit ook
   kunnen beslissen op alleen de payload? Zo ja: skip de tool.

## Voorbeeld

Zie `example_input.json` en `example_output.json`.

## Status

Actief, versie 0.2.0. Single-turn na bevinding dat tools niet uitvoerbaar
zijn binnen de stream-json sessie.
