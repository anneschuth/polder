import { loadJSON } from "./fetcher.js";
import { isActiveOn } from "./util.js";

const NODE_W = 130;
const NODE_H = 36;
const COMPOUND_W = 200;
const COMPOUND_H = 52;
const ZOOM_MS = 700;
const GRID_THRESHOLD = 12;
const GRID_COLS = 10;

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
      .attr("text-anchor", "middle")
      .attr("dominant-baseline", "middle")
      .text((d) => truncate(d.data.label || "", 18));

    // Tweede regel voor compound posts (post met 1 active mandate inline).
    enter
      .filter((d) => compoundPersonName(d) !== null)
      .append("text")
      .attr("class", "node-label node-subline")
      .attr("text-anchor", "middle")
      .attr("dominant-baseline", "middle");

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
    // Hoofdlabel: bij compound iets omhoog, anders gecentreerd.
    merged.select("text.node-label").each(function (d) {
      const sub = compoundPersonName(d);
      const el = d3.select(this);
      if (sub !== null) {
        el.attr("y", -9).text(truncate(d.data.label || "", 28));
      } else {
        el.attr("y", 0).text(truncate(d.data.label || "", 18));
      }
    });
    // Subline (persoon-naam) bij compound.
    merged.select("text.node-subline").each(function (d) {
      const sub = compoundPersonName(d);
      if (sub === null) return;
      d3.select(this).attr("y", 11).text(truncate(sub, 28));
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
    if (d.data.kind === "bestuurslaag" || d.data.kind === "category-tree") {
      d.data._collapsed = !d.data._collapsed;
      redraw();
      const target = rootHierarchy.find((n) => nodeId(n) === d.data.id) || rootHierarchy;
      focusOn(target);
      return;
    }
    if (d.data.bundle && (!d.data.children || d.data.children.length === 0)) {
      try {
        const subtree = await loadJSON(d.data.bundle);
        d.data.children = subtree.children || [];
        d.data.posten = subtree.posten || [];
        for (const key of ["names", "valid_from", "valid_until", "type", "classification"]) {
          if (subtree[key] !== undefined) d.data[key] = subtree[key];
        }
        redraw();
        const target = rootHierarchy.find((n) => nodeId(n) === d.data.id) || rootHierarchy;
        focusOn(target);
        return;
      } catch (err) {
        console.error("bundle load failed", err);
        return;
      }
    }
    focusOn(d);
  }

  function centerOn(target, instant = false) {
    const subtreeNodes = target.descendants();
    const xs = subtreeNodes.map((n) => n.x);
    const ys = subtreeNodes.map((n) => n.y);
    const minX = Math.min(...xs) - NODE_W / 2 - 20;
    const maxX = Math.max(...xs) + NODE_W / 2 + 20;
    const minY = Math.min(...ys) - NODE_H / 2 - 20;
    const maxY = Math.max(...ys) + NODE_H / 2 + 20;
    const subW = Math.max(maxX - minX, NODE_W * 3);
    const subH = Math.max(maxY - minY, NODE_H * 3);
    const fit = Math.min(width / subW, height / subH);
    const scale = Math.max(0.2, Math.min(fit, 1.5));
    const tx = width / 2 - ((minX + maxX) / 2) * scale;
    const ty = height / 2 - ((minY + maxY) / 2) * scale;
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
    let cur = rootHierarchy;
    for (const seg of segments) {
      const fullId = seg.startsWith("layer:")
        ? seg
        : seg.startsWith("cat:")
          ? seg
          : seg.startsWith("post:") || seg.startsWith("person:") || seg.startsWith("org:")
            ? seg
            : `org:${seg}`;
      let next = cur.find ? cur.find((n) => nodeId(n) === fullId) : null;
      if (!next) {
        const child = (cur.data.children || []).find((c) => c.id === fullId);
        if (!child) break;
        if (child.bundle && (!child.children || child.children.length === 0)) {
          try {
            const subtree = await loadJSON(child.bundle);
            child.children = subtree.children || [];
            child.posten = subtree.posten || [];
            redraw();
            next = rootHierarchy.find((n) => nodeId(n) === fullId);
          } catch (err) {
            console.error(err);
            break;
          }
        }
      }
      if (!next) break;
      cur = next;
    }
    if (cur && cur !== rootHierarchy) focusOn(cur);
  }

  async function focusById(id) {
    let found = rootHierarchy.find((n) => nodeId(n) === id);
    if (found) {
      focusOn(found);
      return;
    }
    // Try to load any bundle that might contain this id; scan top-level tiles.
    const candidates = rootHierarchy.descendants().filter((n) => n.data.bundle);
    for (const cand of candidates) {
      if (!cand.data.children || cand.data.children.length === 0) {
        try {
          const subtree = await loadJSON(cand.data.bundle);
          cand.data.children = subtree.children || [];
          cand.data.posten = subtree.posten || [];
        } catch (e) {
          continue;
        }
      }
    }
    redraw();
    found = rootHierarchy.find((n) => nodeId(n) === id);
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

function truncate(s, n) {
  if (s.length <= n) return s;
  return s.slice(0, n - 1) + "…";
}
