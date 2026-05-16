---
name: parse-abd-nieuws
description: Parse een ABD-nieuwsbericht (HTML van algemenebestuursdienst.nl/actueel/nieuws) naar Membership-proposals met evidence_snippet als verifieerbare substring en organization_chain (ministerie, DG, directie, afdeling). Gebruik wanneer de gebruiker zegt 'parse abd-nieuws', 'verwerk abd-bericht', 'extract benoeming uit abd', 'lees abd-nieuwsbericht', of in English 'parse abd news', 'extract appointments from abd', 'process abd article'.
version: 0.6.0
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

## Instructie: OUTPUT ENKEL JSON

Je gaat een ABD-nieuwsbericht (HTML) in een membership-proposal omzetten. De ENIGE output die je moet produceren is een JSON-array. **GEEN inleiding, GEEN analyse, GEEN verklaring, GEEN samenvatting**. Alleen het JSON, startend met `[` en eindigend met `]`.

## Doel

Lees een nieuwsbericht van `algemenebestuursdienst.nl/actueel/nieuws/...` (HTML) en zet de tekst om in Membership-proposals voor Polder. Eén proposal per benoeming, ontslag of verlenging in het bericht. ABD plaatst benoemingen vaak voor het KB in de Staatscourant, dus deze skill is een early-warning bron.

## Input

Een gestructureerde plain-text payload met deze secties (in volgorde):

```
CANONICAL_URL:
<absolute URL van het nieuwsbericht>

TWITTER_DESCRIPTION:
<éénregelige samenvatting van de benoeming, hoogste signaal-dichtheid>

STAATSCOURANT_URLS:           (optioneel, alleen als er Staatscourant-links in het bericht staan)
<URL 1>
<URL 2>
...

BODY:
<plain-text artikel-tekst, footer-boilerplate al afgekapt>
```

- `CANONICAL_URL` is de waarde voor `abd_nieuws_url` in elk proposal.
- `TWITTER_DESCRIPTION` is de gegarandeerde kern-zin: hieruit haal je in de meeste gevallen `person_name`, `role`, `organization`, en `start_date`.
- `STAATSCOURANT_URLS` (als aanwezig) levert `staatscourant_url`. Als de sectie ontbreekt, is `staatscourant_url` null.
- `BODY` bevat de volledige artikel-tekst voor extra context: KB-referentie, opvolging, CV-zinnen, datum-bevestiging via `Nieuwsbericht DD-MM-YYYY`.
- Datum leid je af uit het URL-pad in `CANONICAL_URL` (`/YYYY/MM/DD/`) of uit `BODY`.

## Output

**ALLEEN JSON-array als laatste output, geen andere tekst.** Geen introductie, geen samenvatting, geen markdown-fences, geen "Next step:". De runner vangt jouw stdout op en schrijft die naar het juiste pad. Schrijf zelf geen bestanden met `Write` en noem geen output-pad in je antwoord. Tools zijn alleen voor read-only lookups (`polder search`, `polder show`).

JSON-array met proposals, één per benoeming, ontslag, verlenging of aankondiging. Elk proposal in de array heeft:

- `person_name` (string): naam zoals in het bericht, met titulering en initialen indien aanwezig.
- `existing_person_id` (string of null): polder-slug bij match in `data/personen/`, anders null.
- `organization_id` (string): slug van het diepste organisatie-niveau dat in het bericht genoemd wordt (afdeling boven directie boven DG boven ministerie). Verbreed niet automatisch.
- `organization_chain` (array): hiërarchische keten vanaf ministerie naar afdeling. Elke entry is `{level, name, slug_proposal}`. Levels: `ministerie`, `directoraat-generaal`, `directie`, `afdeling`. Alleen niveaus die letterlijk in het bericht voorkomen, in volgorde van top naar diepst.
- `post_id` (string): slug van de post (bijv. `post:dg-migratie-min-jenv` of `post:afdelingshoofd-beleid-wonen-min-vro`). Stel een nieuwe slug voor als er geen match is, en flag dat in `confidence_reasoning`. **NOOIT** een ABD-functie (raadadviseur, directeur, afdelingshoofd, SG, DG, IG, kwartiermaker, projectleider) mappen op een post die begint met `post:minister-` of `post:staatssecretaris-` of `post:vice-minister-`. Een raadadviseur "bij het Kabinet Minister-President" is geen minister-president maar een ambtenaar; gebruik een nieuwe slug zoals `post:raadadviseur-<portefeuille>-min-az`.
- `role` (string): functie zoals "afdelingshoofd Beleid Wonen, Directie Wonen, ministerie van VRO".
- `start_date` (ISO 8601 of null): ingangsdatum van de benoeming, expliciet uit "per <datum>" of "met ingang van <datum>".
- `end_date` (ISO 8601 of null): null bij benoeming, datum bij ontslag of einde verlenging.
- `decision_reference` (string): KB-nummer als het bericht dit noemt, anders `"ABD-nieuwsbericht <YYYY-MM-DD>"`.
- `staatscourant_url` (string of null): URL naar het KB in de Staatscourant als het bericht ernaar linkt; anders null.
- `abd_nieuws_url` (string, verplicht): de canonical URL van het nieuwsbericht.
- `event_type` (string): één van `benoeming`, `ontslag`, `verlenging`, `aankondiging`, `overlijden`, `overig`.
- `confidence` (float, 0 tot 1).
- `confidence_reasoning` (string): welke signalen meetelden, en welke ontbreken.
- `evidence_snippet` (string): letterlijke substring uit de payload (uit `TWITTER_DESCRIPTION` of `BODY`) met de feiten.

