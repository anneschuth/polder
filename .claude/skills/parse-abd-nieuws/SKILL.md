---
name: parse-abd-nieuws
description: Parse een ABD-nieuwsbericht (HTML van algemenebestuursdienst.nl/actueel/nieuws) naar Membership-proposals met evidence_snippet als verifieerbare substring. Gebruik wanneer de gebruiker zegt 'parse abd-nieuws', 'verwerk abd-bericht', 'extract benoeming uit abd', 'lees abd-nieuwsbericht', of in English 'parse abd news', 'extract appointments from abd', 'process abd article'.
version: 0.2.0
triggers:
  - parse abd-nieuws
  - verwerk abd-bericht
  - extract benoeming uit abd
  - lees abd-nieuwsbericht
  - parse abd news
  - extract appointments from abd
  - process abd article
---

# parse-abd-nieuws

## Doel

Lees een nieuwsbericht van `algemenebestuursdienst.nl/actueel/nieuws/...` (HTML) en zet de tekst om in Membership-proposals voor Polder. Eén proposal per benoeming, ontslag of verlenging in het bericht. ABD plaatst benoemingen vaak voor het KB in de Staatscourant, dus deze skill is een early-warning bron.

## Input

- Pad naar een HTML-bestand uit `_cache/abd-nieuws/<slug>-<date>.html`, of een HTML-string in geheugen.
- De bron-URL leid je af uit `<meta name="DCTERMS.identifier">` of `<link rel="canonical">`.
- Datum komt uit de URL-pad (`/YYYY/MM/DD/`) en wordt bevestigd door `Nieuwsbericht DD-MM-YYYY` in de body.

## Output

JSON-array met proposals, één per benoeming, ontslag, verlenging of aankondiging. Elk proposal heeft:

- `person_name` (string): naam zoals in het bericht, met titulering en initialen indien aanwezig.
- `existing_person_id` (string of null): polder-slug bij match in `data/personen/`, anders null.
- `organization_id` (string): slug van de organisatie waar de post bij hoort.
- `post_id` (string): slug van de post (bijv. `post:dg-migratie-min-jenv`). Stel een nieuwe slug voor als er geen match is, en flag dat in `confidence_reasoning`.
- `role` (string): functie zoals "directeur-generaal Migratie bij het ministerie van Justitie en Veiligheid".
- `start_date` (ISO 8601 of null): ingangsdatum van de benoeming, expliciet uit "per <datum>" of "met ingang van <datum>".
- `end_date` (ISO 8601 of null): null bij benoeming, datum bij ontslag of einde verlenging.
- `decision_reference` (string): KB-nummer als het bericht dit noemt, anders `"ABD-nieuwsbericht <YYYY-MM-DD>"`.
- `staatscourant_url` (string of null): URL naar het KB in de Staatscourant als het bericht ernaar linkt; anders null.
- `abd_nieuws_url` (string, verplicht): de canonical URL van het nieuwsbericht.
- `event_type` (string): één van `benoeming`, `ontslag`, `verlenging`, `aankondiging`, `overig`.
- `confidence` (float, 0 tot 1).
- `confidence_reasoning` (string): welke signalen meetelden, en welke ontbreken.
- `evidence_snippet` (string): letterlijke substring uit de artikel-tekst met de feiten.

## Stappen voor de LLM

1. Laad de HTML met `BeautifulSoup` of vergelijkbaar. Pak de body-tekst (meestal in `<article>` of `<main>`). Bewaar de raw plain-text voor de substring-check.
2. Identificeer organisatie en post. Lees titel, eerste alinea en de "bij <organisatie>" suffix in de URL-slug. Match tegen `data/organisaties/` op naam of afkorting (`JenV`, `IenW`, `OCW`, `BZK`, `Belastingdienst`, ...).
3. Per benoeming of ontslag in de tekst:
   - Extract persoonsnaam. ABD gebruikt zelden titulering, dus vaak alleen voornaam plus achternaam.
   - Extract functie. Let op samenstellingen zoals "kwartiermaker/directeur" en "plaatsvervangend directeur".
   - Extract ingangsdatum: zoek "De benoeming gaat in op <datum>", "per <datum>", "met ingang van <datum>".
   - Extract KB-referentie als die in het bericht staat (vaak in de laatste alinea).
4. Bouw `event_type`. Heuristieken:
   - Titel of tekst noemt "benoemd", "wordt directeur", "wordt DG": `benoeming`.
   - "neemt afscheid", "vertrekt", "wordt opgevolgd": `ontslag`.
   - "verlengd", "wordt herbenoemd": `verlenging`.
   - Persbericht zonder concrete persoon-functie-koppeling (jaarverslag, ABD-blad): `overig` met confidence-cap 0.4.
5. Confidence-rubriek (geldt voor merge-overweging, niet voor staging-write):
   - Volledige naam plus expliciete functie plus expliciete datum plus KB-referentie of staatscourant-link: tot 0.9. Cap op 0.85 voor standalone ABD-nieuws zonder KB-link, in lijn met de two-source rule.
   - KB-link in tekst (`zoek.officielebekendmakingen.nl/stcrt-...`): cap mag tot 0.95 omdat het bericht dan effectief twee bronnen citeert.
   - Naam ambigu (twee of meer matches in `data/personen/`): max 0.7, forceert review.
   - Functie niet matchbaar tegen bestaande post: max 0.6, plus `confidence_reasoning` waarin je een nieuwe `post_id`-suggestie expliciet noemt.
6. Substring-check vóór output: `assert evidence_snippet in raw_html_text`. Faal hard als de assert false retourneert. Geen paraphrase, geen normalisatie, geen whitespace-trimming.

## Harde regels

1. **Quote-or-die.** `evidence_snippet` is een letterlijke substring van de gedownloade artikel-HTML. Validator faalt anders.
2. **Two-source rule.** Een proposal uit alleen ABD-nieuws krijgt confidence-cap 0.85 en merget niet automatisch. Met expliciete KB-link of staatscourant-URL in de tekst mag de cap naar 0.95.
3. **Staging-only.** Schrijf naar `data/_staging/abd-nieuws-YYYY-MM-DD.json`. Nooit direct naar `data/personen/`, `data/organisaties/` of `data/posten/`.
4. **Geen privé-data.** Alleen functie en naam en datum. Geen geboortedatum (alleen jaartal als die elders al vaststaat), geen contactgegevens, nooit BSN.
5. **Confidence per proposal** als float in [0, 1] met `confidence_reasoning` als string.

## Voorbeeld

Zie `example_input.md` voor de relevante body-tekst van een ABD-nieuwsbericht, en `example_output.json` voor het bijbehorende proposal. De `evidence_snippet` in het output-bestand is letterlijk te vinden in de input.

## Aanroep vanuit Claude Code CLI

```bash
claude "Gebruik parse-abd-nieuws op _cache/abd-nieuws/esther-pijs-directeur-generaal-migratie-bij-jenv-2026-05-08.html en schrijf naar data/_staging/abd-nieuws-2026-05-09.json"
```

## Status

Actief, versie 0.2.0. Vierde skill in Polder, na review-pr-diff, parse-staatscourant en parse-organogram.
