import { loadJSON } from "./fetcher.js";
import { isActiveOn } from "./util.js";

const NODE_W = 150;
const NODE_H = 48;
const COMPOUND_W = 210;
const COMPOUND_H = 62;
const ZOOM_MS = 700;
const GRID_THRESHOLD = 12;
const GRID_COLS = 10;

// Label-tekst: 11px font, gewrapt over max 2 regels. Gemiddelde
// glyph-breedte bij 11px ≈ 6px; CHARS_PER_LINE houdt ~10px padding aan
// elke kant van NODE_W/COMPOUND_W aan.
const LABEL_LINE_H = 13;
const charsPerLine = (boxW) => Math.max(6, Math.floor((boxW - 20) / 6));

// Breek een label over maximaal `maxLines` regels op woordgrens. Te lange
// woorden worden hard gesplitst; de laatste regel krijgt een ellipsis als
// er nog tekst over is. Geeft een array van regelstrings terug.
function wrapLabel(text, boxW, maxLines) {
  const max = charsPerLine(boxW);
  const words = String(text || "").split(/\s+/).filter(Boolean);
  const lines = [];
  let line = "";
  for (let i = 0; i < words.length; i += 1) {
    let word = words[i];
    // Woord langer dan een regel: hard afbreken.
    while (word.length > max) {
      if (line) {
        lines.push(line);
        line = "";
        if (lines.length === maxLines) break;
      }
      lines.push(word.slice(0, max - 1) + "-");
      word = word.slice(max - 1);
      if (lines.length === maxLines) break;
    }
    if (lines.length === maxLines) break;
    const candidate = line ? `${line} ${word}` : word;
    if (candidate.length <= max) {
      line = candidate;
    } else {
      if (line) lines.push(line);
      line = word;
      if (lines.length === maxLines) break;
    }
  }
  if (line && lines.length < maxLines) lines.push(line);
  // Resterende tekst die niet meer paste: ellipsis op de laatste regel.
  const used = lines.join(" ").replace(/-(?= )/g, "").length;
  const full = String(text || "").length;
  if (lines.length === maxLines && used < full) {
    let last = lines[maxLines - 1];
    if (last.length >= max) last = last.slice(0, max - 1);
    lines[maxLines - 1] = last.replace(/\s+$/, "") + "…";
  }
  return lines.length ? lines : [""];
}

// Sibling-gap en level-height groeien met focus-diepte: bij root staan
// onderdelen dicht op elkaar; bij inzoom op een directie krijgen kinderen
// meer lucht.
const COMPACT = { sibling: 4, level: 56 };
const SPACIOUS = { sibling: 24, level: 110 };

function spacingForDepth(depth) {
  const t = Math.min(1, depth / 4);
  return {
    sibling: COMPACT.sibling + (SPACIOUS.sibling - COMPACT.sibling) * t,
    level: COMPACT.level + (SPACIOUS.level - COMPACT.level) * t,
  };
}

