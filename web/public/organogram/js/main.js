import { loadJSON } from "./fetcher.js";
import { createChart } from "./pack.js";
import { openFlatOverlay } from "./overlay.js";
import { initTimeSlider } from "./timeslider.js";
import { initSearch } from "./search.js";
import { getState, setState } from "./state.js";

// Navigate to the matching polder detail page instead of opening an
// inline panel. The viz now defers to the rest of the Astro site for
// detail views, which keeps the visualisation focused on exploration.
function siteBase() {
  const meta = document.querySelector('meta[name="organogram-data-base"]');
  if (meta && meta.content) {
    // dataBase is `<base>/organogram/data` — strip the suffix to get the
    // site base (e.g. `/polder`).
    return meta.content.replace(/\/organogram\/data$/, "");
  }
  return "";
}

function gotoPerson(personId) {
  if (!personId) return;
  const slug = personId.replace(/^person:/, "");
  window.location.href = `${siteBase()}/personen/${slug}/`;
}

// Navigeer naar de organisatie-detailpagina. De route is
// /organisaties/<type>/<slug>/ waar <type> de mapnaam is (meervoud, bv.
// "gemeenten") en <slug> de org-id zonder org:-prefix.
function gotoOrg(orgType, orgId) {
  if (!orgType || !orgId) return;
  const slug = orgId.replace(/^org:/, "");
  window.location.href = `${siteBase()}/organisaties/${orgType}/${slug}/`;
}

// DOM references are looked up inside bootstrap() each run so the viz
// can be re-initialised after Astro View Transitions swap the page DOM.
let container = null;
let breadcrumbEl = null;
let chartApi = null;
let searchApi = null;
let rootData = null;

// Force light theme — dark mode is removed from this embedded variant.
document.documentElement.dataset.theme = "light";

async function bootstrap() {
  container = document.getElementById("chart");
  breadcrumbEl = document.getElementById("breadcrumb");
  if (!container) return; // not on the organogram page

  // Reset module-level state so re-runs after view transitions don't
  // bleed state from the previous page lifecycle.
  chartApi = null;
  searchApi = null;
  rootData = null;

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
      gotoPerson(node.data.person_id);
    },
    onFocusChange: (node) => {
      const path = pathOf(node);
      setState({ org: path || null }, true);
    },
    date: initialDate,
  });

  searchApi = initSearch((item) => {
    setState({ q: item.label });
    // Always zoom in on the selected item — including persons. The
    // person-detail page is reachable by clicking the person *node* in
    // the chart (which navigates), not from the search dropdown which
    // should keep the user inside the visualisation.
    chartApi.focusById(item.id);
  });
  searchApi.indexFromHierarchy(rootData);

  initTimeSlider((ms) => chartApi.setDate(ms));

  const state = getState();
  if (state.org) chartApi.focusByPath(state.org);
  if (state.p) gotoPerson(state.p);
  if (state.q && !state.org && !state.p) {
    // Pre-fill the search box and pick the top match so URLs like
    // `#q=Def` jump straight to Defensie. Persons are zoomed-in on too;
    // the detail page is reachable from the chart node, not the search.
    const input = document.getElementById("search");
    if (input) input.value = state.q;
    const best = searchApi.findBest(state.q);
    if (best) chartApi.focusById(best.id);
  }

  // Fire-and-forget eager pre-load of all ministerie and agentschap
  // bundles so the search index covers persons inside them right after
  // boot. We don't await — the chart is fully usable without these and
  // they trickle in over the next second or two.
  preloadBundles(index.tiles);
}

// Astro fires `astro:page-load` after every navigation, including the
// very first hard load (when the ClientRouter is installed). Use it as
// the single bootstrap entry point so the viz is set up exactly once
// per page lifecycle, never twice.
document.addEventListener("astro:page-load", () => {
  bootstrap();
});

// Walk rootData to find a node with the given id (raw data, ignores
// _collapsed). Used by mergeBundle to install bundle children on the
// correct branch so search can index them.
function findInRootData(id) {
  function walk(node) {
    if (!node) return null;
    if (node.id === id) return node;
    for (const c of node.children || []) {
      const hit = walk(c);
      if (hit) return hit;
    }
    return null;
  }
  return rootData ? walk(rootData) : null;
}

// Cap on simultaneous bundle fetches. The eager preload touches every
// ministerie/category-tree tile plus all their members (~1700 bundles);
// firing them all at once exhausts the browser's connection pool and
// Chrome returns ERR_INSUFFICIENT_RESOURCES. A small worker pool keeps
// the preload fire-and-forget but bounded.
const PRELOAD_CONCURRENCY = 6;

async function preloadBundles(tiles) {
  const targets = tiles.filter(
    (t) => t.kind === "ministerie" || t.kind === "category-tree",
  );
  const jobs = [];
  for (const t of targets) {
    if (t.bundle) jobs.push({ id: t.id, bundle: t.bundle });
    for (const m of t.members || []) {
      if (m.bundle) jobs.push({ id: m.id, bundle: m.bundle });
    }
  }
  let next = 0;
  async function worker() {
    while (next < jobs.length) {
      const job = jobs[next++];
      try {
        const data = await loadJSON(job.bundle);
        mergeBundle(job.id, data);
      } catch {
        /* preload is best-effort; the chart works without it */
      }
    }
  }
  const pool = Array.from(
    { length: Math.min(PRELOAD_CONCURRENCY, jobs.length) },
    worker,
  );
  await Promise.all(pool);
}

function mergeBundle(id, data) {
  if (!data) return;
  const target = findInRootData(id);
  if (!target) return;
  if (!target.children || target.children.length === 0) {
    // Markeer geïnstalleerde subtree-children als dichtgeklapt zodat ze niet
    // auto-expanderen zodra hun parent opengaat (per-laag openklappen).
    target.children = (data.children || []).map((child) => {
      const hasKids =
        (child.children && child.children.length) ||
        (child.posten && child.posten.length) ||
        child.bundle;
      if (hasKids) child._collapsed = true;
      return child;
    });
    target.posten = data.posten || [];
  }
  if (searchApi && rootData) searchApi.indexFromHierarchy(rootData);
}

function tileToNode(tile) {
  if (tile.kind === "category-tree") {
    return {
      id: tile.id,
      kind: "category-tree",
      label: tile.label,
      _collapsed: true,
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
      org_type: tile.org_type,
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
    _collapsed: true,
    _descendant_org_count: tile.descendant_org_count,
  };
}

async function handleFlatTile(node) {
  if (!node.data.bundle) return;
  let data;
  try {
    data = await loadJSON(node.data.bundle);
  } catch (err) {
    console.error("flat-tile bundle load failed", err);
    return;
  }
  // Type voor de detail-route is het enkelvoudige org_type van de tile
  // (gemeente, zbo, waterschap, …) — dat is wat de Astro-route
  // /organisaties/<type>/<slug>/ verwacht (zie orgUrl in lib/data.ts).
  const orgType = node.data.org_type;
  openFlatOverlay(data, (item) => gotoOrg(orgType, item.id));
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
