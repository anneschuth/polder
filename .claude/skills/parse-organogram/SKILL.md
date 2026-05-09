---
name: parse-organogram
description: Vision-analyse op organogram PDF of PNG, extract organisatiehierarchie en bemenste posten. Gebruik wanneer de gebruiker een organogram-afbeelding aanlevert of zegt 'parse organogram', 'extract organisatiestructuur', 'lees organogram', 'organogram naar JSON', or in English 'parse org chart', 'extract organisation structure', 'read organogram'.
version: 0.2.0
triggers:
  - parse organogram
  - extract organisatiestructuur
  - lees organogram
  - organogram naar JSON
  - parse org chart
  - extract organisation structure
  - read organogram
---

# parse-organogram

## Doel

Voer vision-analyse uit op een PDF of PNG van een ministerieel organogram en lever proposals voor de organisatiehierarchie (parent naar child) plus de personen op de zichtbare posten. Personen-extracties krijgen een lage confidence-cap omdat vision foutgevoelig is op kleine namen en initialen.

## Input

- Pad naar een PNG, JPG of PDF van een organogram.
- Optioneel: ministerie-slug en peildatum, indien niet uit metadata afleidbaar.

## Output

JSON-array met proposals. Twee soorten, beide met `bron_pagina_nummer`, `bron_url` en `evidence`:

Orgaanstructuur:

```json
{
  "type": "org_structure",
  "parent_id": "org:min-bzk",
  "child_name": "DG Bestuur en Wonen",
  "bron_pagina_nummer": 1,
  "bron_url": "https://www.rijksoverheid.nl/.../organogram-bzk-2026.pdf",
  "confidence": 0.92,
  "evidence": "blok rechts boven, eerste rij onder SG"
}
```

Persoon-op-post:

```json
{
  "type": "person_post",
  "person_name": "drs. M. de Boer",
  "post_id": "post:sg-min-bzk",
  "classification": "abd-tmg",
  "bron_pagina_nummer": 1,
  "bron_url": "https://www.rijksoverheid.nl/.../organogram-bzk-2026.pdf",
  "confidence": 0.78,
  "evidence": "bovenste box, pagina 1"
}
```

Het `child_id` veld komt later via een aparte entity-resolution-skill of handmatige review. De parser stelt alleen `child_name` voor.

## Stappen voor de LLM

1. Roep Claude vision aan via `anthropic.messages.create` met de afbeelding als image-content. Voor PDF: render eerst per pagina naar PNG (bijvoorbeeld via `pdf2image`), dan vision per pagina.
2. Loop alle gedetecteerde boxen langs. Voor elke box:
   - Lees de titel-regel ("Directie X", "DG Bestuur", "Afdeling Beleid").
   - Lees de eventuele persoonsnaam onder de titel.
   - Volg de verbindingslijn omhoog naar de parent-box; de parent geeft `parent_id`.
3. Map de titel naar `classification` uit `schemas/post.schema.json`:
   - "Secretaris-Generaal", "SG", "plv. SG", "Directeur-Generaal", "DG", "Inspecteur-Generaal", "IG" naar `abd-tmg`.
   - "Directeur", "plv. directeur", "Programmadirecteur" naar `abd-directeur`.
   - "Afdelingshoofd", "Hoofd Afdeling X", "MT-lid", "clusterhoofd" naar `abd-afdelingshoofd`.
   - "Projectleider", "Kwartiermaker" naar `abd-projectleider`.
   - Onbekende titel: laat `classification` weg en flag voor handmatige review.
4. Cap `confidence` voor elk `person_post` proposal op 0.85. Org-structuur mag hoger als de lijnen helder zijn.
5. Skip rood-AVG-niveau posten (beleidsmedewerker, communicatiemedewerker, jurist, secretariaat). Niet extracten, ook niet als ze in het organogram staan.
6. Schrijf het resultaat naar `data/_staging/organogram-{ministerie}-{datum}.json`. Niet direct naar `data/organisaties/`, `data/personen/` of `data/posten/`.

## Harde regels

1. **Staging-only.** Output ALTIJD onder `data/_staging/`. Nooit direct in `data/`.
2. **Confidence-cap.** Personen uit vision: maximaal 0.85, ook bij heldere afbeelding. Vision blijft foutgevoelig.
3. **Quote-or-die voor evidence.** `evidence` beschrijft de locatie in de afbeelding (bijvoorbeeld "blok rechts boven, pagina 2"). Geen evidence, geen proposal.
4. **Two-source rule.** Een organogram-extractie merget alleen met expliciete bevestiging uit een tweede bron (bijvoorbeeld een KB in de Staatscourant) of expliciete human review.
5. **Geen rood-AVG.** Beleidsmedewerkers en juridisch ondersteunend personeel komen niet in proposals, ook niet als ze in de afbeelding zichtbaar zijn.
6. **Bron verplicht.** Elke proposal heeft `bron_pagina_nummer` en `bron_url`. Onleesbare regio: log een waarschuwing met paginanummer, geen gokken.

## Voorbeeld

Zie `example_image_description.md` voor een tekstuele beschrijving van een fictief BZK-organogram (echte PNG of PDF zit niet in de repo wegens omvang) en `example_output.json` voor de bijbehorende proposals.

## Aanroep in workflow

```yaml
- uses: anthropics/claude-code-action@v1
  with:
    anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
    prompt: |
      Lees alle PDF's in _cache/abd-organogrammen/ en parse met de
      parse-organogram skill. Schrijf proposals naar
      data/_staging/organogram-{ministerie}-{datum}.json.
    claude_args: "--model claude-opus-4-7 --max-turns 15"
```

Vision werkt het best met opus, niet haiku.

## Aanroep vanuit Claude Code CLI

```bash
claude "Gebruik parse-organogram op _cache/abd-organogrammen/bzk-2026-04.pdf en schrijf naar data/_staging/organogram-bzk-2026-04.json"
```

## Status

Actief, versie 0.2.0.