export function createChart(container, rootData, onCrumbChange, options = {}) {
  const { onFlatTile, onPerson, onFocusChange } = options;
  let date = options.date != null ? options.date : Date.now();
  const width = container.clientWidth;
  const height = container.clientHeight;

  const svg = d3
    .select(container)
    .append("svg")
    .attr("width", width)
    .attr("height", height);

  const viewport = svg.append("g").attr("class", "viewport");
  const linkLayer = viewport.append("g").attr("class", "links");
  const nodeLayer = viewport.append("g").attr("class", "nodes");

  let rootHierarchy;
  let focusNode;

  const zoomBehavior = d3
    .zoom()
    .scaleExtent([0.1, 2.5])
    .filter((event) => {
      if (event.type === "mousedown" && event.target.closest("g.node")) return false;
      return !event.button;
    })
    .on("zoom", (event) => {
      viewport.attr("transform", event.transform);
    });
  svg.call(zoomBehavior);

  redraw();
  centerOn(rootHierarchy, true);
  emitCrumb();

  svg.on("click", () => focusOn(rootHierarchy));

  function childrenAccessor(d) {
    if (d._collapsed) return null;
    if (d.kind === "post") {
      const activeMandaten = (d.mandaten || []).filter((m) => isActiveOn(m, date));
      // Compound rendering: bij exact 1 lopend mandaat tonen we de persoon
      // inline in de post-tile (zie renderNodes); geen children-list nodig.
      // Bij meerdere mandaten (zeldzaam: tijdelijke overdracht) tonen we
      // ze nog wel als losse person-children.
      if (activeMandaten.length <= 1) return null;
      return activeMandaten.map(mandaatToPersonNode);
    }
    const out = [];
    if (d.children) {
      for (const c of d.children) {
        if (hasOwnDates(c) && !isActiveOn(c, date)) continue;
        out.push(c);
      }
    }
    if (d.posten) {
      for (const p of d.posten) {
        if (hasOwnDates(p) && !isActiveOn(p, date)) continue;
        // Filter posts zonder lopend mandaat op de huidige datum. Een
        // post:minister-min-X die historisch open staat maar nu niet
        // bemand wordt, hoort niet als lege box in de tree. Posten met
        // mandaten die ALLE buiten het datum-window vallen worden ook
        // gefilterd.
        const mandaten = p.mandaten || [];
        if (mandaten.length === 0) continue;
        const activeCount = mandaten.filter((m) => isActiveOn(m, date)).length;
        if (activeCount === 0) continue;
        out.push(p);
      }
    }
    return out.length ? out : null;
  }

  function redraw() {
    rootHierarchy = d3.hierarchy(rootData, childrenAccessor);

    const focusDepth = focusNode ? focusNode.depth : 0;
    const sp = spacingForDepth(focusDepth);
    // Bij compound-nodes (post met persoon inline) gebruiken we COMPOUND_W
    // als sibling-spacing zodat tegels niet overlappen.
    const layout = d3.tree().nodeSize([COMPOUND_W + sp.sibling, COMPOUND_H + sp.level]);
    layout(rootHierarchy);
    wrapWideRows(rootHierarchy, sp);

    if (!focusNode) focusNode = rootHierarchy;
    else {
      const refreshed = rootHierarchy.find((n) => nodeId(n) === nodeId(focusNode));
      focusNode = refreshed || rootHierarchy;
    }

    renderLinks();
    renderNodes();
    applyFade();
  }

  function renderLinks() {
    const links = linkLayer
      .selectAll("path.link")
      .data(rootHierarchy.links(), (d) => `${nodeId(d.source)}->${nodeId(d.target)}`);
    links.exit().remove();
    links
      .enter()
      .append("path")
      .attr("class", "link")
      .merge(links)
      .attr("d", (d) => linkPath(d.source, d.target));
  }

  function renderNodes() {
    const nodes = nodeLayer
      .selectAll("g.node")
      .data(rootHierarchy.descendants(), (d) => nodeId(d));
    nodes.exit().remove();

    const enter = nodes
      .enter()
      .append("g")
      .attr("class", (d) => `node ${nodeKindClass(d)}`)
      .attr("data-id", (d) => nodeId(d))
      .style("cursor", "pointer")
      .on("click", (event, d) => {
        event.stopPropagation();
        handleClick(d);
      });

    enter
      .append("rect")
      .attr("width", (d) => nodeWidth(d))
      .attr("height", (d) => nodeHeight(d))
      .attr("x", (d) => -nodeWidth(d) / 2)
      .attr("y", (d) => -nodeHeight(d) / 2)
      .attr("rx", 6)
      .attr("ry", 6);

    enter
      .append("text")
      .attr("class", "node-label")
      .attr("text-anchor", "middle");

    // Tweede label-element voor de persoonsnaam bij compound posts.
    enter
      .filter((d) => compoundPersonName(d) !== null)
      .append("text")
      .attr("class", "node-label node-subline")
      .attr("text-anchor", "middle");

    enter.on("mouseenter", (event, d) => {
      if (d.data.bundle && (!d.data.children || d.data.children.length === 0)) {
        loadJSON(d.data.bundle).catch(() => {});
      }
    });

    const merged = enter.merge(nodes);
    merged.attr("class", (d) => `node ${nodeKindClass(d)}`);
    merged.attr("transform", (d) => `translate(${d.x},${d.y})`);
    merged
      .select("rect")
      .attr("width", (d) => nodeWidth(d))
      .attr("height", (d) => nodeHeight(d))
      .attr("x", (d) => -nodeWidth(d) / 2)
      .attr("y", (d) => -nodeHeight(d) / 2);
    // Hoofdlabel: gewrapt over max 2 regels, verticaal gecentreerd in de
    // box. Bij compound posts krijgt het label 1 regel zodat de
    // persoonsnaam eronder past.
    merged.selectAll("text.node-label:not(.node-subline)").each(function (d) {
      const sub = compoundPersonName(d);
      const boxW = nodeWidth(d);
      const maxLines = sub !== null ? 1 : 2;
      const lines = wrapLabel(d.data.label || "", boxW, maxLines);
      renderTspans(d3.select(this), lines, sub !== null);
    });
    // Subline (persoon-naam) bij compound, onder het label.
    merged.select("text.node-subline").each(function (d) {
      const sub = compoundPersonName(d);
      if (sub === null) return;
      const lines = wrapLabel(sub, nodeWidth(d), 1);
      const sel = d3.select(this);
      sel.selectAll("tspan").remove();
      sel
        .append("tspan")
        .attr("x", 0)
        .attr("y", LABEL_LINE_H * 0.9)
        .attr("dominant-baseline", "central")
        .text(lines[0]);
    });
  }

  // Plaats label-regels als <tspan>s, verticaal gecentreerd. Bij een
  // subline (compound post) schuift het blok omhoog zodat de naam eronder
  // ruimte heeft.
  function renderTspans(textSel, lines, hasSubline) {
    textSel.selectAll("tspan").remove();
    const n = lines.length;
    const centerY = hasSubline ? -LABEL_LINE_H * 0.55 : 0;
    const firstY = centerY - ((n - 1) * LABEL_LINE_H) / 2;
    lines.forEach((ln, i) => {
      textSel
        .append("tspan")
        .attr("x", 0)
        .attr("y", firstY + i * LABEL_LINE_H)
        .attr("dominant-baseline", "central")
        .text(ln);
    });
  }

  function compoundPersonName(d) {
    // Geeft de persoonsnaam terug als deze post precies één actief
    // mandaat heeft op de huidige datum. Anders null.
    if (!d.data || d.data.kind !== "post") return null;
    const active = (d.data.mandaten || []).filter((m) => isActiveOn(m, date));
    if (active.length !== 1) return null;
    return active[0].person_label || active[0].person_id || null;
  }

  function nodeWidth(d) {
    return compoundPersonName(d) !== null ? COMPOUND_W : NODE_W;
  }

  function nodeHeight(d) {
    return compoundPersonName(d) !== null ? COMPOUND_H : NODE_H;
  }

  function nodeKindClass(d) {
    if (d.depth === 0) return "root";
    if (d.data.kind === "bestuurslaag") return "bestuurslaag";
    if (d.data.kind === "category-flat") return "category-flat";
    if (d.data.kind === "category-tree") return "category-tree";
    if (d.data.kind === "person") return "person";
    if (d.data.kind === "post") return "post";
    if (d.data.kind === "ministerie" || d.data.type === "ministerie") return "ministerie";
    if (d.children && d.children.length) return "onderdeel";
    return "leaf";
  }

  function nodeId(d) {
    return d.data.id || `_${d.depth}_${d.data.label || ""}`;
  }

  // True als deze node (potentieel) kinderen kan tonen: een bestuurslaag of
  // category-tree, een org met children/posten in geheugen, of een org met
  // een nog niet geladen bundle. Een niet-geladen bundle telt mee zodat
  // handleClick zo'n node als uitklapbaar behandelt vóór de eerste load.
  function canHaveChildren(data) {
    if (!data) return false;
    if (data.kind === "bestuurslaag" || data.kind === "category-tree") return true;
    if (data.children && data.children.length) return true;
    if (data.posten && data.posten.length) return true;
    if (data.bundle) return true;
    return false;
  }

  async function handleClick(d) {
    if (d.data.kind === "category-flat" && onFlatTile) {
      onFlatTile(d);
      return;
    }
    if (d.data.kind === "person" && onPerson) {
      onPerson(d);
      return;
    }
    // Compound post-tile (bewindspersoon inline): klik opent persoon-panel.
    if (d.data.kind === "post" && onPerson) {
      const active = (d.data.mandaten || []).filter((m) => isActiveOn(m, date));
      if (active.length === 1 && active[0].person_id) {
        onPerson({ data: { person_id: active[0].person_id } });
        return;
      }
    }
    if (!canHaveChildren(d.data)) {
      // Echte leaf: gewoon inzoomen, geen toggle.
      focusOn(d);
      return;
    }

    const isOpen = !d.data._collapsed && childrenAccessor(d.data) != null;
    if (isOpen) {
      // Al opengeklapt → dichtklappen en uitzoomen naar de parent. Parent-id
      // vóór redraw vastleggen; redraw herbouwt de hierarchy.
      const parentId = d.parent ? nodeId(d.parent) : null;
      d.data._collapsed = true;
      redraw();
      const target =
        (parentId && rootHierarchy.find((n) => nodeId(n) === parentId)) ||
        rootHierarchy.find((n) => nodeId(n) === d.data.id) ||
        rootHierarchy;
      focusOn(target);
      return;
    }

    // Dicht → openklappen. Bundle eerst laden als die nog niet binnen is.
    if (d.data.bundle && (!d.data.children || d.data.children.length === 0)) {
      try {
        const subtree = await loadJSON(d.data.bundle);
        d.data.children = (subtree.children || []).map(collapseSubtreeNode);
        d.data.posten = subtree.posten || [];
        for (const key of ["names", "valid_from", "valid_until", "type", "classification"]) {
          if (subtree[key] !== undefined) d.data[key] = subtree[key];
        }
      } catch (err) {
        console.error("bundle load failed", err);
        return;
      }
    }
    d.data._collapsed = false;
    redraw();
    const target = rootHierarchy.find((n) => nodeId(n) === d.data.id) || rootHierarchy;
    focusOn(target);
  }

  function centerOn(target, instant = false) {
    // Zoom op de aangeklikte node + zijn dichtstbijzijnde kinderen, op een
    // leesbare schaal, gecentreerd op de node zelf. De d3.tree-layout
    // spreidt een brede kinderrij over duizenden pixels uit; de hele rij
    // inpassen zou tot een onleesbare mini-strip uitzoomen. Liever ingezoomd
    // op de org en horizontaal pannen door de onderdelen.
    const kids = (target.children || []).filter(
      (n) => Number.isFinite(n.x) && Number.isFinite(n.y),
    );
    if (!Number.isFinite(target.x) || !Number.isFinite(target.y)) return;
    // Verticale extent (node + kinderrijen) bepaalt de schaal samen met een
    // horizontaal venster van de ~8 kinderen het dichtst bij de node-x.
    const NEAR = 8;
    const near = kids
      .map((n) => ({ n, d: Math.abs(n.x - target.x) }))
      .sort((a, b) => a.d - b.d)
      .slice(0, NEAR)
      .map((o) => o.n);
    const frame = [target, ...near];
    const xs = frame.map((n) => n.x);
    const ys = [target, ...kids].map((n) => n.y);
    const minX = Math.min(...xs) - NODE_W / 2 - 20;
    const maxX = Math.max(...xs) + NODE_W / 2 + 20;
    const minY = Math.min(...ys) - NODE_H / 2 - 20;
    const maxY = Math.max(...ys) + NODE_H / 2 + 20;
    const subW = Math.max(maxX - minX, NODE_W * 3);
    const subH = Math.max(maxY - minY, NODE_H * 3);
    const fit = Math.min(width / subW, height / subH);
    // Ondergrens 0.55 zodat tegels altijd leesbaar blijven, bovengrens 1.5.
    const scale = Math.max(0.55, Math.min(fit, 1.5));
    // Altijd op de aangeklikte node centreren — niet op het midden van een
    // bounding box die door de brede layout ver van de node kan liggen.
    const cx = target.x;
    const cy = (minY + maxY) / 2;
    const tx = width / 2 - cx * scale;
    const ty = height / 2 - cy * scale;
    const transform = d3.zoomIdentity.translate(tx, ty).scale(scale);
    const sel = instant ? svg : svg.transition().duration(ZOOM_MS);
    sel.call(zoomBehavior.transform, transform);
  }

  function applyFade() {
    const focusSet = new Set();
    focusNode.each((n) => focusSet.add(n));
    let cur = focusNode;
    while (cur) {
      focusSet.add(cur);
      cur = cur.parent;
    }
    nodeLayer
      .selectAll("g.node")
      .classed("faded", (d) => !focusSet.has(d))
      .classed("inactive", (d) => !nodeActive(d));
    linkLayer
      .selectAll("path.link")
      .classed("faded", (d) => !focusSet.has(d.source) || !focusSet.has(d.target))
      .classed("inactive", (d) => !nodeActive(d.target));
  }

  function nodeActive(d) {
    const data = d.data;
    if (data.kind === "person") return isActiveOn(data, date);
    if (data.kind === "post") return isActiveOn(data, date);
    if (data.valid_from !== undefined || data.valid_until !== undefined) {
      return isActiveOn(data, date);
    }
    return true;
  }

  function emitCrumb() {
    if (!onCrumbChange) return;
    const trail = [];
    let cur = focusNode;
    while (cur) {
      trail.unshift({
        id: cur.data.id || null,
        label: cur.data.label || "Overheid",
        node: cur,
      });
      cur = cur.parent;
    }
    onCrumbChange(trail, (target) => focusOn(target));
  }

  function focusOn(target) {
    const targetId = target && target.data && target.data.id;
    focusNode = target;
    redraw();
    if (targetId) {
      const refreshed = rootHierarchy.find((n) => nodeId(n) === targetId);
      if (refreshed) focusNode = refreshed;
    }
    centerOn(focusNode);
    applyFade();
    emitCrumb();
    if (onFocusChange) onFocusChange(focusNode);
  }

  async function focusByPath(pathStr) {
    if (!pathStr) return;
    const segments = pathStr.split("/").filter(Boolean);
    if (!segments.length) return;
    // Volg de keten gericht door rootData: per segment de matchende child
    // zoeken, en als die een nog-niet-geladen bundle heeft die laden vóór
    // we dieper gaan. Zo halen we precies de bundles op het pad op (bv.
    // alleen min-bzk), niet blind de hele wereld. Faalt een segment, val
    // dan terug op de generieke zoek-via-id (focusById) met de eindslug.
    let node = rootData;
    for (const seg of segments) {
      let next = (node.children || []).find((c) => idMatches(c.id, seg));
      if (!next) {
        for (const p of node.posten || []) {
          if (idMatches(p.id, seg)) {
            next = p;
            break;
          }
        }
      }
      if (!next) {
        // Segment niet zichtbaar onder de huidige node — laad de bundle van
        // node (indien aanwezig) en probeer opnieuw.
        if (node.bundle && (!node.children || node.children.length === 0)) {
          try {
            const subtree = await loadJSON(node.bundle);
            node.children = (subtree.children || []).map(collapseSubtreeNode);
            node.posten = subtree.posten || [];
          } catch (e) {
            break;
          }
          next = (node.children || []).find((c) => idMatches(c.id, seg));
        }
      }
      if (!next) break;
      next._collapsed = false;
      // Laad de bundle van de gevonden node zodat het volgende segment
      // (zijn kind) vindbaar is.
      if (next.bundle && (!next.children || next.children.length === 0)) {
        try {
          const subtree = await loadJSON(next.bundle);
          next.children = (subtree.children || []).map(collapseSubtreeNode);
          next.posten = subtree.posten || [];
        } catch (e) {
          // Geen bundle of mislukt — de subtree kan al inline aanwezig zijn.
        }
      }
      node = next;
    }
    // Uncollapse de hele keten naar de gevonden eindnode en focus erop.
    // Lukte de gerichte walk niet, val terug op id-zoek met de eindslug.
    const endId = node && node.id;
    if (!endId || node === rootData) {
      return focusById(segments[segments.length - 1]);
    }
    const path = findInData(rootData, endId);
    if (path) for (const n of path) n._collapsed = false;
    redraw();
    const found = rootHierarchy.find((n) => idMatches(nodeId(n), endId));
    if (found) focusOn(found);
    else return focusById(segments[segments.length - 1]);
  }

  // Matcht een node-id tegen een query. Exacte match, of — als de query
  // geen prefix (`<type>:`) heeft — match op de slug ongeacht het type, zodat
  // een prefix-loze deeplink-segment ook een post: of person: node vindt.
  function idMatches(candidateId, query) {
    if (!candidateId) return false;
    if (candidateId === query) return true;
    if (query.includes(":")) return false;
    const colon = candidateId.indexOf(":");
    return colon >= 0 && candidateId.slice(colon + 1) === query;
  }

  // Walk the raw data tree (ignoring _collapsed) looking for a node id,
  // collecting all ancestors along the way so we can uncollapse them.
  // Persons are represented as mandaten inside posten[] (not children),
  // so when a person-id is requested we also scan post.mandaten for it
  // and return a path that ends at the holding post — focusing on the
  // post zooms in on the person as effectively as a person node would.
  function findInData(node, id, path = []) {
    const here = [...path, node];
    if (idMatches(node.id, id)) return here;
    // Posten hangen in posten[], niet in children[]. Match op de post-id
    // zelf (een deeplink kan eindigen op een post-slug), en — voor een
    // person-query of prefix-loze slug — op de mandaat-persoon.
    for (const p of node.posten || []) {
      if (idMatches(p.id, id)) return [...here, p];
      if (id.startsWith("person:") || !id.includes(":")) {
        for (const m of p.mandaten || []) {
          if (idMatches(m.person_id, id)) return [...here, p];
        }
      }
    }
    for (const child of node.children || []) {
      const hit = findInData(child, id, here);
      if (hit) return hit;
    }
    return null;
  }

  async function focusById(id) {
    let found = rootHierarchy.find((n) => idMatches(nodeId(n), id));
    if (found) {
      focusOn(found);
      return;
    }
    // Load bundles one at a time, checking after each load whether the
    // target has appeared in the raw data tree. This avoids loading the
    // whole world when the target sits in just one ministerie bundle.
    let pathToTarget = findInData(rootData, id);
    let pass = 0;
    while (!pathToTarget && pass < 30) {
      pass += 1;
      let nextCandidate = null;
      (function collect(node) {
        if (nextCandidate) return;
        if (node.bundle && (!node.children || node.children.length === 0)) {
          nextCandidate = node;
          return;
        }
        for (const c of node.children || []) collect(c);
      })(rootData);
      if (!nextCandidate) break;
      try {
        const subtree = await loadJSON(nextCandidate.bundle);
        nextCandidate.children = (subtree.children || []).map(collapseSubtreeNode);
        nextCandidate.posten = subtree.posten || [];
      } catch (e) {
        // Mark as visited so we don't loop forever on a broken bundle.
        nextCandidate.children = [];
      }
      pathToTarget = findInData(rootData, id);
    }
    if (!pathToTarget) {
      redraw();
      return;
    }
    // Uncollapse every ancestor so the target lands in rootHierarchy.
    for (const n of pathToTarget) n._collapsed = false;
    redraw();
    // For persons the path ends at the holding post — focus on that
    // post since the person itself is rendered inline on the post tile.
    const finalNode = pathToTarget[pathToTarget.length - 1];
    found = rootHierarchy.find((n) => nodeId(n) === finalNode.id);
    if (found) focusOn(found);
  }

  return {
    focusRoot() {
      focusOn(rootHierarchy);
    },
    focusById,
    focusByPath,
    setDate(ms) {
      date = ms;
      redraw();
    },
  };
}

