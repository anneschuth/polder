# Gaten in @nldd/design-system (vanuit polder)

Polder bouwt zijn detailpagina's zoveel mogelijk op `@nldd/design-system`
(componenten, tokens, iconen). Een aantal patronen heeft geen component in het
design-system, dus daar valt polder terug op eigen CSS. Dit document beschrijft
die gevallen, waar polder ze nu zelf oplost, en hoe een design-system-component
eruit zou kunnen zien. Bedoeld als input voor feature-requests richting NLDD.

Geverifieerd tegen `@nldd/design-system@0.8.43`.

## 1. Term-waarde-lijst (definitielijst)

**Waar polder dit nu doet:** `.deflist` in `web/src/layouts/Base.astro`,
gebruikt door `Identifiers.astro`, `Contact.astro` en de
persoons-/organisatie-/post-detailpagina's voor blokken als geboortejaar,
geslacht, identifiers en contactvelden.

Het is een twee-koloms grid: term links (gedempt), waarde rechts, met
auto-fill naar meerdere kolommen op brede schermen en één kolom onder 32rem.

`nldd-description-cell` lijkt verwant maar stapelt titel boven omschrijving en
zit per definitie binnen een `nldd-list-item`. Voor een dichte
sleutel-waarde-weergave buiten een lijst is er niets.

**Voorstel:** een `nldd-description-list` met `nldd-description-list-item`
(term/value als slot of attribuut), een `columns`-attribuut voor de
auto-fill-breedte, en responsive inklap naar één kolom. Semantisch een
`<dl>`/`<dt>`/`<dd>`.

## 2. Tijd-geschaalde horizontale tijdlijn

**Waar polder dit nu doet:** `web/src/components/Timeline.astro` plus de `.tl*`
CSS in `Base.astro`. Het toont mandaten als gekleurde balken op een gedeelde
jaar-as, met een legenda per functietype.

`nldd-timeline-track-cell` bestaat, maar dat is een verticale stappenindicator
(lijn met dot, `step="past|future|none"`) bedoeld voor een lijst van
opeenvolgende stappen. Het plaatst niets proportioneel op een tijdas.

**Voorstel:** een component dat per rij een balk op een gedeelde tijdas
positioneert (begin/eind als datum), met optionele as-labels en een
legenda-slot. Dit is breder bruikbaar dan polder: projectplanningen,
ambtstermijnen, contracthistorie.

## 3. Proportionele verdeelbalk

**Waar polder dit nu doet:** `web/src/components/GeoCouncil.astro` met de
`.seatbar`-CSS in `Base.astro`. Een horizontale balk waarin elk segment een
fractie van het geheel beslaat (zetelverdeling per partij), met labels in het
segment en een legenda eronder.

Er is geen design-system-primitief voor een gestapelde proportionele staaf.

**Voorstel:** een `nldd-distribution-bar` die een lijst `{ label, value,
color }` neemt en segmenten proportioneel rendert, met toegankelijke
beschrijving (de waarden in tekst voor screenreaders) en een legenda-slot.
Generiek: stemverdelingen, budgetverdelingen, capaciteitsbezetting.

## 4. Datatabel

**Waar polder dit nu doet:** `<table class="data">` in de organisatie- en
post-detailpagina's en in `MandaatTimeline.astro`, voor mandaatoverzichten tot
enkele honderden rijen.

`nldd-list` + cells dekt korte relatielijsten goed en wordt daar nu ook voor
gebruikt. Voor een echte tabel met kolomkoppen, uitlijning per kolom en veel
rijen mist het de tabelsemantiek (`<table>`/`<th>` met scope) en is een lijst
van rijen minder geschikt voor scannen en sorteren. Polder houdt daarom bewust
`<table>` voor deze gevallen.

**Voorstel (lichter dan de andere drie):** een `nldd-table` /
`nldd-data-table` met kolomdefinities, headers en eventueel sorteren, zodat
tabeldata niet via een lijst-omweg hoeft.

## 5. Iconen voor navigatie-concepten ontbreken

**Waar polder dit tegenkomt:** de hoofdnavigatie in
`web/src/layouts/Base.astro`, waar `nldd-menu-bar-item` per item een `icon`
krijgt. De iconenset (`icon-registry.js` plus `icon-aliases.js`) heeft een
goede `person-2` voor Personen en `apartment-building` voor Organisaties, maar
geen passend icoon voor twee kernconcepten van deze dataset:

- **Post / functie / rol.** Geen badge-, insigne- of rol-icoon. Polder valt
  nu terug op `certificate` (alias voor diploma/license), wat de lading niet
  precies dekt.
- **Organogram / organisatiehiërarchie.** Geen org-chart-, boom-, sitemap-
  of hiërarchie-icoon. Polder valt nu terug op `rectangle-stack` als
  benadering.

De set bevat wel `person`, `person-2`, `person-badge-gear` en
`apartment-building`, dus de organisatie-/personen-kant is gedekt; het zijn
specifiek de relatie- en structuurconcepten die ontbreken.

**Voorstel:** voeg aan de iconenset een hiërarchie-/organogram-icoon toe
(een knoop-met-vertakkingen, vergelijkbaar met Material `account_tree` of een
sitemap-glyph) en een rol-/functie-icoon (een insigne of naamplaatje los van
het persoon-icoon). Beide zijn breder bruikbaar dan polder: elk
organisatie- of HR-domein heeft "structuur" en "functie" als concept.
