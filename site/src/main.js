import { loadJSON } from "./fetcher.js";
import { createChart } from "./pack.js";
import { openFlatOverlay } from "./overlay.js";
import { openPersonPanel, closePersonPanel } from "./panel.js";
import { initTimeSlider } from "./timeslider.js";
import { initSearch } from "./search.js";
import { getState, setState, onStateChange } from "./state.js";

const container = document.getElementById("chart");
const breadcrumbEl = document.getElementById("breadcrumb");

let chartApi = null;
let searchApi = null;
let rootData = null;

(async function bootstrap() {
  const index = await loadJSON("index.json");
  const tilesById = new Map(index.tiles.map((t) => [t.id, t]));

  rootData = {
    id: "_root",
    label: "Overheid",
    kind: "root",
    children: (index.layers || []).map((layer) => ({
      id: layer.id,
      label: layer.label,
      kind: "bestuurslaag",
      _collapsed: true,
      children: layer.tile_ids
        .map((tid) => tilesById.get(tid))
        .filter(Boolean)
        .map(tileToNode),
    })),
  };

  const initialDate = getState().date ? Date.parse(getState().date) : Date.now();

  chartApi = createChart(container, rootData, renderBreadcrumb, {
    onFlatTile: handleFlatTile,
    onPerson: (node) => {
      setState({ p: node.data.person_id }, true);
      openPersonPanel(node.data.person_id);
    },
    onFocusChange: (node) => {
      const path = pathOf(node);
      setState({ org: path || null }, true);
    },
    date: initialDate,
  });

  searchApi = initSearch((item) => {
    setState({ q: item.label });
    if (item._kind === "persoon") {
      openPersonPanel(item.id);
    } else {
      chartApi.focusById(item.id);
    }
  });
  searchApi.indexFromHierarchy(rootData);

  initTimeSlider((ms) => chartApi.setDate(ms));

  const state = getState();
  if (state.org) chartApi.focusByPath(state.org);
  if (state.p) openPersonPanel(state.p);

  onStateChange((s) => {
    if (!s.p) closePersonPanel();
  });

  // Idle-time pre-load van ministerie- en agentschap-bundles zodat zoek breed werkt.
  if ("requestIdleCallback" in window) {
    requestIdleCallback(() => preloadBundles(index.tiles), { timeout: 5000 });
  }
})();

async function preloadBundles(tiles) {
  const targets = tiles.filter(
    (t) => t.kind === "ministerie" || t.kind === "category-tree",
  );
  for (const t of targets) {
    if (t.bundle) {
      loadJSON(t.bundle).then(() => mergeBundle(t.id, null)).catch(() => {});
    }
    if (t.members) {
      for (const m of t.members) {
        if (m.bundle) loadJSON(m.bundle).then(() => mergeBundle(m.id, null)).catch(() => {});
      }
    }
  }
}

function mergeBundle(_id, _data) {
  if (searchApi && rootData) searchApi.indexFromHierarchy(rootData);
}

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
        valid_from: m.valid_from,
        valid_until: m.valid_until,
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
    valid_from: tile.valid_from,
    valid_until: tile.valid_until,
    children: [],
    _descendant_org_count: tile.descendant_org_count,
  };
}

async function handleFlatTile(node) {
  if (!node.data.bundle) return;
  const data = await loadJSON(node.data.bundle);
  openFlatOverlay(data);
}

function pathOf(hierNode) {
  const ids = [];
  let cur = hierNode;
  while (cur && cur.parent) {
    if (cur.data.id) ids.unshift(cur.data.id.replace(/^[^:]+:/, ""));
    cur = cur.parent;
  }
  return ids.join("/");
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
