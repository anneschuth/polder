---
name: parse-staatscourant
description: Parse een Staatscourant-publicatie (XML met KB-tekst) naar Membership-proposals met evidence_snippet als verifieerbare substring. Gebruik wanneer de gebruiker zegt 'parse staatscourant', 'verwerk KB', 'extract benoemingen', 'lees besluittekst', or in English 'parse staatscourant', 'extract appointments', 'process KB document', of een Staatscourant-XML aanlevert.
version: 0.1.0
---

# parse-staatscourant

## Doel

Lees een Staatscourant-publicatie (XML met vrije besluittekst van de Koninklijke Bibliotheek) en zet deze om in Membership-proposals voor het Polder-databestand. Eén proposal per benoeming, ontslag of verlenging.

## Input

- Pad naar een XML-bestand, of een XML-string in het geheugen.
- Optioneel: publicatiedatum en bron-URL als die niet uit het XML komen.

## Output

JSON-array met proposals. Elk proposal heeft:

- `person_name` (string)
- `existing_person_id` (string of null)
- `organization_id` (string)
- `post_id` (string)
- `role` (string)
- `start_date` (ISO 8601 of null)
- `end_date` (ISO 8601 of null)
- `decision_reference` (string, bijv. "Stcrt. 2026, 12345")
- `confidence` (float 0-1)
- `confidence_reasoning` (string)
- `evidence_snippet` (string)

## Harde regels

1. `evidence_snippet` MOET een letterlijke substring zijn van de bron-tekst. Voer een pre-output substring-check uit in code; faal hard als de check false retourneert.
2. Schrijf NOOIT direct naar `data/`. Output gaat naar `data/_staging/staatscourant-YYYY-MM-DD.json`.
3. Two-source rule: een proposal mag alleen automatisch door als er een tweede onafhankelijke bron is, of als `confidence` minimaal 0.98 is en er een review-window van 7 dagen geldt.
4. `confidence_reasoning` is verplicht en specifiek: noem welke signalen meetelden (expliciete datum, expliciete post-ID, naamoverlap met bestaand persoon).

## Voorbeeld

Input: `stcrt-2026-12345.xml` met regel "Met ingang van 1 juni 2026 wordt benoemd tot directeur Bestuur en Organisatie van het ministerie van BZK: drs. P. de Vries."

Output:

```json
[{
  "person_name": "P. de Vries",
  "existing_person_id": null,
  "organization_id": "org:bzk",
  "post_id": "post:bzk-directeur-bestuur-organisatie",
  "role": "directeur",
  "start_date": "2026-06-01",
  "end_date": null,
  "decision_reference": "Stcrt. 2026, 12345",
  "confidence": 0.94,
  "confidence_reasoning": "Expliciete datum, expliciete post, naam matcht geen bestaand persoon.",
  "evidence_snippet": "Met ingang van 1 juni 2026 wordt benoemd tot directeur Bestuur en Organisatie van het ministerie van BZK: drs. P. de Vries."
}]
```

## Status

Stub. Implementatie volgt in week 5 van de roadmap.
