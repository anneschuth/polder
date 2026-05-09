# Security

## Kwetsbaarheden melden

Meld kwetsbaarheden bij voorkeur via een private security advisory op GitHub, of via mail aan anne.schuth@gmail.com. Geef het type kwetsbaarheid, de getroffen component en zo mogelijk een reproductie. Wacht met publiek delen tot er een fix is uitgerold.

Respons-window: best-effort binnen zeven dagen na ontvangst.

## Wat wel en niet in issues hoort

Polder bevat publieke functionaris-data, geen privé-data. In het publieke bug-tracker:

- Geen credentials, API-keys of tokens.
- Geen BSN, geboortedata buiten het jaar, privé-adressen of andere bijzondere persoonsgegevens.
- Geen interne logbestanden waarin secrets of persoonsgegevens kunnen zitten.

Voor takedown- of correctieverzoeken is er een apart issue-template; dat is geen security-kanaal maar een AVG-kanaal.

## Scope

Reports zijn welkom voor:

- De Polder-codebase (`src/`, `scripts/`, `schemas/`).
- De CI-workflows en de gepubliceerde data-artefacten.
- De Datasette-instance en het Frictionless Data Package.

Buiten scope: kwetsbaarheden in upstream-bronnen (ROO, KOOP, TK OData). Daarvoor: meld bij de bronhouder.
