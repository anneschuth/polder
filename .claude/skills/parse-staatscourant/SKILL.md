---
name: parse-staatscourant
description: Parse een Staatscourant-publicatie (KB-XML) naar Membership-proposals met evidence_snippet als verifieerbare substring. Gebruik wanneer de gebruiker zegt 'parse staatscourant', 'verwerk KB', 'extract benoemingen', 'lees besluittekst', of in English 'parse staatscourant', 'extract appointments', 'process KB document', of een Staatscourant-XML aanlevert.
version: 0.3.0
triggers:
  - parse staatscourant
  - verwerk KB
  - extract benoemingen
  - lees besluittekst
  - parse staatscourant
  - extract appointments
  - process KB document
---

# parse-staatscourant

## Doel

Lees een Staatscourant-publicatie (KB-XML van KOOP / officielebekendmakingen.nl) en zet de tekst om in Membership-proposals voor Polder. Eén proposal per benoeming, ontslag of verlenging in het KB.

## Input

Een gestructureerde plain-text payload met deze secties (in volgorde):

```
KB_REFERENCE:
<bv. stcrt-2024-7691>          (afgeleid uit filename, basis voor decision_reference)

STAATSCOURANT_URL:
<https://zoek.officielebekendmakingen.nl/stcrt-2024-7691.html>

INTITULE:                       (optioneel, ontbreekt bij sommige besluit-types)
<titel-zin met datum, kenmerk, type besluit, organisatie>

BODY:
<plain-text besluit-tekst, regel-per-regel; tabellen als rij-per-rij;
 NAAM-regels markeren ondertekenaars met voornaam-initialen + achternaam>
```

- `KB_REFERENCE` is de basis voor `decision_reference` (formatteer als bijv. "Staatscourant nr. 2024-7691")
- `STAATSCOURANT_URL` gebruikt de skill voor `staatscourant_url`
- `INTITULE` (als aanwezig) bevat de titel-zin met type besluit en organisatie
- `BODY` heeft de besluit-tekst zonder XML-attributen, geschikt voor naam/functie/datum-extractie
- `NAAM:` regels onderaan zijn ondertekenaars (minister/SG/voorzitter), niet de benoemde personen

## Output

**ALLEEN JSON-array als laatste output, geen andere tekst.** Output is een JSON-array met proposals, één per benoeming of ontslag. Geen introductietekst, geen samenvatting, geen markdown-fences, geen "Next step:"-suggesties. Alleen de array.

**Schrijf zelf geen bestanden.** De runner vangt jouw stdout op en schrijft die naar het juiste pad. Gebruik nooit `Write` of `>` om JSON naar disk te zetten, en noem geen output-pad in je antwoord.

Elk proposal heeft:

- `person_name` (string): naam zoals in het KB, met titulering en initialen.
- `existing_person_id` (string of null): polder-slug bij match, anders null.
- `organization_id` (string): slug van de organisatie waar het KB op slaat.
- `post_id` (string): slug van de post (bijv. `post:sg-min-bzk`). **NOOIT** een ABD-functie (raadadviseur, directeur, afdelingshoofd, SG, DG, IG, kwartiermaker, projectleider) mappen op `post:minister-*` of `post:staatssecretaris-*`. Een ambtenaar is geen bewindspersoon.
- `role` (string): tekst zoals "Secretaris-generaal van het ministerie van BZK". **Casing**: begin de `role` met een hoofdletter op de functietitel (`Secretaris-generaal`, `Directeur`, `Minister`, `Staatssecretaris`, ...), ook als het KB anders schrijft. Uitzondering: gevestigde afkortingsvormen (`pSG`, `plv.`). Het afgeleide `role`-veld wordt gecanonicaliseerd; een letterlijke `evidence_snippet` behoudt de bron-casing (quote-or-die).
- `start_date` (ISO 8601): ingangsdatum van de benoeming.
- `end_date` (ISO 8601 of null): null voor benoeming, datum voor ontslag.
- `decision_reference` (string): KB-nummer plus datum, bijv. "KB nr. 2026-001234, 15 april 2026".
- `staatscourant_url` (string): URL naar de publicatie.
- `confidence` (float, 0 tot 1).
- `confidence_reasoning` (string): welke signalen meetelden.
- `evidence_snippet` (string): letterlijke substring uit het KB-XML met de feiten.

## Stappen voor de LLM

