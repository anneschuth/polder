# Voorbeeld-organogram (tekstuele beschrijving)

Dit bestand staat in de repo in plaats van een echte PNG of PDF, omdat
afbeeldingen van organogrammen al snel meerdere MB's beslaan en niet thuishoren
in een git-repository. Het beschrijft een fictief organogram van het Ministerie
van BZK, peildatum april 2026, dat dient als referentie voor `example_output.json`.

Bron-URL: `https://www.rijksoverheid.nl/.../organogram-bzk-2026-04.pdf`

## Pagina 1

Bovenaan de pagina staat een box met de tekst:

  "Secretaris-Generaal
   drs. M. de Boer"

Onder deze box lopen vier verbindingslijnen naar een rij van vier boxen, links
naar rechts:

1. "DG Bestuur en Wonen
    mr. K. Jansen"
2. "DG Overheidsorganisatie
    drs. T. Smit"
3. "DG Digitalisering en Overheidsorganisatie
    ir. L. de Vries"
4. "plv. SG
    mw. drs. A. el Idrissi"

Onder de eerste box ("DG Bestuur en Wonen") hangen drie kleinere boxen:

- "Directie Wonen
   drs. P. van Dijk"
- "Directie Bestuur en Financien
   (vacature)"
- "Programmadirectie Volkshuisvesting
   drs. R. Nguyen"

Onderaan de pagina staat een rij boxen met titels als "Afdeling Beleid",
"Afdeling Communicatie" en "Cluster Juridische Zaken". Deze afdelingen vallen op
rood-AVG-niveau (beleidsmedewerkers, communicatiemedewerkers, juristen) en
worden NIET geextract.

## Pagina 2

Een aparte rij boxen onder "DG Overheidsorganisatie":

- "Afdelingshoofd ICT-Beleid
   ing. S. Bakker"
- "Projectleider Digitale Identiteit
   drs. F. Hassan"

Beide vallen onder geel-AVG en worden wel geextract.
