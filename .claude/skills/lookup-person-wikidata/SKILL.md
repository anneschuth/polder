---
name: lookup-person-wikidata
description: Zoek een persoon in Wikidata op naam, retourneer kandidaten met geboortejaar en Q-id. Gebruik bij ambigue persoonsverwijzingen voordat een polder-record wordt aangemaakt, zodat we waar mogelijk een echt geboortejaar in de slug krijgen in plaats van een UUID-fallback.
version: 0.1.0
triggers:
  - lookup persoon wikidata
  - zoek geboortejaar wikidata
  - persoon wikidata kandidaat
  - find person wikidata
  - resolve birthyear wikidata
---

# lookup-person-wikidata

## Doel

Vóór het aanmaken van een nieuw `person:*` record proberen we via Wikidata een
geboortejaar en Q-id te vinden. Lukt dat, dan krijgt de persoon een stabiele
slug `<family>-<initials>-<jaar>`. Lukt het niet, dan valt de caller terug op
een UUID-suffix (zie `slugify_person(..., fallback_uuid=...)`).

Deze skill doet de Wikidata-lookup en levert kandidaten met scores. De skill
schrijft nooit naar `data/personen/`, alleen naar `data/_staging/`.

## Input

JSON met:

- `name`: object `{family, given?, initials?}` of een platte string
  (`"Mark Rutte"`).
- `context` (optioneel): `{organization, role, date}` voor disambiguatie. Bij
  bijvoorbeeld "BZK directie Digitale Samenleving" check je P108 (employer) of
  P39 (position held) van een kandidaat tegen die organisatie.

## Output

JSON met:

- `input`: echo van de genormaliseerde input.
- `candidates`: lijst van `{qid, label, birth_year, description, score, recommended}`.
- `recommended_qid`: het qid van de beste match als de top-score boven 0.85 ligt,
  anders `null`.

`birth_year` is `null` als Wikidata geen P569 heeft voor die persoon. In dat
geval **geen geboortejaar verzinnen**; laat `null` staan.

## Stappen voor de LLM

1. Roep `polder.fetchers.wikidata_sparql.lookup_person_by_name(family,
   initials=..., given=..., endpoint="qlever", cache_dir=Path("_cache/wikidata-personen"))`.
   Cache wordt onder `_cache/wikidata-personen/<query-hash>.json` gehasht; bij een
   tweede call met dezelfde input is er geen netwerkverkeer.
2. Voor elke kandidaat, scoor:
   - **Naam-overeenkomst**: family-naam exact in `label` (1.0), edit-distance
     ≤ 2 (0.7), anders 0.0. Bij meegegeven `given`: tel +0.2 als de given-naam
     ook in het label staat. Cap op 1.0.
   - **Context-fit**: als `context.organization` is meegegeven, check of de
     `description` of een andere Wikidata-property (bv. P108 employer) die
     organisatie noemt. Als ja: +0.1.
3. Markeer de kandidaat met de hoogste score als `recommended: true` mits
   `score > 0.85`. Anders blijft `recommended: false` voor alle kandidaten en
   `recommended_qid: null`.
4. Schrijf het resultaat naar `data/_staging/lookup-<name-slug>.json` (de
   CLI-wrapper `polder skill lookup-person` doet dit al).

## Harde regels

1. **Lege lijst bij geen match.** Nooit een vermoeden uitspugen. Als Wikidata
   geen kandidaten geeft: `candidates: []` en `recommended_qid: null`.
2. **Schrijf alleen naar `data/_staging/`.** Nooit direct naar `data/personen/`.
3. **Geen geboortejaar verzinnen.** `birth_year: null` blijft `null`.
4. **Cache idempotent.** Twee runs met identieke input geven identieke output.
5. **Confidence per kandidaat** als float in [0, 1] in het `score`-veld.
   Lage score (< 0.85) → handmatige review.

## Aanroep vanuit CLI

```bash
uv run polder skill lookup-person "Mark Rutte"
uv run polder skill lookup-person "Suzie Kewal" --organization "BZK directie Digitale Samenleving"
```

## Voorbeeld

Zie `example_input.json` en `example_output.json`.

## Status

Actief, versie 0.1.0.