1. Lees de payload-secties (`KB_REFERENCE`, `STAATSCOURANT_URL`, optioneel `INTITULE`, `BODY`). De combined payload is je raw text voor de evidence-substring-check.
2. Identificeer welke organisatie het betreft. Lees `INTITULE` (als aanwezig) en de eerste regels van `BODY` (bv. "De Minister voor Rechtsbescherming,"). Match tegen `data/organisaties/` op naam of afkorting.
3. Voor elke benoeming of ontslag in `BODY`:
   - Extract persoonsnaam (titulering, voornamen of initialen, achternaam).
   - Extract functie (Secretaris-Generaal, Directeur-Generaal, plaatsvervangend SG, lid van bestuur X, ...).
   - Extract ingangsdatum: zoek "per <datum>" of "met ingang van <datum>".
   - `decision_reference`: combineer `KB_REFERENCE` met datum, bv. "Staatscourant nr. 2024-7691, 28 februari 2024".
   - `staatscourant_url`: gebruik de waarde uit de payload-sectie.
4. Stel `organization_id` en `post_id` voor volgens de Polder-conventie. Voor ministeries: `org:min-<afkorting>` (bv. `org:min-def`, `org:min-fin`, `org:min-jenv`, `org:min-bzk`, `org:min-ocw`, `org:min-szw`, `org:min-vws`, `org:min-bz`, `org:min-ienw`, `org:min-lvvn`, `org:min-az`, `org:min-ezk`, `org:min-kgg`). Bewindspersoon-posts: `post:minister-min-<afkorting>` of `post:staatssecretaris-min-<afkorting>`. Een aliassen-fallback in de resolver matcht varianten zoals `org:ministerie-defensie` of `post:minister-defensie` ook op de canonical slug, dus exactheid is geen blocker, maar volg de conventie waar je hem kent.
5. Negeer `NAAM:`-regels onderaan: dat zijn ondertekenaars (minister/SG/voorzitter die het besluit tekent), niet de benoemde personen. Tenzij de ondertekenaar zelf in de body wordt benoemd of ontslagen.
6. Bouw het proposal. Confidence-rubriek:
   - Volledige naam plus expliciete functie plus expliciete datum plus KB-referentie: 0.95 of hoger.
   - Naam ambigu (twee of meer matches in `data/personen/`): maximaal 0.7, forceert review.
   - KB-referentie ontbreekt of post niet matchbaar: maximaal 0.6.
7. Substring-check vóór output: `assert evidence_snippet in payload_text` waar `payload_text` de complete input-payload is. Faal hard als de assert false retourneert. Geen paraphrase, geen normalisatie, geen whitespace-trimming.

## Harde regels

1. **Quote-or-die.** `evidence_snippet` is een letterlijke substring van de input-payload (INTITULE of BODY). Validator faalt anders.
2. **Two-source rule.** Een proposal merget alleen automatisch als `confidence` minimaal 0.98 is plus een 7-daags review-window. Anders is een tweede onafhankelijke bron vereist.
3. **Staging-only.** Schrijf naar `data/_staging/staatscourant-YYYY-MM-DD.json`. Nooit direct naar `data/organisaties/`, `data/personen/` of `data/posten/`.
4. **Confidence per proposal** als float in [0, 1] met expliciete `confidence_reasoning`.
5. Geen BSN, geen geboortedatum, geen privé-contactgegevens in een proposal. Alleen jaartal in `birth.year` als die elders al vaststaat.

## Voorbeeld

Zie `example_input.xml` voor een KB-fragment, en `example_output.json` voor het bijbehorende proposal. De `evidence_snippet` in het output-bestand is letterlijk te vinden in de input.

## Aanroep in workflow

```yaml
- uses: anthropics/claude-code-action@v1
  with:
    anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
    prompt: |
      Lees alle nieuwe KB's uit _cache/staatscourant/ en parse met de
      parse-staatscourant skill. Schrijf proposals naar
      data/_staging/staatscourant-{date}.json.
    claude_args: "--model claude-haiku-4-5 --max-turns 10"
```

## Aanroep vanuit Claude Code CLI

```bash
claude "Gebruik parse-staatscourant op _cache/staatscourant/stcrt-2026-12345.xml en schrijf naar data/_staging/staatscourant-2026-05-09.json"
```

## Status

Actief, versie 0.2.0. Tweede skill na review-pr-diff.
