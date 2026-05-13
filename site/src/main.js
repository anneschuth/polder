import { loadJSON } from "./fetcher.js";
import { createChart } from "./pack.js";

const container = document.getElementById("chart");
const breadcrumbEl = document.getElementById("breadcrumb");

(async function bootstrap() {
  const index = await loadJSON("index.json");

  // Synthesise a fake root containing the tiles as direct children. Each tile
  // keeps its `bundle` pointer so a click can lazy-load the real subtree.
  const root = {
    id: "_root",
    label: "Overheid",
    children: index.tiles.map((tile) => ({
      id: tile.id,
      kind: tile.kind,
      label: tile.label,
      label_full: tile.label_full,
      bundle: tile.bundle,
      type: tile.kind === "ministerie" ? "ministerie" : undefined,
      // empty children = lazy; populated by handleClick after fetch
      children: [],
      _descendant_org_count: tile.descendant_org_count,
    })),
  };

  createChart(container, root, renderBreadcrumb);
})();

function renderBreadcrumb(trail, navigate) {
  breadcrumbEl.innerHTML = "";
  trail.forEach((entry, i) => {
    if (i > 0) {
      const sep = document.createElement("span");
      sep.className = "sep";
      sep.textContent = "›";
      breadcrumbEl.appendChild(sep);
    }
    const span = document.createElement("span");
    span.className = "crumb";
    span.textContent = entry.label;
    span.addEventListener("click", (e) => {
      e.stopPropagation();
      navigate(entry.node);
    });
    breadcrumbEl.appendChild(span);
  });
}
