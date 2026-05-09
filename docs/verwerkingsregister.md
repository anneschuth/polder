# Verwerkingsregister

Verwerkingsregister volgens art. 30 AVG. Wordt bijgewerkt bij elke uitbreiding van scope en jaarlijks herzien naast de DPIA.

## Verwerkingen

| Veld | Verwerking "Polder dataset" |
|---|---|
| Verwerkingsactiviteit | Aanleggen en publiceren van git-versioneerd register van Nederlandse overheidsfunctionarissen |
| Doel | Openbaar register voor onderzoek, journalistiek en civic tech |
| Categorieën persoonsgegevens | Naam, functie, organisatie, mandaatperiode, KB-referentie, geboortejaar (alleen jaar), gender (m/f/x). Zie `docs/dpia.md` sectie 3 |
| Categorieën betrokkenen | Bewindspersonen, Kamerleden, statenleden, raadsleden, burgemeesters, wethouders, gedeputeerden, dijkgraven, DB- en AB-leden waterschap, ABD-TMG en management op directieniveau, leden Hoge Colleges van Staat, rechters, RvB-leden ZBO's, gezaghebbers Caribisch Nederland, griffiers. Zie `docs/dpia.md` sectie 4 |
| Categorieën ontvangers | Publiek. De dataset is gelicenseerd onder CC0 en wordt gehost op GitHub; eventueel ook gepubliceerd via Datasette of vergelijkbare leesinterface |
| Doorgifte buiten EU | GitHub Inc (Verenigde Staten) als hostingpartij. Doorgifte valt onder het Adequaatheidsbesluit EU-VS Data Privacy Framework (2023). Geen andere doorgifte buiten de EER |
| Bewaartermijn | Onbepaald. Polder is een historisch register; records worden niet gewist bij einde mandaat. In plaats daarvan wordt `valid_until` gezet |
| Beveiligingsmaatregelen | 2FA op GitHub-account, branch protection op `main`, geen credentials in git, signed commits optioneel, pre-commit hooks tegen accidentele lekken en grote bestanden |

## Verwerkingsverantwoordelijke

Anne Schuth, persoonlijk project, anne.schuth@gmail.com.

## Verwerker

GitHub Inc, hosting van de repository. Geen sub-verwerkers. Bij overdracht aan een openbare organisatie wordt het register herzien.

## Wijzigingen

Nieuwe rijen worden toegevoegd wanneer een aanvullende verwerking ontstaat (bijvoorbeeld een aparte API-laag, een mailinglijst voor gebruikers, een geautomatiseerde scraper met eigen verwerkingsdoel). Bestaande rijen worden bijgewerkt bij elke wijziging in scope, ontvangers of bewaartermijn.
