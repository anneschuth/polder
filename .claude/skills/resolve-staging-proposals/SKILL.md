---
name: resolve-staging-proposals
description: Match staging-proposals (uit parse-abd-nieuws, parse-staatscourant, parse-organogram) aan bestaande organisatie-, post- en persoon-records in `data/`. Verrijkt elke entry met `resolved_organization_id`, `resolved_post_id`, `resolved_person_id`, per-veld confidence en een `merge_recommendation`. Gebruik wanneer de gebruiker zegt 'resolve staging', 'match staging-proposals', 'koppel staging aan data', of in English 'resolve staging proposals', 'match staging records', 'reconcile staging file'.
version: 0.1.0
triggers:
  - resolve staging
  - match staging-proposals
  - koppel staging aan data
  - resolve staging proposals
  - match staging records
  - reconcile staging file
---

# resolve-staging-proposals

## Doel

Een staging-bestand uit `data/_staging/*.json` (output van `parse-abd-nieuws`, `parse-staatscourant` of `parse-organogram`) wordt gematcht tegen bestaande records in `data/organisaties/`, `data/posten/` en `data/personen/`. De skill verrijkt elke entry met resolved-IDs en confidence, en zegt per record of een mens nodig is. De skill schrijft nooit naar de canonical data-mappen.

## Input

Pad naar één staging-file. Voorbeelden:

- `data/_staging/abd-nieuws-2026-04.json`
- `data/_staging/staatscourant-2024-07.json`
- `data/_staging/organogram-min-bzk-2026-05-09.json`

De skill detecteert het staging-type uit de filenaam-prefix en uit de aanwezige velden (`organization_chain` voor abd-nieuws v0.3+, `staatscourant_url` plus `decision_reference` voor staatscourant, `type` is `person_post` of `org_structure` voor organogram).

## Output

Pad: `data/_staging/<input-stem>.resolved.json`. Format: JSON-array met dezelfde records als de input, plus per record:

- `resolved_organization_id` (string of null): bestaande slug uit `data/organisaties/` of null.
- `resolved_organization_level` (string of null): `afdeling`, `directie`, `directoraat-generaal`, `ministerie`, of null.
- `resolved_post_id` (string of null): bestaande slug uit `data/posten/`.
- `resolved_person_id` (string of null): bestaande slug uit `data/personen/`.
- `resolution_confidence` (object): `{organization, post, person}` als floats in [0, 1] per veld. Per-veld, niet één globale.
- `resolution_notes` (string): uitleg met evidence (welk veld in welk bestand matchte).
- `propose_post_creation` (bool): true als organisatie geresolved is maar post niet bestaat.
- `merge_recommendation` (string): `auto-merge`, `needs-review` of `skip`.

## Stappen voor de LLM

1. Laad de staging-file. Bepaal staging-type uit de filenaam-prefix. Per record:
2. **Bouw een organization_chain** als de input die niet al heeft. Voor abd-nieuws v0.3+ bestaat het veld; voor oudere abd-nieuws en voor staatscourant- en organogram-records leid je de keten af uit `organization_id` (alleen ministerie-niveau) plus `role`-tekst.
3. **Match diepste niveau eerst.** Loop `organization_chain` van diepst naar breedst:
   - Voor afdeling- of directie- of DG-niveau: zoek in `data/organisaties/organisatieonderdelen/*.yaml` op exacte naam-match (lowercase, accenten genegeerd) of fuzzy-match (Levenshtein ≤ 2) op het `names[].value` veld.
   - Een afdelings-match telt alleen als het matchende onderdeel `parent_id` heeft die overeenkomt met de directie-slug uit de chain. Geen ouder-relatie? Confidence-cap 0.8 met expliciete note.
   - Match op afdeling-niveau zonder ouder-conflict: confidence 0.9 of hoger.
4. **Klim omhoog** als het diepste niveau niet matcht. Probeer directie, dan DG, dan ministerie. De eerste die matcht wordt `resolved_organization_id`, en `resolved_organization_level` is dat niveau. Confidence-cap 0.85 zodra je hoger zit dan het diepste niveau in de chain, met note "afdeling-niveau niet gevonden, gematcht op directie-niveau".
5. **Person-match.** Lees `data/personen/*.yaml`. Match op `name.family` plus `name.initials`. Of een persoon nog actief is volgt uit de mandaten (`end_date is None`), niet uit de folder. Geboortejaar onbekend in de proposal? Confidence-cap 0.85. Meerdere kandidaten passen? Confidence-cap 0.7 en alle kandidaten in `resolution_notes`. Volg de slug-conventie `person:<family>-<initials-lower>-<birthyear>` zoals beschreven in `entity-resolution`.
6. **Post-match.** Bouw de kandidaat-slug `post:<role-slug>-<resolved_organization_id-zonder-org-prefix>`. Bestaat in `data/posten/`? `resolved_post_id` ingevuld, confidence ≥ 0.9. Bestaat niet, maar organisatie wel geresolved? `propose_post_creation: true`, `resolved_post_id: null`, confidence 0.0 met note "post moet handmatig aangemaakt worden".
7. **Merge-recommendation.**
   - `auto-merge`: alle drie de IDs geresolved, alle confidences ≥ 0.95, geen rood-AVG-veld geraakt, geen `propose_post_creation`.
   - `needs-review`: minstens één veld geresolved maar confidence < 0.95, of `propose_post_creation: true`, of de chain matcht niet volledig.
   - `skip`: organisatie niet geresolved op enig niveau, of person niet geresolved en geen jaar bekend.

## Harde regels

1. **Diff-only mode.** Lees `data/`, schrijf alleen naar `data/_staging/<input-stem>.resolved.json`. Nooit naar canonical mappen.
2. **Geen records aanmaken.** Geen nieuwe organisatie-, post- of persoon-yaml. Alleen voorstellen via `propose_post_creation` en de slug-suggesties uit de input.
3. **Quote-or-die voor matches.** Elke geresolvde claim heeft in `resolution_notes` de evidence: bestand-pad plus het veld dat matchte. Voorbeeld: `matched on data/organisaties/organisatieonderdelen/directie-wonen.yaml names[0].value`.
4. **Confidence per veld.** `resolution_confidence` is een object met aparte floats voor `organization`, `post`, `person`. Niet één globale score.
5. **Two-source rule respecteren.** Een staatscourant-only match mag tot 0.95; abd-nieuws-only blijft op 0.85 ook als de skill perfect matcht.
6. **Geen privé-data toevoegen.** Geen geboortemaand of -dag, geen contactgegevens, nooit BSN.

## Voorbeeld

Zie `example_input.json` voor een staging-record uit parse-abd-nieuws v0.3 met een vier-niveau organization_chain, en `example_output.json` voor de geresolvde versie.

## Aanroep

```bash
polder skill resolve-staging data/_staging/abd-nieuws-2026-04.json
# Output: data/_staging/abd-nieuws-2026-04.resolved.json
```

Output landt in `data/_staging/abd-nieuws-2026-04.resolved.json` naast het input-bestand.

## Status

Actief, versie 0.1.0. Vijfde skill in Polder, na review-pr-diff, parse-staatscourant, parse-organogram, parse-abd-nieuws en entity-resolution. Gebruikt door de daily-update workflow vóór de auto-merge stap.