## Stappen voor de LLM

1. Lees de payload-secties (`CANONICAL_URL`, `TWITTER_DESCRIPTION`, evt. `STAATSCOURANT_URLS`, `BODY`). De combined payload is je raw text voor de evidence-substring-check; `evidence_snippet` MOET een letterlijke substring van de payload zijn (whitespace en interpunctie meetellen).
2. Identificeer organisatie en post. Lees titel, eerste alinea en de "bij <organisatie>" suffix. Stel slugs voor volgens de Polder-conventie: ministeries als `org:min-<afkorting>` (`org:min-jenv`, `org:min-fin`, `org:min-bzk`, ...), DG/directie/afdeling als `org:onderdeel-<slug>-min-<min-slug>`. De resolver matcht varianten (`ministerie-X`, `minister-X`) achteraf op de canonical slug, dus exactheid is geen blocker; volg de conventie waar je hem kent.
3. Zoek benoemings- en ontslagpatronen. ABD-berichten volgen meestal één van deze sjablonen:
   - **Standaard benoeming**: "X wordt [met ingang van <datum>] <functie> bij/onderdeel van <organisatieketen>. ... De benoeming gaat in op <datum>."
   - **Alternatief**: "X wordt <functie>, een <subunit> van de <parent> bij het ministerie van Y. De benoeming gaat in op <datum>."
   - **Ontslagmelding**: "X neemt afscheid / vertrekt per <datum>. X wordt opgevolgd door Y."
   - **Verlenging**: "X wordt herbenoemd / verlengd als <functie> voor <periode>."
   - **Overlijdensbericht**: "X is op <DD-MM-YYYY> overleden" / "X is op <leeftijd>-jarige leeftijd overleden" / "In memoriam: X". Hier is er geen post-mutatie; het bericht meldt een overlijden van een (oud-)topambtenaar.
4. Per benoeming of ontslag in de tekst:
   - Extract persoonsnaam (volledige naam, zelden titulering).
   - Extract functie. Let op samenstellingen zoals "kwartiermaker/directeur", "waarnemend pSG", "plaatsvervangend directeur".
   - Extract ingangsdatum: zoek "De benoeming gaat in op <datum>", "per <datum>", "met ingang van <datum>", of bepaal uit context.
   - Extract KB-referentie als die in het bericht staat (meestal laatste alinea).
4. **Identificeer organisatie-niveaus.** Berichten beschrijven vaak een keten van top naar diepst:
   - "ministerie van X" (top, level `ministerie`).
   - "directoraat-generaal Y" of "DG Y" (level `directoraat-generaal`, organisatieonderdeel).
   - "directie Z" of "concerndirectie Z" (level `directie`).
   - "afdeling W" (level `afdeling`, het diepst).
   Extract alle niveaus die het bericht letterlijk noemt en bouw `organization_chain` als array van top naar diepst. Per entry een slug-voorstel volgens de Polder-conventie:
   - Ministerie: `org:min-<slug>` (bestaat al, ROO).
   - DG, directie, afdeling: `org:onderdeel-<slug>-min-<min-slug>` of korter `org:onderdeel-<slug>` als die slug al bestaat in `data/organisaties/organisatieonderdelen/`.
   `organization_id` is altijd de slug van het diepste niveau in `organization_chain`. Geen automatische verbreding: als het bericht alleen "directeur Wonen" zegt, is `organization_id` directie-niveau; bij "afdelingshoofd Beleid Wonen" is het afdeling-niveau.

   **Chain is verplicht voor abd-classification-rollen.** Voor `directeur`, `afdelingshoofd`, `secretaris-generaal`, `directeur-generaal`, `inspecteur-generaal`: lever ALTIJD minimaal 2 chain-entries (ministerie + één tussenliggend organisatieonderdeel). Een directeur hoort onder een directie of agentschap, geen ministerie direct. Als de bron het tussenliggende niveau niet expliciet noemt: leid het af uit de role-string ("directeur concerndirectie Mens en Organisatie" -> concerndirectie als chain[1] met slug `org:onderdeel-concerndirectie-mens-en-organisatie-min-<min>`). Een chain met alleen ministerie + organization_id=ministerie is fout voor deze classifications.
