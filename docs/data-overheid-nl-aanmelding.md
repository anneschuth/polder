# Aanmelding bij data.overheid.nl

Polder hoort thuis in de nationale open-data-catalogus. Dit document beschrijft het aanmeldproces.

## Wat is data.overheid.nl

[data.overheid.nl](https://data.overheid.nl/) is de nationale catalogus van open overheidsdata, beheerd door Logius namens BZK. De backend is CKAN; metadata volgt het profiel DCAT-AP-NL. De catalogus is harvest-bron voor het Europese data.europa.eu.

## Voorwaarden

- Open licentie. Polder voldoet met CC0 1.0 op de data en EUPL-1.2 op de code.
- Metadata in DCAT-AP-NL formaat (afgeleid van DCAT-AP 2.0.1).
- Stable URL voor de dataset-distributies. Voor Polder: GitHub release-assets plus de Datasette-instantie.
- Contactpunt voor vragen en takedown-verzoeken.

## Aanmeldproces

1. Account aanmaken via `datacommunicatie@overheid.nl`. Mailadres moet aantoonbaar gekoppeld zijn aan publisher (in dit geval Anne Schuth als individuele bijdrager, met verwijzing naar het GitHub-project).
2. Na bevestiging inloggen op `data.overheid.nl/auth/login`.
3. Onder Mijn datasets een nieuwe dataset aanmaken. De UI accepteert handmatige invoer of een DCAT-AP-NL JSON-LD upload.
4. Polder levert het JSON-LD bestand uit `datapackage.json` (zie sjabloon onder).
5. Beheerder reviewt binnen 5 werkdagen en publiceert.

## DCAT-AP-NL velden

| Veld | Waarde |
|---|---|
| `dct:title` | Polder |
| `dct:description` | Git-versioned, CC0-gelicenseerde dataset van Nederlandse overheidsorganisaties, posten, personen en mandaten |
| `dcat:theme` | Bestuur en organisatie |
| `dct:publisher` | Anne Schuth |
| `dcat:contactPoint` | anne.schuth@gmail.com |
| `dct:license` | https://creativecommons.org/publicdomain/zero/1.0/ |
| `dct:accrualPeriodicity` | http://publications.europa.eu/resource/authority/frequency/DAILY |
| `dcat:distribution` | YAML, CSV, SQLite, JSON via Datasette |

## Update-frequentie

Dagelijks. De `daily-update` GitHub Actions workflow opent elke ochtend een PR met diff. Bij merge naar `main` triggert `publish.yml` een nieuwe build die als release-asset op GitHub verschijnt en naar de Datasette-instantie gaat. De `accessURL` in DCAT verwijst naar de latest-release; permanente versies via release-tags.

## Voorbeeld DCAT-AP-NL JSON-LD

```json
{
  "@context": "https://data.overheid.nl/dcat-ap-nl.jsonld",
  "@type": "dcat:Dataset",
  "dct:identifier": "https://github.com/anneschuth/polder",
  "dct:title": "Polder",
  "dct:description": "Git-versioned, CC0-gelicenseerde dataset van Nederlandse overheidsorganisaties, posten, personen en mandaten. Volgt een Popolo-achtig graafmodel.",
  "dcat:keyword": ["overheid", "open-data", "nederland", "popolo", "mandaten"],
  "dcat:theme": ["http://standaarden.overheid.nl/owms/terms/Bestuur_en_organisatie"],
  "dct:publisher": {
    "@type": "foaf:Person",
    "foaf:name": "Anne Schuth"
  },
  "dcat:contactPoint": {
    "@type": "vcard:Individual",
    "vcard:fn": "Anne Schuth",
    "vcard:hasEmail": "mailto:anne.schuth@gmail.com"
  },
  "dct:license": "https://creativecommons.org/publicdomain/zero/1.0/",
  "dct:accrualPeriodicity": "http://publications.europa.eu/resource/authority/frequency/DAILY",
  "dct:issued": "2026-05-09",
  "dcat:distribution": [
    {
      "@type": "dcat:Distribution",
      "dct:title": "Frictionless Data Package",
      "dcat:accessURL": "https://github.com/anneschuth/polder/releases/latest/download/datapackage.json",
      "dcat:mediaType": "application/json",
      "dct:format": "JSON"
    },
    {
      "@type": "dcat:Distribution",
      "dct:title": "SQLite",
      "dcat:accessURL": "https://github.com/anneschuth/polder/releases/latest/download/polder.db",
      "dcat:mediaType": "application/vnd.sqlite3",
      "dct:format": "SQLite"
    },
    {
      "@type": "dcat:Distribution",
      "dct:title": "Datasette",
      "dcat:accessURL": "https://datasette.polder.dev/",
      "dcat:mediaType": "text/html",
      "dct:format": "API"
    }
  ]
}
```

## Sjabloon-mail

Onderwerp: Aanmelding open dataset Polder

```
Beste team data.overheid.nl,

Ik wil graag de dataset Polder aanmelden voor opname in de catalogus.

Polder is een git-versioned, CC0-gelicenseerde dataset van Nederlandse
overheidsorganisaties, posten, personen en mandaten, met dagelijkse updates
via een geautomatiseerde pipeline. Bronnen zijn onder andere ROO, TOOI,
TK OData, Logius COR, Wikidata en KOOP.

- Repo: https://github.com/anneschuth/polder
- Distributies: YAML (git), CSV en SQLite (release-assets), Datasette (live)
- Licentie: CC0 1.0
- Updatefrequentie: dagelijks
- Contact: anne.schuth@gmail.com

In de bijlage een DCAT-AP-NL JSON-LD beschrijving. Graag hoor ik welk account
ik kan gebruiken om de dataset zelf te onderhouden in jullie portaal.

Met vriendelijke groet,
Anne Schuth
```
