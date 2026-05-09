---
name: entity-resolution
description: Match een persoonsverwijzing aan een bestaande person:* slug, of stel een nieuwe slug voor via embedding-similarity, naam-distance en geboortejaar. Gebruik wanneer de gebruiker zegt 'match persoon', 'resolve naam', 'is X dezelfde als Y', 'koppel persoon aan slug', 'entity merge', or in English 'resolve person', 'match name to slug', 'is X the same as Y', 'entity resolution'.
version: 0.1.0
---

# entity-resolution

## Doel

Match een nieuwe persoonsverwijzing (zoals "dr. ir. J.P. Jansen") aan een bestaande `person:*` slug, of stel een nieuwe slug voor wanneer geen kandidaat goed past. Voorkomt dubbele personen in `data/personen/`.

## Input

- `name` (string): de ruwe persoonsverwijzing.
- `context` (object, optioneel): organisatie, datum, post, bron-URL.
- `kandidaten` (array): personen uit `data/personen/` met velden id, full_name, initials, family_name, birth_year, organisatie-historie.

## Output

Bij match:

```json
{ "matched_id": "person:jansen-jp-1965", "confidence": 0.93, "reasoning": "..." }
```

Geen match:

```json
{ "matched_id": null, "proposed_id": "person:jansen-jp-1965", "reasoning": "..." }
```

Meerdere kandidaten boven 0.7:

```json
{ "matched_id": null, "kandidaten": [...], "confidence": 0.5, "reasoning": "Twee kandidaten boven drempel, review nodig." }
```

## Harde regels

1. Combineer drie signalen: embedding-similarity op volledige naam-plus-context, naam-distance (Levenshtein op family_name plus initialen-match), en geboortejaar-overlap. Niet alleen name-match.
2. Bij meerdere kandidaten boven 0.7 confidence: output ALLE kandidaten en forceer final confidence op 0.5, zodat een mens beslist.
3. Slug-conventie voor `proposed_id`: `person:<family-name>-<initialen>-<geboortejaar>`, lowercase, hyphens, ASCII (transliteer accenten).
4. Geen geboortejaar bekend: zet `<geboortejaar>` op `unknown` en flag in reasoning.

## Voorbeeld

Input: `name: "dr. ir. J.P. Jansen"`, context organisatie BZK, kandidaten met één hit "person:jansen-jp-1965" (full_name "Jan Pieter Jansen", birth_year 1965).

Output: `{ "matched_id": "person:jansen-jp-1965", "confidence": 0.93, "reasoning": "Initialen J.P. matchen, family_name identiek, geboortejaar bekend en consistent met BZK-loopbaan sinds 2008." }`

## Status

Stub.
