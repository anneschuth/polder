# AVG-grenzen

Polder publiceert namen van overheidsfunctionarissen voor zover dat verenigbaar is met de AVG en de Nederlandse jurisprudentie. De juridische basis is de uitspraak van de Afdeling bestuursrechtspraak van de Raad van State van 31 januari 2018 (ECLI:NL:RVS:2018:314).

## ABRvS-doctrine 2018

De Afdeling oordeelde dat namen van ambtenaren openbaar mogen worden gemaakt als de medewerker "uit hoofde van functie in de openbaarheid treedt". Dat sluit beleidsmedewerkers, juristen, communicatieadviseurs en handhavers grotendeels uit, maar laat alle gekozen, benoemde of representatieve functies binnen scope vallen. De doctrine is sindsdien meermaals bevestigd, onder andere bij Wob/Woo-verzoeken om gespreksverslagen.

Het criterium is functioneel, niet hierarchisch. Een directeur Communicatie valt buiten scope, een burgemeester van een gemeente van 800 inwoners erbinnen.

## Groen, geel, rood

### Groen, publiceren standaard

Functionarissen die uit hoofde van functie in de openbaarheid treden:

- Bewindspersonen: ministers, staatssecretarissen
- Kamerleden Tweede Kamer en Eerste Kamer
- Commissarissen van de Koning, gedeputeerden, statenleden
- Burgemeesters, wethouders, raadsleden
- Dijkgraven, DB-leden waterschap, AB-leden waterschap
- Voorzitters Hoge Colleges van Staat (Raad van State, Algemene Rekenkamer, Nationale ombudsman)
- RvB-leden ZBO's
- ABD-Topmanagementgroep (SG, DG, plaatsvervangend SG, IG), classification `abd-tmg`
- Gemeentesecretarissen en provinciesecretarissen
- Rechters en raadsheren
- Gezaghebbers Caribisch Nederland (Bonaire, Sint Eustatius, Saba)
- Griffiers van vertegenwoordigende organen

Voor deze groep publiceren we naam, functie, organisatie, mandaatperiode, KB-referentie en publieke contactgegevens van de organisatie.

### Geel, alleen functioneel, alleen reeds publiek

Functionarissen waar publicatie kan, mits de organisatie zelf de naam al openbaar maakt (organogram, jaarverslag, persbericht). Geen privé-contactgegevens.

- ABD-directeuren (schaal 17-18, classification `abd-directeur`): directeur, plaatsvervangend directeur, programmadirecteur
- ABD-afdelingshoofden (schaal 15-16, classification `abd-afdelingshoofd`): afdelingshoofd, MT-lid op directieniveau, clusterhoofd
- ABD-projectleiders en kwartiermakers (tijdelijke posten, classification `abd-projectleider`)
- Secretarissen ZBO's en agentschappen
- Bestuurders en secretarissen gemeenschappelijke regelingen
- Officieren van justitie op procureur-generaal en hoofdofficier-niveau, classification `officier-van-justitie`

Voor deze groep slaan we de bron op (`sources[].url`) waaruit de naam reeds publiek blijkt. Verdwijnt de naam bij de organisatie, dan zetten we `valid_until` op die datum.

### Rood, niet doen

- Beleidsmedewerkers, juristen, communicatie, handhavers, dossierbehandelaars
- Privé-contactgegevens (woonadres, prive-telefoon, prive-email)
- Geboortedata met maand en dag (alleen jaar voor disambiguatie)
- BSN
- WNT-salarisdata buiten de wettelijke publicatieplicht
- Foto's
- Bijzondere persoonsgegevens (gezondheid, geloof, politieke voorkeur buiten openbare functie)

## Practicalia

- **DPIA en verwerkingsregister** bij start van het project. De drempel >1M persoonsgegevensrecords ligt boven onze schaal, maar een DPIA leggen we vast omdat het over publieke functionarissen gaat.
- **Takedown-flow** van maximaal 14 dagen via een GitHub-issue-template. Verzoek tot verwijdering of correctie wordt behandeld binnen 14 dagen, ook bij feitelijke onjuistheden.
- **Opt-out bij bedreigde ambtsdragers**, bijvoorbeeld op basis van Veilig Bestuur-meldingen. Op verzoek wordt de naam vervangen door `[op verzoek verwijderd]`, met `valid_until` op datum van verzoek. De record blijft bestaan met die ene wijziging.
- **Geen aggregatie met sociale-media-data**, geen profielbouw, geen verrijking met externe profilers. Polder is een register, geen monitoringstool.
