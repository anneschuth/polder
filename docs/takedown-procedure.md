# Takedown-procedure

Polder behandelt verzoeken tot verwijdering, correctie of opt-out binnen 14 dagen. Verzoeken komen binnen via een GitHub-issue op basis van het template `takedown.yml` in de repository. Wie geen GitHub-account heeft, mailt naar anne.schuth@gmail.com en het verzoek wordt namens de verzoeker als issue aangemaakt zonder persoonlijke gegevens.

## Soorten verzoeken

### Verwijdering wegens onjuistheid

Een record bevat een fout (verkeerde mandaatperiode, verkeerde organisatie, verkeerde naamgenoot). Behandeling: de oude record krijgt `valid_until` op de datum van vondst, en een nieuwe record met de correcte waarden wordt toegevoegd met aansluitende `valid_from`. De foute record blijft zichtbaar als historische correctie. Dit voorkomt dat downstream-gebruikers stilzwijgend andere conclusies trekken op gecorrigeerde data zonder de wijziging te kennen.

### Correctie van feitelijke fouten

Spellingsfouten, ontbrekende KB-referentie, verkeerde geboortejaar-disambiguatie. Behandeling: een PR via het `data-bug` issue-template, met bron. Geen `valid_until` nodig wanneer het om een eenduidige correctie van een typefout gaat.

### Opt-out bij bedreigde ambtsdragers

Verzoek op basis van Veilig Bestuur-meldingen of vergelijkbare gemotiveerde dreiging (rechtbankvonnis, politie-advies, gestaakte vervolging tegen bedreiger). Behandeling: de naam wordt vervangen door `[op verzoek verwijderd]`, `valid_until` wordt gezet op datum van verzoek, en de record blijft bestaan zodat historische analyses (aantallen, doorstroom, partijaanhankelijkheid) intact blijven. De functie en organisatie blijven publiek; enkel de naam verdwijnt.

## Beoordelingscriteria

Bij elk verzoek wegen drie factoren:

1. **Wettelijke grondslag van publicatie**. Past de functie binnen de groene of gele categorie van `docs/avg-grenzen.md`? Bij rood is publicatie sowieso niet toegestaan en wordt de record direct verwijderd.
2. **Openbaarheid van de functie**. Treedt de persoon uit hoofde van functie in de openbaarheid? Een statenlid wel, een dossierbehandelaar niet.
3. **Mate van bedreiging**. Onderbouwd verzoek (politie, advocaat, Veilig Bestuur-melding) leidt tot opt-out, ook bij groene functies.

## Escalatie

Wanneer de verzoeker niet akkoord gaat met de beoordeling: doorverwijzing naar de Autoriteit Persoonsgegevens. Polder werkt mee aan AP-onderzoek, levert binnen de wettelijke termijn alle gevraagde documentatie (DPIA, verwerkingsregister, log van eerdere verzoeken) en past de uitkomst van het AP-besluit toe.

## Logging

Elk verzoek krijgt een GitHub-issue dat 5 jaar publiek bewaard blijft. In het issue staat het besluit (gehonoreerd, afgewezen, gedeeltelijk gehonoreerd), de gronden en de datum. De naam van de verzoeker en de naam van de betrokkene worden niet in het issue genoemd. Het issue verwijst naar de commit waarin de wijziging is doorgevoerd. Hiermee is de besluitvorming controleerbaar zonder dat het takedown-register zelf een nieuwe vindplaats voor persoonsgegevens wordt.

## Termijn

14 dagen vanaf binnenkomst. Bij bedreigingsverzoek met onderbouwing wordt de naam binnen 24 uur vervangen door `[op verzoek verwijderd]`, vooruitlopend op de volledige beoordeling. De definitieve verwerking volgt binnen 14 dagen.

## Verantwoording

Aantallen en typen verzoeken worden jaarlijks geaggregeerd gepubliceerd in het release-overzicht van Polder, zonder herleidbare details. Dit dient de transparantie van het project en sluit aan bij de jaarlijkse DPIA-herbeoordeling.
