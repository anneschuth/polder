---
name: parse-organogram
description: Vision- of HTML-tekstanalyse op een organogram, extract organisatiehierarchie en bemenste posten. Gebruik wanneer de gebruiker een organogram-afbeelding of inline organogram-tekst aanlevert of zegt 'parse organogram', 'extract organisatiestructuur', 'lees organogram', 'organogram naar JSON', or in English 'parse org chart', 'extract organisation structure', 'read organogram'.
version: 0.3.0
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

Lever proposals voor de organisatiehierarchie (parent naar child) plus de personen op de zichtbare posten van een ministerieel organogram. Twee inputmodi: vision-analyse op een PDF of PNG, of tekstanalyse op de `inline_text` uit het ABD-manifest. Personen-extracties krijgen een confidence-cap omdat zowel vision als afgeknipte HTML-tekst foutgevoelig zijn op initialen.

## Input

Eén van:

- Pad naar een PNG, JPG of PDF van een organogram (vision-modus).
- Een entry uit `data/_staging/abd-manifest-<datum>.json` waarvan `inline_text` is gevuld (tekstmodus). Sommige ministeries (AZ, DEF, OCW) publiceren geen organogram-PDF maar wel een HTML-pagina met directie-namen en personen.

Optioneel: ministerie-slug en peildatum, indien niet uit metadata afleidbaar.

## Output

**ALLEEN JSON-array als laatste output, geen andere tekst.** Geen introductie, geen samenvatting, geen markdown-fences, geen "Next step:". De runner vangt jouw stdout op en schrijft die naar het juiste pad. Schrijf zelf geen bestanden met `Write` en noem geen output-pad in je antwoord. Tools zijn alleen voor `Read` (PDF) en `polder search` (slug-lookup).

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

### Vision-modus (PDF of PNG)

1. Roep Claude vision aan via `anthropic.messages.create` met de afbeelding als image-content. Voor PDF: render eerst per pagina naar PNG (bijvoorbeeld via `pdf2image`), dan vision per pagina.
2. Loop alle gedetecteerde boxen langs. **Ga zo diep als de afbeelding gaat** — niveaus ministerie -> DG -> directie -> afdeling -> team zijn ALLE relevant. Een organogram toont vaak meer dan alleen DG/directie; sla afdelingen niet over. Voor elke box:
   - Lees de titel-regel ("Directie X", "DG Bestuur", "Afdeling Beleid", "Cluster Y", "Team Z").
   - Lees de eventuele persoonsnaam onder de titel.
   - Volg de verbindingslijn omhoog naar de parent-box; de parent geeft `parent_id`.
3. Map de titel naar `classification` (zie mapping hieronder).
4. Cap `confidence` voor elk `person_post` proposal op 0.85. Org-structuur mag hoger als de lijnen helder zijn.
5. **Afdeling-niveau verplicht waar het bestaat in de bron.** Als de bron afdelingen toont onder een directie (zelfs zonder eigen persoon-naam), produceer dan toch een `org_structure`-record voor elke afdeling met de directie als `parent_id`. Liever 50 afdeling-records met `confidence: 0.85` dan een platte boom met alleen 17 directies.

### Tekstmodus (inline_text uit manifest)

1. Lees `inline_text` van de manifest-entry. Parse koppen en sub-koppen (h1/h2/h3 zijn al weg, maar woorden als "DG", "Directie", "Afdeling" markeren niveaus).
2. Voor elk herkend kopje: maak een `org_structure`-proposal met `parent_id` op het bovenliggende niveau (root = `org:<ministerie-slug>`).
3. Voor elke persoonsnaam direct na een kopje: maak een `person_post`-proposal. `evidence` MOET een letterlijke substring van `inline_text` zijn die de naam plus omringende context bevat (minimaal de zin waar de naam in staat). Geen substring, geen proposal.
4. Map de titel naar `classification` (zie mapping hieronder).
5. Cap `confidence` voor `person_post` op 0.85, voor `org_structure` op 0.90 als de hiërarchie eenduidig is.

### Classification-mapping

- "Secretaris-Generaal", "SG", "plv. SG", "Directeur-Generaal", "DG", "Inspecteur-Generaal", "IG" naar `abd-tmg`.
- "Directeur", "plv. directeur", "Programmadirecteur" naar `abd-directeur`.
- "Afdelingshoofd", "Hoofd Afdeling X", "MT-lid", "clusterhoofd" naar `abd-afdelingshoofd`.
- "Projectleider", "Kwartiermaker" naar `abd-projectleider`.
- Onbekende titel: laat `classification` weg en flag voor handmatige review.

### Hiërarchie-patroon ministeries

Een Nederlands ministerie heeft een vaste top-laag die in elk organogram terugkomt. Volg dit patroon strict, niet de visuele layout:

1. `org:min-<x>` is de root.
2. Bewindspersonen-posten (`post:minister-min-<x>`, `post:staatssecretaris-min-<x>`, eventuele MZP-posten) hangen DIRECT onder `org:min-<x>`. Geen tussenliggend organisatieonderdeel.
3. `org:onderdeel-sg-min-<x>` (Secretaris-generaal Cluster, classification `organisatieonderdeel`) hangt onder `org:min-<x>`. Hier komt `post:sg-min-<x>` (SG) en `post:plv-sg-min-<x>` (plv-SG).
4. ALLE DG's, het Bureau ABD, AIVD, eventuele programma-DG's en clusters (Bestuursondersteuning, Mensen en Middelen) hangen onder `org:onderdeel-sg-min-<x>`, NIET direct onder `org:min-<x>`.
5. Onder elke DG hangen de directies (`org:onderdeel-directie-<x>-min-<y>`), onder de directies hangen de afdelingen.

Concreet voor BZK (zie `_cache/organogrammen/bzk-2026-organogram.pdf` als referentie-voorbeeld): SG Vincent Roozen en plv-SG Mark de Boer staan tussen de bewindspersonen-strook en de DG-rij; DGDOO, DGKR, DG VHB, DG RO, DG VBR, DG OBDR, DG AIVD, DG ABD hebben SG-cluster als parent.

In tekstmodus is dit niet altijd visueel zichtbaar. Default-aanname dan: nieuwe org_structure-records met `classification: abd-tmg` (DG/IG) krijgen `parent_id: org:onderdeel-sg-min-<x>`, tenzij de tekst expliciet anders zegt.

### Output schrijven

Skip rood-AVG-niveau posten (beleidsmedewerker, communicatiemedewerker, jurist, secretariaat). Niet extracten, ook niet als ze in de bron staan. De runner schrijft de proposals automatisch naar `data/_staging/organogram-{ministerie}-{datum}.json`; jij produceert alleen de JSON-array op stdout.

## Harde regels

1. **Staging-only.** Output ALTIJD onder `data/_staging/`. Nooit direct in `data/`.
2. **Confidence-cap.** Personen: maximaal 0.85, ook bij heldere bron. Vision en HTML-tekst blijven foutgevoelig op initialen.
3. **Quote-or-die voor evidence.** Vision-modus: `evidence` beschrijft de locatie in de afbeelding (bijvoorbeeld "blok rechts boven, pagina 2"). Tekstmodus: `evidence` is een letterlijke substring van `inline_text`. Geen evidence, geen proposal.
4. **Two-source rule.** Een organogram-extractie merget alleen met expliciete bevestiging uit een tweede bron (bijvoorbeeld een KB in de Staatscourant) of expliciete human review.
5. **Geen rood-AVG.** Beleidsmedewerkers en juridisch ondersteunend personeel komen niet in proposals, ook niet als ze in de bron zichtbaar zijn.
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
