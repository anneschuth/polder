# Datamodel

Polder volgt een Popolo-achtig graafmodel met vijf classes: Organization, Person, Post, Mandaat en Event. Source-of-truth is YAML in `data/`, gevalideerd tegen JSON Schema 2020-12 in `schemas/`.

## Organization

Een overheidsorganisatie. Ministeries, ZBO's, agentschappen, RWT's, hoge colleges, gemeenten, provincies, waterschappen, gemeenschappelijke regelingen, adviescolleges, inspecties, rechterlijke macht, politie/OM, Caribisch Nederland en organisatieonderdelen (directies, divisies, afdelingen, bureaus binnen een ministerie of uitvoerende dienst).

Velden: `id`, `type`, `identifiers` (oin, tooi, wikidata, roo_id, kvk), `classification`, `parent_id`, `names[]` met `valid_from`, `contact`, `valid_from`, `valid_until`, `sources[]`.

```yaml
id: org:min-bzk
type: ministerie
identifiers:
  oin: "00000001003214345000"
  tooi: https://identifier.overheid.nl/tooi/id/ministerie/mnre1034
  wikidata: Q1727053
  roo_id: "9632"
classification: ministerie
parent_id: org:rijksoverheid
names:
  - value: Ministerie van Binnenlandse Zaken en Koninkrijksrelaties
    abbr: BZK
    valid_from: 2010-10-14
contact:
  website: https://www.rijksoverheid.nl/ministeries/ministerie-van-binnenlandse-zaken-en-koninkrijksrelaties
  bezoekadres: Turfmarkt 147, 2511 DP Den Haag
valid_from: 1798-08-12
valid_until: null
sources:
  - id: roo
    url: https://organisaties.overheid.nl/9632/
    retrieved: 2026-05-08
    fields: [names, classification, parent_id, contact]
```

### Organisatieonderdeel

ROO levert ~1650 directies, divisies, afdelingen en bureaus binnen ministeries, agentschappen en uitvoerende diensten. We modelleren deze als top-level Organization-records met `type: organisatieonderdeel` en een `parent_id` naar de enclosing organisatie. Geen aparte class, geen embedded sub-record: dat zou Person- en Mandaat-relaties onnodig ingewikkeld maken (een ABD-directeur zit op een directie, niet op het ministerie zelf).

De parent komt rechtstreeks uit de XML-ancestry in `exportOO.xml` waar onderdelen als geneste `<organisatie>` onder hun moeder staan. Records landen in `data/organisaties/organisatieonderdelen/`. Onderdelen kunnen zelf weer onderdelen onder zich hebben (een afdeling binnen een directie); de boom is meerlagig.

```yaml
id: org:onderdeel-dpmo
type: organisatieonderdeel
identifiers:
  roo_id: "29754690"
classification: organisatieonderdeel
parent_id: org:agentschap-dji
names:
  - value: Directie Personeel, Management en Organisatie-ontwikkeling
    abbr: DPMO
    valid_from: 1900-01-01
valid_from: 1900-01-01
valid_until: null
sources:
  - id: roo
    url: https://organisaties.overheid.nl/29754690/
    retrieved: 2026-05-09
```

## Person

Een natuurlijk persoon met één of meer mandaten. Mandaten staan inline (zie hieronder), niet in een aparte folder, tot files >50KB worden.

Velden: `id`, `identifiers` (wikidata, tk_persoon_id, abd_id), `name` (full, family, given, initials, honorifics_pre), `birth.year`, `gender`, `mandaten[]`.

```yaml
id: person:jansen-jp-1965
identifiers:
  wikidata: Q12345678
  tk_persoon_id: null
name:
  full: Jan Pieter Jansen
  family: Jansen
  given: Jan Pieter
  initials: J.P.
  honorifics_pre: [dr., ir.]
birth: { year: 1965 }
gender: m
mandaten:
  - id: 01HXY9ABCDEFGHJKMNPQRSTVWX
    organization_id: org:min-bzk
    post_id: post:sg-min-bzk
    role: Secretaris-Generaal
    start_date: 2022-09-01
    end_date: null
    appointment:
      decision: KB 2022-08-15
      staatscourant_url: https://zoek.officielebekendmakingen.nl/stcrt-2022-...
    sources:
      - { id: staatscourant, url: ..., retrieved: 2022-08-20 }
      - { id: roo, url: https://organisaties.overheid.nl/9632/, retrieved: 2026-05-08 }
```

