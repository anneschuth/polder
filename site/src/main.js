import { loadJSON } from "./fetcher.js";
import { createChart } from "./pack.js";
import { openFlatOverlay } from "./overlay.js";
import { openPersonPanel } from "./panel.js";

const container = document.getElementById("chart");
const breadcrumbEl = document.getElementById("breadcrumb");

(async function bootstrap() {
  const index = await loadJSON("index.json");
  const tilesById = new Map(index.tiles.map((t) => [t.id, t]));

  const root = {
    id: "_root",
    label: "Overheid",
    kind: "root",
    children: (index.layers || []).map((layer) => ({
      id: layer.id,
      label: layer.label,
      kind: "bestuurslaag",
      children: layer.tile_ids
        .map((tid) => tilesById.get(tid))
        .filter(Boolean)
        .map(tileToNode),
    })),
  };

  createChart(container, root, renderBreadcrumb, {
    onFlatTile: handleFlatTile,
    onPerson: (node) => openPersonPanel(node.data.person_id),
  });
})();

function tileToNode(tile) {
  if (tile.kind === "category-tree") {
    return {
      id: tile.id,
      kind: "category-tree",
      label: tile.label,
      _count: tile.count,
      children: (tile.members || []).map((m) => ({
        id: m.id,
        kind: "category-member",
        label: m.label,
        label_full: m.label_full,
        bundle: m.bundle,
        type: m.org_type,
        children: [],
        _descendant_org_count: m.descendant_org_count,
      })),
    };
  }
  if (tile.kind === "category-flat") {
    return {
      id: tile.id,
      kind: "category-flat",
      label: tile.label,
      bundle: tile.bundle,
      _count: tile.count,
      children: [],
    };
  }
  return {
    id: tile.id,
    kind: tile.kind,
    label: tile.label,
    label_full: tile.label_full,
    bundle: tile.bundle,
    type: tile.kind === "ministerie" ? "ministerie" : undefined,
    children: [],
    _descendant_org_count: tile.descendant_org_count,
  };
}

async function handleFlatTile(node) {
  if (!node.data.bundle) return;
  const data = await loadJSON(node.data.bundle);
  openFlatOverlay(data);
}

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
