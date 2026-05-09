# Bronnen

Polder bouwt op publieke registers van de Nederlandse overheid plus Wikidata. Deterministische fetchers waar de bron gestructureerd is, LLM-skills waar parsing nodig is.

## Primaire feeds

| Bron | Endpoint | Formaat | Update | Licentie | Dekking |
|---|---|---|---|---|---|
| ROO | `organisaties.overheid.nl`, `api-organisaties.overheid.nl`, dagelijkse `exportOO.xml` | XML/CSV/REST/SRU | dagelijks | CC0 | alle organisatietypes, bestuurders tot SG/DG/burgemeester/dijkgraaf |
| TOOI | `standaarden.overheid.nl/tooi`, URI's onder `identifier.overheid.nl/tooi/id/` | SKOS/RDF | gestaag | CC0 | URI-stelsel voor alle organisatietypes |
| TK OData | `gegevensmagazijn.tweedekamer.nl/OData/v4/2.0/` | OData v4 + Atom SyncFeed | near-realtime | open | TK-personen, fracties, commissies, vanaf 2008-09-01 |
| Logius COR | `oinregister.logius.nl`, `portaal.digikoppeling.nl/registers/corApi/` | REST | gestaag | open | OIN per organisatie |
| KOOP SRU | `repository.overheid.nl/sru` | SRU/XML | live | open | Staatscourant, KB's, sinds 2009 |
| Wikidata | `query.wikidata.org/sparql` | SPARQL | live | CC0 | Q-id crosswalks |
| Allmanak (OSF) | `rest-api.allmanak.nl/v0/` | PostgREST | gestaag | open | secundaire bron, eigen `systemid` |
| Open Raadsinformatie | `api.openraadsinformatie.nl/v1/elastic/` | Elastic + Popolo ODS | gestaag | open | 265+ gemeenten, raadsleden |
| Kiesraad | `data.overheid.nl` (zoek op authority Kiesraad) | CSV/XML | per verkiezing | open | uitslagen, kandidaatlijsten |
| Algemene Rekenkamer RWT | `www.rekenkamer.nl/onderwerpen/rwt-register` | HTML | jaarlijks | gebruik | RWT-lijst |
| Rijksfinanciën ZBO/Agentschap | jaarlijkse Excel/CSV via Min FIN | spreadsheet | jaarlijks | open | overzicht ZBO/agentschap |

## Per bron

### ROO

KOOP's Register Overheidsorganisaties. Dagelijkse XML-dump op `organisaties.overheid.nl`. Fetcher in `src/fetchers/roo.py`. CC0-gelicenseerd, dus directe import in `data/organisaties/` zonder licentie-issues. Dekt organisaties tot directieniveau, bestuurders tot SG/DG/burgemeester/dijkgraaf. Geen termijn-historie en geen ABD-managers onder de TMG.

### TOOI

Het URI-stelsel voor overheidsorganisaties op `identifier.overheid.nl/tooi/`. SKOS/RDF, stabiel. Gebruikt voor `identifiers.tooi` op elke Organisatie. Update zelden, mutaties via KOOP-issuetracker.

### TK OData

`tkapi`-library wrapt de OData-feed. Dekt Persoon, PersoonNevenfunctie, PersoonLoopbaan, Fractie, FractieZetel, Commissie, CommissieZetel. Near-realtime synchronisatie via Atom SyncFeed. TK-leden vanaf 2008-09-01.

### Logius COR

OIN-register voor alle organisaties die via Digikoppeling met de overheid uitwisselen. Match op naam plus KvK om OIN aan ROO-record te koppelen. Niet-publieke organisaties met OIN (commerciële Digikoppeling-deelnemers) negeren.

### KOOP SRU

Search/Retrieve via URL op de hele KOOP-repository. Gebruikt voor Staatscourant-KB's. Levert XML met vrije tekst, dus parsing vereist LLM (`parse-staatscourant`-skill).

### Wikidata

SPARQL-endpoint voor Q-id crosswalks. Query alle organisaties met `P31` ministerie/gemeente/provincie/waterschap, plus alle huidige TK-leden. CC0.

### Allmanak (OSF)

PostgREST API van Open State Foundation. Secundaire bron, primair voor cross-validation en backlink. Eigen `systemid`-veld behouden voor link-out.

### Open Raadsinformatie

Elastic-API met Popolo-export. 265+ gemeenten, dekt raadsleden, wethouders en raadsbesluiten. Voor raadsleden en wethouders fetchen; collegebesluiten buiten scope MVP.

### Kiesraad

Per verkiezing aparte CSV/XML op `data.overheid.nl`. Voor kandidaatlijsten en uitslagen. Niet relevant voor de hoofdgraaf, wel voor Event-records.

### Algemene Rekenkamer RWT

Lijst van Rechtspersonen met een Wettelijke Taak. Jaarlijks ge-update HTML-pagina. Scrapen, mappen op naam aan ROO.

### Rijksfinanciën ZBO/Agentschap

Min FIN publiceert jaarlijks een Excel met financiële kengetallen per ZBO en agentschap. Bevat dekkingscheck voor de eigen lijst.

## LLM-bronnen

Parsing nodig, hoog risico op hallucinatie zonder guardrails (quote-or-die, two-source rule, confidence-gated auto-merge).

| Bron | Type | Waarvoor | Risico |
|---|---|---|---|
| Staatscourant via KOOP SRU | XML met vrije tekst in body | benoemingen-KB's parsen | gemiddeld, met two-source rule mitigeerbaar |
| Eerste Kamer pagina's | HTML scrape | EK-leden, commissies | laag, structuur is consistent |
| Rijksoverheid.nl organogrammen | HTML + soms PDF/PNG | ABD-management onder TMG | gemiddeld, structuur varieert per ministerie |
| ABD-jaarverslagen | PDF | ABD-populatie aggregaten | laag (alleen validation, geen brondata) |

## Externe links (geen ingestion, alleen crosswalk)

- **berthub.eu/tkconv (OpenTK)**: deep-link template `https://berthub.eu/tkconv/persoon.html?nummer={tk_persoon_id}`. Voor elk TK-lid in `personen/` automatisch genereerbaar.
- **Wikidata items**: `https://www.wikidata.org/wiki/{wikidata}`.
- **Allmanak**: `https://www.allmanak.nl/cat/{cat_id}/...` waar mappable.