5. Bouw `event_type`. Heuristieken:
   - Titel of tekst noemt "benoemd", "wordt directeur", "wordt DG": `benoeming`.
   - "neemt afscheid", "vertrekt", "wordt opgevolgd": `ontslag`.
   - "verlengd", "wordt herbenoemd": `verlenging`.
   - "is overleden", "overlijdensbericht", "is op X-jarige leeftijd overleden", "in memoriam": `overlijden`.
   - Persbericht zonder concrete persoon-functie-koppeling (jaarverslag, ABD-blad): `overig` met confidence-cap 0.4.

   **Bij `overlijden`**: `post_id`, `organization_id` en `organization_chain` zijn niet van toepassing. Een overlijden is geen post-mutatie maar sluit van rechtswege alle nog lopende mandaten. Lever `post_id: null`, `organization_id: null`, `organization_chain: []`. Vul `end_date` met de overlijdensdatum (uit "is op DD-MM-YYYY overleden", "op DD-MM-YYYY", of "Nieuwsbericht DD-MM-YYYY" als laatste terugval). Laat `start_date: null`. `person_name` is verplicht; `existing_person_id` invullen als je een match in `data/personen/` vindt (de resolver matcht anders op familienaam).
6. Bepaal `confidence` volgens de regels in "Confidence-bepaling" hieronder.
7. Substring-check vóór output: `assert evidence_snippet in payload_text` waar `payload_text` de complete input-payload is (alle secties samen). Faal hard als de assert false retourneert. Geen paraphrase, geen normalisatie, geen whitespace-trimming.

## Confidence-bepaling

Vanaf v0.4.0 vervangen onderstaande regels de oude vlakke 0.85-cap. De drempel voor `apply-staging-auto` is 0.85; tussen 0.85 en 0.94 is een proposal dus auto-mergeable.

### Floor: 0.85 als de vier kernfeiten expliciet en ondubbelzinnig zijn

Bij `event_type` van `benoeming`, `ontslag` of `verlenging` geldt een vloer van 0.85 als alle vier hieronder waar zijn:

1. **Familienaam expliciet** in de tekst (volledige achternaam letterlijk genoemd).
2. **Functie expliciet** (functietitel woordelijk in de tekst).
3. **Organisatie expliciet** (ministerie of organisatieonderdeel woordelijk genoemd, of duidelijk in de `organization_chain`).
4. **Datum expliciet** (`start_date` of `end_date` afleidbaar uit een ISO-converteerbare datum-frase).

Als deze vier kloppen: `confidence` mag niet onder 0.85 zakken, ook niet door de verzwarende factoren hieronder.

### Cap: 0.94 zonder `staatscourant_url`

De two-source rule blijft staan: zonder externe verificatie via een Staatscourant-URL is de bovengrens 0.94. Met een geldige `staatscourant_url` mag de confidence tot 0.97. Boven de 0.97 vereist een tweede onafhankelijke bron buiten ABD plus Staatscourant.

### Lagere ceiling bij ontbrekende kernfeiten

- **1 van de 4 kernfeiten ontbreekt** (bijvoorbeeld geen datum, of organisatie alleen impliciet): max 0.80.
- **2 of meer kernfeiten ontbreken**: max 0.65.
- **Naam ambigu** (twee of meer matches in `data/personen/` zonder onderscheidende informatie): max 0.55, forceert review.

### Overlijden: aparte schaal

Een `overlijden`-proposal kent geen vier kernfeiten (geen functie/organisatie/start). De confidence hangt af van twee dingen:

1. **Familienaam expliciet** in titel of body.
2. **Overlijdensdatum expliciet** (een ISO-converteerbare datum uit "op DD-MM-YYYY overleden" of, bij ontbreken, de berichtdatum als die als overlijdensdatum gelezen kan worden).

Beide expliciet: `confidence` 0.90. Alleen naam, datum onzeker of alleen berichtdatum als proxy: 0.70. De two-source-cap is hier niet van toepassing: een ABD-overlijdensbericht is de gezaghebbende bron voor het feit zelf. De resolver/apply sluit alleen mandaten van een eenduidig gematchte persoon; bij ambigue naam forceert de lage person-confidence sowieso review.