function wrapWideRows(root, sp) {
  root.each((node) => {
    const kids = node.children;
    if (!kids || kids.length <= GRID_THRESHOLD) return;
    if (kids.some((c) => c.children && c.children.length)) return;
    const cols = GRID_COLS;
    const colW = NODE_W + sp.sibling;
    const rowH = NODE_H + Math.max(8, sp.level * 0.4);
    const startX = node.x - ((cols - 1) * colW) / 2;
    const baseY = kids[0].y;
    kids.forEach((c, i) => {
      const row = Math.floor(i / cols);
      const col = i % cols;
      c.x = startX + col * colW;
      c.y = baseY + row * rowH;
    });
  });
}


// Markeer een nieuw uit een bundle geladen subtree-node als dichtgeklapt,
// zodat hij niet meteen zijn eigen kinderen toont (per-laag openklappen).
// Alleen nodes die zelf kinderen/posten/een bundle hebben krijgen de flag;
// echte leaves blijven ongemoeid zodat ze als leaf-klik werken.
function collapseSubtreeNode(node) {
  if (!node || typeof node !== "object") return node;
  const hasKids =
    (node.children && node.children.length) ||
    (node.posten && node.posten.length) ||
    node.bundle;
  if (hasKids) node._collapsed = true;
  return node;
}

function hasOwnDates(d) {
  return (
    d.valid_from !== undefined ||
    d.valid_until !== undefined ||
    d.start_date !== undefined ||
    d.end_date !== undefined
  );
}

function mandaatToPersonNode(m) {
  return {
    id: `mandaat:${m.id || `${m.person_id}-${m.start_date || ""}`}`,
    kind: "person",
    label: m.person_label || m.person_id || "?",
    person_id: m.person_id,
    role: m.role,
    start_date: m.start_date,
    end_date: m.end_date,
  };
}

function linkPath(source, target) {
  const sy = source.y + NODE_H / 2;
  const ty = target.y - NODE_H / 2;
  const my = (sy + ty) / 2;
  return `M${source.x},${sy} C${source.x},${my} ${target.x},${my} ${target.x},${ty}`;
}