## Post

Een functie los van de zittende persoon. Eén Post kan over de tijd door meerdere personen worden vervuld.

Velden: `id`, `organization_id`, `label`, `classification`, `seat_count`, `valid_from`, `valid_until`.

`classification` kent de volgende waarden:

| Waarde | Niveau | AVG |
|---|---|---|
| `bewindspersoon` | minister, staatssecretaris | groen |
| `abd-tmg` | SG, DG, plv-SG, IG (schaal 19+) | groen |
| `abd-directeur` | directeur, plv-directeur, programmadirecteur (schaal 17-18) | geel |
| `abd-afdelingshoofd` | afdelingshoofd, MT-lid, clusterhoofd (schaal 15-16) | geel |
| `abd-projectleider` | projectleider, kwartiermaker (tijdelijk) | geel |
| `gemeentesecretaris`, `provinciesecretaris` | ambtelijke top decentraal | geel |
| `kamerlid`, `statenlid`, `raadslid` | gekozen volksvertegenwoordigers | groen |
| `commissaris-vd-koning`, `gedeputeerde`, `wethouder`, `burgemeester` | bestuurders gekozen of benoemd | groen |
| `dijkgraaf`, `db-waterschap`, `ab-waterschap` | waterschapsbestuur | groen |
| `voorzitter-hcs`, `lid-hcs` | Hoge Colleges van Staat | groen |
| `rvb-zbo` | RvB-leden ZBO's | groen |
| `rechter`, `officier-van-justitie` | rechterlijke macht en OM-top | groen |
| `gezaghebber`, `griffier`, `overig` | overig publiek | groen of geel |

```yaml
id: post:sg-min-bzk
organization_id: org:min-bzk
label: Secretaris-Generaal
classification: abd-tmg
seat_count: 1
valid_from: 1962-01-01
valid_until: null
```

## Mandaat

De relatie persoon × post × periode. Mandaten staan inline in een Person-record, met UUIDv7 als id voor lexicografische sortering op tijd.

Velden: `id` (UUIDv7), `organization_id`, `post_id`, `role`, `start_date`, `end_date`, `appointment.decision`, `appointment.staatscourant_url`, `sources[]`.

Voorbeeld zie de `mandaten[]`-entry hierboven onder Person.

## Event

Optioneel. Verkiezingen, formaties, herindelingen. Niet verplicht voor MVP.

```yaml
id: event:tk-verkiezing-2025
type: verkiezing
date: 2025-10-29
organizations: [org:tweede-kamer]
sources:
  - { id: kiesraad, url: https://www.kiesraad.nl/..., retrieved: 2025-10-30 }
```

## Identifier-strategie

- **Organisaties**: OIN als beschikbaar via Logius COR, anders eigen slug `org:gemeente-utrecht`. Altijd TOOI-URI, Wikidata-Q en KvK/RSIN waar bekend.
- **Personen**: stable slug `person:jansen-jp-1965` (familienaam + initialen + geboortejaar voor disambiguatie). Plus Wikidata-Q en TK-persoonId.
- **Posten**: slug `post:sg-min-bzk` of `post:burgemeester-utrecht`.
- **Mandaten**: UUIDv7, lexicografisch sorteerbaar op tijd, met expliciete `start_date` en `end_date`.

## Validatie-regels

Afgedwongen in `src/validate.py`:

1. Elk YAML-record valideert tegen het bijbehorende JSON Schema met `additionalProperties: false`.
2. Alle `*_id` referenties resolven naar een bestaand record.
3. Geen overlappende mandaten op een single-seat Post (waarschuwing, geen error, vanwege interim-periodes).
4. Geen records met `valid_until` in de toekomst zonder bron.
5. Elk record heeft minstens één entry in `sources[]`.
6. Geboortedata alleen als jaartal, geen maand of dag.
7. Geen BSN-achtige patterns in tekstvelden, regex-check op 9-cijferige reeksen.