### Verzwarende factoren (verlagen, niet onder de floor)

- **"voorlopig", "tijdelijk", "vermoedelijk"** in de zin rond de benoeming: -0.05 op de berekende confidence.
- **Meerdere benoemingen** in één bericht waarvan onduidelijk welke persoon welke post krijgt: cap 0.70 op alle betrokken proposals, met expliciete note in `confidence_reasoning`.
- **Functie of niveau niet matchbaar** tegen bestaande post of organisatieonderdeel in `data/`: cap 0.85 (op het floor-niveau, niet eronder), met de nieuwe slug-suggestie expliciet in `confidence_reasoning`.

Verzwarende factoren mogen de confidence niet onder 0.85 brengen wanneer de basis 4-uit-4 expliciet was. De boete voor "voorlopig" is een nuance, geen blokkade.

### Voorbeelden

**Aart van der Vlist** (waarnemend pSG bij EZK, 20 april 2026):

- Familienaam ✓, functie "waarnemend pSG" ✓, organisatie "EZK" ✓, datum "20 april 2026" ✓.
- Geen `staatscourant_url`: cap 0.94.
- Geen verzwarende factoren.
- `confidence` = 0.92.

**Gerdine Keijzer-Baldé** (legt pSG-functie neer, 13 april 2026):

- Familienaam ✓, functie "pSG (voorlopig)" ✓, organisatie "EZK" ✓, datum "13 april 2026" ✓.
- Geen `staatscourant_url`: cap 0.94.
- "voorlopig" in de zin: -0.05.
- `confidence` = 0.89.

Beide vallen boven de 0.85-drempel en zijn auto-mergeable in `apply-staging`.

## Harde regels

1. **Quote-or-die.** `evidence_snippet` is een letterlijke substring van de input-payload (TWITTER_DESCRIPTION of BODY). Validator faalt anders.
2. **Two-source rule.** Een proposal uit alleen ABD-nieuws krijgt confidence-cap 0.94 (was 0.85 in v0.3.0). Met expliciete KB-link of staatscourant-URL in de tekst mag de cap naar 0.97. Boven 0.97 vereist een derde onafhankelijke bron.
3. **Staging-only.** Schrijf naar `data/_staging/abd-nieuws-YYYY-MM-DD.json`. Nooit direct naar `data/personen/`, `data/organisaties/` of `data/posten/`.
4. **Geen privé-data.** Alleen functie en naam en datum. Geen geboortedatum (alleen jaartal als die elders al vaststaat), geen contactgegevens, nooit BSN.
5. **Confidence per proposal** als float in [0, 1] met `confidence_reasoning` als string. Volg de regels in "Confidence-bepaling".
6. **Niveau-discipline.** `organization_chain` bevat alleen niveaus die letterlijk genoemd worden. Geen geraden DG-tussenlaag als het bericht die niet noemt. `organization_id` is het diepste niveau, niet het breedste.

## Voorbeeld

Zie `example_input.md` voor de relevante body-tekst van een ABD-nieuwsbericht (afdelingsbenoeming met vier niveaus), en `example_output.json` voor het bijbehorende proposal met `organization_chain`. De `evidence_snippet` in het output-bestand is letterlijk te vinden in de input.

## Aanroep vanuit Claude Code CLI

```bash
claude "Gebruik parse-abd-nieuws op _cache/abd-nieuws/marleen-heijster-afdelingshoofd-beleid-wonen-2026-05-09.html en schrijf naar data/_staging/abd-nieuws-2026-05-09.json"
```

## Status

Actief, versie 0.6.0. Vierde skill in Polder, na review-pr-diff, parse-staatscourant en parse-organogram. Nieuw in 0.6.0: `event_type: overlijden` met eigen confidence-schaal; sluit van rechtswege alle lopende mandaten via een aparte apply-sweep, zonder post/org. Nieuw in 0.4.0: confidence-vloer 0.85 bij vier expliciete kernfeiten, cap 0.94 zonder staatscourant_url, en expliciete verzwarende factor voor "voorlopig"-formuleringen. Nieuw in 0.3.0: `organization_chain` met expliciete niveau-keten en `organization_id` op diepste genoemde niveau.

## KRITIEK: OUTPUT ENKEL JSON, GEEN MARKDOWN

Je eindresultaat moet **zuiver JSON zijn, zonder markdown code blocks**. Dus NIET:

```
```json
[...]
```
```

Maar dit (start direct met `[`):

```
[...]
```

WICHTIG: Geen ` ```json `, geen ` ``` ` eromheen. Geen inleiding. Geen samenvatt. Alleen de array, eerste karakter `[`, laatste karakter `]`.
