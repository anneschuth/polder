---
name: parse-organogram
description: Vision-analyse op organogram PDF of PNG, extract organisatiehierarchie en bemenste posten. Gebruik wanneer de gebruiker een organogram-afbeelding aanlevert of zegt 'parse organogram', 'extract organisatiestructuur', 'lees organogram', 'organogram naar JSON', or in English 'parse org chart', 'extract organisation structure', 'read organogram'.
version: 0.1.0
---

# parse-organogram

## Doel

Voer vision-analyse uit op een PDF of PNG van een organogram en extract de organisatiehierarchie (parent naar child) plus, waar zichtbaar, de personen op de posten.

## Input

- Pad naar PNG, JPG of PDF van een organogram.
- Optioneel: ministerie-slug en datum, indien niet uit metadata afleidbaar.

## Output

JSON-array met proposals. Twee soorten:

Orgaanstructuur:

```json
{
  "type": "org_structure",
  "parent_id": "org:bzk",
  "child_id": "org:bzk-dg-bestuur",
  "child_name": "DG Bestuur en Wonen",
  "bron_pagina_nummer": 1,
  "bron_url": "...",
  "confidence": 0.92
}
```

Persoon-op-post:

```json
{
  "type": "person_post",
  "person_name": "P. de Vries",
  "post_id": "post:bzk-dg-bestuur-wonen",
  "bron_pagina_nummer": 1,
  "bron_url": "...",
  "confidence": 0.78
}
```

## Harde regels

1. Output ALTIJD met `bron_pagina_nummer` en `bron_url`. Geen bron, geen proposal.
2. Personen-extracties uit vision krijgen automatisch `confidence` van maximaal 0.85. Vision is foutgevoelig op kleine tekst.
3. Schrijf naar `data/_staging/organogram-{ministerie}-{datum}.json`. Niet direct in `data/`.
4. Bij onleesbare delen: laat het proposal weg en log een waarschuwing met pagina-nummer en regio. Geen gokken.
5. Wijs `classification` toe op basis van titel in het organogram, gebruik de ABD-niveaus uit `schemas/post.schema.json`:
   - SG, DG, plv-SG, IG → `abd-tmg` (groen)
   - directeur, plv-directeur, programmadirecteur → `abd-directeur` (geel)
   - afdelingshoofd, MT-lid, clusterhoofd → `abd-afdelingshoofd` (geel)
   - projectleider, kwartiermaker → `abd-projectleider` (geel)
   Bij onduidelijke titel: laat `classification` weg en flag voor menselijke review.
6. AVG-niveau van de extracted post bepaalt of de proposal naar `_staging/` mag. Rood-niveau (beleidsmedewerkers etc) NOOIT extracten, ook niet als ze in het organogram staan.

## Voorbeeld

Input: `organogram-bzk-2026-04.pdf` met op pagina 1 een blok "DG Bestuur en Wonen, P. de Vries".

Output: twee proposals, één van type `org_structure` (parent BZK, child DG Bestuur en Wonen) en één van type `person_post` met `confidence` 0.78.

## Status

Stub.
