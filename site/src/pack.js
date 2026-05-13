import { loadJSON } from "./fetcher.js";
import { isActiveOn } from "./util.js";

const NODE_W = 130;
const NODE_H = 36;
const LEVEL_H = 90;
const SIBLING_GAP = 12;
const ZOOM_MS = 700;

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

  redraw();
  centerOn(rootHierarchy, true);
  emitCrumb();

  svg.on("click", () => focusOn(rootHierarchy));

  function redraw() {
    rootHierarchy = d3.hierarchy(rootData, childrenAccessor);

    const layout = d3.tree().nodeSize([NODE_W + SIBLING_GAP, LEVEL_H]);
    layout(rootHierarchy);

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
      .attr("width", NODE_W)
      .attr("height", NODE_H)
      .attr("x", -NODE_W / 2)
      .attr("y", -NODE_H / 2)
      .attr("rx", 6)
      .attr("ry", 6);

    enter
      .append("text")
      .attr("class", "node-label")
      .attr("text-anchor", "middle")
      .attr("dominant-baseline", "middle")
      .text((d) => truncate(d.data.label || "", 18));

    enter.on("mouseenter", (event, d) => {
      if (d.data.bundle && (!d.data.children || d.data.children.length === 0)) {
        loadJSON(d.data.bundle).catch(() => {});
      }
    });

    const merged = enter.merge(nodes);
    merged.attr("class", (d) => `node ${nodeKindClass(d)}`);
    merged.attr("transform", (d) => `translate(${d.x},${d.y})`);
    merged.select("text.node-label").text((d) => truncate(d.data.label || "", 18));
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
    const scale = Math.min(width / subW, height / subH, 1.5);
    const tx = width / 2 - ((minX + maxX) / 2) * scale;
    const ty = height / 2 - ((minY + maxY) / 2) * scale;
    const transform = `translate(${tx},${ty}) scale(${scale})`;
    if (instant) {
      viewport.attr("transform", transform);
    } else {
      viewport.transition().duration(ZOOM_MS).attr("transform", transform);
    }
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
    focusNode = target;
    centerOn(target);
    applyFade();
    emitCrumb();
    if (onFocusChange) onFocusChange(target);
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
      applyFade();
    },
  };
}

function childrenAccessor(d) {
  // Voor org-nodes: gewone children + posten (elk een eigen subtree).
  // Voor post-nodes: mandaten worden persoon-leaves.
  if (d.kind === "post") {
    return (d.mandaten || []).map(mandaatToPersonNode);
  }
  const out = [];
  if (d.children) out.push(...d.children);
  if (d.posten) out.push(...d.posten);
  return out.length ? out : null;
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
