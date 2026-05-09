# DPIA Polder

Data Protection Impact Assessment voor Polder, een open dataset van Nederlandse overheidsfunctionarissen. Deze DPIA is opgesteld op grond van art. 35 AVG en wordt jaarlijks herbeoordeeld.

## 1. Doelstelling van de verwerking

Polder houdt een git-versioneerd, CC0-gelicenseerd register bij van personen die uit hoofde van een publieke functie in de openbaarheid treden. Het doel is drieledig: onderzoek (politicologie, bestuurskunde, geschiedenis), journalistiek (controle op besluitvorming, verantwoording, lobbying) en civic tech (apps, visualisaties, koppelingen met andere open data). De dataset bevat historische mandaatperiodes en is daarmee bruikbaar voor langetermijnanalyse die in losse departementale registers niet mogelijk is.

## 2. Verwerkingsverantwoordelijke

Anne Schuth, persoonlijk project, anne.schuth@gmail.com. Geen werknemers, geen verwerkers anders dan GitHub als hosting. Bij eventuele overdracht aan een openbare organisatie wordt deze DPIA herzien en wordt een DPO-consultatie verplicht.

## 3. Soorten persoonsgegevens

Wat we vastleggen:

- naam (voornaam, tussenvoegsel, achternaam, optioneel roepnaam)
- functie en organisatie
- mandaatperiode (`valid_from`, `valid_until`)
- KB-referentie of bronverwijzing
- geboortejaar (alleen jaar, voor disambiguatie tussen naamgenoten)
- gender als `m`, `f` of `x`

Wat we niet vastleggen: BSN, woonadres, privé-telefoonnummers, privé-e-mailadres, foto's, bijzondere persoonsgegevens (gezondheid, geloof, politieke voorkeur buiten de openbare functie), volledige geboortedata, salarisgegevens buiten de wettelijke WNT-publicatieplicht.

## 4. Categorieën betrokkenen

Binnen scope:

- bewindspersonen (ministers, staatssecretarissen)
- Kamerleden Tweede Kamer en Eerste Kamer
- Commissarissen van de Koning, gedeputeerden, statenleden
- burgemeesters, wethouders, raadsleden
- dijkgraven, DB- en AB-leden waterschap
- ABD-Topmanagementgroep en management op directieniveau
- voorzitters en leden Hoge Colleges van Staat
- rechters en raadsheren
- RvB-leden ZBO's
- gezaghebbers Caribisch Nederland
- griffiers van vertegenwoordigende organen

Geschat aantal records bij volledige dekking: tussen 50.000 en 150.000, inclusief historische posten vanaf circa 1945.

## 5. Rechtmatige grondslag

Art. 6 lid 1 sub e AVG, taak van algemeen belang. Onderbouwing leunt op de uitspraak van de Afdeling bestuursrechtspraak van de Raad van State van 31 januari 2018 (ECLI:NL:RVS:2018:314): namen mogen openbaar als de medewerker uit hoofde van functie in de openbaarheid treedt. Aanvullende grondslagen: openbaarheid van bestuur (Woo), controleerbaarheid van het openbaar bestuur, en de publicatieplicht voor benoemingen via Koninklijk Besluit.

Voor de gele categorie (ABD-managers, ZBO-secretarissen) geldt een aanvullende voorwaarde: de naam moet reeds publiek zijn via de organisatie zelf. Verdwijnt de naam bij de bron, dan wordt `valid_until` gezet.

## 6. Doelbinding en proportionaliteit

De groen/geel/rood-classificatie uit `docs/avg-grenzen.md` is bindend. Rood-niveau (beleidsmedewerkers, juristen, communicatie, handhavers, dossierbehandelaars) wordt nooit gepubliceerd, ook niet wanneer een individuele bron de naam wel noemt. Velden die niet strikt nodig zijn voor identificatie en historische reconstructie worden niet opgenomen, zelfs als ze beschikbaar zijn.

## 7. Bewaartermijn

Onbepaald. Polder is een historisch register; verwijdering van afgelopen mandaten zou het doel ondergraven. Records worden niet gewist bij einde mandaat. In plaats daarvan wordt `valid_until` gezet op de einddatum. Correcties gebeuren door een nieuwe record toe te voegen met aansluitende `valid_from`, plus `valid_until` op de oude record.

## 8. Beveiligingsmaatregelen

Alle data is publiek onder CC0; vertrouwelijkheid is geen doel. Wel relevant:

- geen credentials, tokens of API-sleutels in git
- GitHub-account met 2FA verplicht
- branch protection op `main`, geen directe pushes
- signed commits optioneel maar aanbevolen
- pre-commit hooks tegen accidentele lekken (large files, merge conflict markers)

## 9. Rechten van betrokkenen

- **Inzage**: alle data is publiek doorzoekbaar via GitHub en eventueel Datasette.
- **Correctie**: via PR (`data-bug` issue-template) of via takedown-flow bij meer ingrijpende correcties.
- **Verwijdering**: alleen bij bedreigde ambtsdragers (bijvoorbeeld Veilig Bestuur-meldingen) of feitelijke onjuistheid. Standaard wordt de naam vervangen door `[op verzoek verwijderd]` en blijft de record bestaan; volledige verwijdering vindt plaats bij rechtelijk bevel.
- **Bezwaar**: via takedown-flow, beoordeling binnen 14 dagen.

Zie `docs/takedown-procedure.md` voor de operationele uitwerking.

## 10. Risico-analyse en mitigaties

| Risico | Kans | Impact | Mitigatie |
|---|---|---|---|
| Identificatie van familieleden via naam-disambiguatie | laag | midden | Alleen geboortejaar opslaan, geen volledige geboortedatum, geen woonplaats |
| Aggregatie met andere bronnen tot persoonlijk profiel | midden | midden | AVG-grenzen-doctrine bindend: geen sociale-media-data, geen profielbouw, geen monitoringfunctionaliteit |
| Bedreiging van ambtsdragers door publicatie | laag | hoog | Opt-out via Veilig Bestuur-route, naam vervangen, record behouden |
| Foutieve toeschrijving (verkeerde Jan de Vries) | midden | midden | Geboortejaar voor disambiguatie, KB-referentie of bronverwijzing per record, correctie via PR |
| Datalek door GitHub-incident | laag | laag | Data is sowieso publiek; geen extra impact |
| Opname van rood-categorie door scraping-fout | midden | hoog | Schema valideert tegen toegestane functietypes, pre-commit hook draait `polder-validate` |
| Verouderde data wordt als actueel gelezen | hoog | laag | `valid_until` verplicht zodra mandaat eindigt, downstream-tooling moet dit respecteren |

## 11. DPO-consultatie

Niet vereist voor een persoonlijk project zonder werknemers en zonder grootschalige bijzondere persoonsgegevens. Bij donatie of overdracht aan een openbare organisatie wordt DPO-consultatie verplicht gesteld als voorwaarde, naast hernieuwde DPIA en herbeoordeling van de grondslag.

## 12. Periodieke herbeoordeling

Jaarlijks, in januari. Daarnaast bij:

- elke nieuwe categorie betrokkenen (uitbreiding van scope)
- elke rechterlijke uitspraak die de ABRvS-doctrine raakt
- elke wijziging in de Woo of AVG-implementatie
- substantiële groei voorbij 150.000 records
- klacht bij de Autoriteit Persoonsgegevens

De herbeoordeling wordt gedocumenteerd als wijziging op dit bestand, inclusief datum en ondertekenaar.
