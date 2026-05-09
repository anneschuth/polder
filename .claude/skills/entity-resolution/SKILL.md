---
name: entity-resolution
description: Match een persoonsverwijzing aan een bestaande person:* slug, of stel een nieuwe slug voor via family-name, initialen, geboortejaar en context. Gebruik wanneer de gebruiker zegt 'match persoon', 'resolve naam', 'is X dezelfde als Y', 'koppel persoon aan slug', 'entity merge', of in English 'resolve person', 'match name to slug', 'is X the same as Y', 'entity resolution'.
version: 0.2.0
triggers:
  - match persoon
  - resolve naam
  - koppel persoon aan slug
  - entity merge
  - resolve person
  - match name to slug
  - entity resolution
---

# entity-resolution

## Doel

Match een nieuwe persoonsverwijzing (zoals "dr. ir. J.P. Jansen") aan een bestaande `person:*` slug uit `data/personen/`, of stel een nieuwe slug voor wanneer geen kandidaat goed past. Voorkomt dubbele records en stabiliseert slug-toekenning over fetchers en KB-parses heen.

## Input

JSON met:

- `name`: string ("dr. ir. J.P. Jansen") of object `{family, given, initials, honorifics_pre[]}`.
- `context` (optioneel): `{organization_id, role, date}` voor disambiguatie.
- `candidates`: array van bestaande person-records die de caller al heeft voorgeladen uit `data/personen/`. Elk record heeft minstens `id`, `name.family`, `name.initials`, en optioneel `birth.year` plus `mandaten[]`.

De caller filtert candidates op een redelijk venster (bijvoorbeeld zelfde family-name of binnen 5 jaar van een bekende geboortedatum). Deze skill werkt strikt op de aangeleverde lijst en hallucineert geen extra kandidaten.

## Output

JSON met:

- `matched_id` (string of null): bestaande slug bij goede match, anders null.
- `proposed_id` (string of null): nieuwe slug `person:<family>-<initials-lower>-<birthyear>` als geen match.
- `confidence` (float 0 tot 1).
- `reasoning` (string): tekstuele uitleg per signaal.
- `alternative_candidates` (array): andere mogelijkheden bij confidence < 0.95, elk met `id`, `score` en `reason`.

## Stappen voor de LLM

1. Normaliseer de input-naam: extract `family`, `initials` en optioneel `given`. Strip honorifics (`dr.`, `ir.`, `mr.`, `drs.`). Houd tussenvoegsels apart van de family-stam.
2. Voor elke candidate, bereken vier deelscores:
   - **Family-match**: exact (1.0), edit-distance kleiner dan of gelijk aan 2 (0.8), tussenvoegsel-tolerant gelijk (0.9), anders 0.0.
   - **Initials-match**: exact (1.0), substring-relatie tussen input en candidate (0.7), anders 0.0.
   - **Birthyear-match**: input bevat birthyear en exact (1.0), verschil binnen 2 jaar (0.8), input zonder birthyear (0.5), conflict groter dan 2 jaar (0.0).
   - **Context-boost**: organisatie of datum overlapt met een mandaat van de candidate, telt als +0.1 bovenop de gewogen som.
3. Bereken final confidence als gewogen som plus context-boost: `family * 0.5 + initials * 0.25 + birth * 0.2 + context * 0.05 + boost`. Cap op 1.0.
4. Bij meerdere kandidaten boven 0.7: zet final confidence terug op 0.5, geef ALLE kandidaten boven 0.7 mee als `alternative_candidates`, en laat een mens beslissen.
5. Bij geen match (alle kandidaten onder 0.7): bouw `proposed_id` volgens conventie `person:<family>-<initials-lower>-<birthyear>`. Lowercase ASCII, hyphens, transliteer accenten, strip tussenvoegsels uit de slug-stam (zelfde regels als `slugify_person` in `src/polder/fetchers/tk_odata.py`). Zonder bekende birthyear: gebruik `unknown` als jaartal-segment en flag dit in `reasoning`.

## Harde regels

1. **Confidence per claim** als float in [0, 1] met expliciete `reasoning` waarin de deelscores benoemd staan.
2. Geen birthyear bekend: `confidence` blijft kleiner dan of gelijk aan 0.85, ook bij verder perfecte match. Forceert een tweede check.
3. **Diff-only mode.** Werk alleen met de lijst uit `candidates[]`. Nooit "vul aan naar beste vermogen" met extern geheugen. De caller is verantwoordelijk voor de pre-filter.
4. Bij twee of meer kandidaten boven 0.7: forceer review met `confidence = 0.5` en vul `alternative_candidates` volledig.
5. Slug-conventie identiek aan `slugify_person`: tussenvoegsels uit de stam, ASCII, lowercase, hyphens.

## Voorbeeld

Zie `example_input.json` en `example_output.json`. Input is een KB-fragment met "dr. J.P. Jansen" en twee Jansen-kandidaten. Output kiest de candidate met matchende initialen, en levert de andere Jansen als alternative.

## Aanroep

```yaml
prompt: |
  Bepaal of de persoon in deze KB-tekst overeenkomt met een bestaande
  polder-record. Gebruik entity-resolution skill met de candidates
  uit data/personen/ die binnen 5 jaar van de geboortedatum vallen.
```

## Aanroep vanuit Claude Code CLI

```bash
claude "Match deze persoonsverwijzing aan data/personen/ via de entity-resolution skill: dr. J.P. Jansen, BZK, 2022-09-01"
```

## Status

Actief, versie 0.2.0.
