import { loadJSON } from "./fetcher.js";

const PAD = 6;
const ZOOM_MS = 750;
const FADE_MS = 240;

export function createChart(container, rootData, onCrumbChange) {
  const width = container.clientWidth;
  const height = container.clientHeight;

  const svg = d3
    .select(container)
    .append("svg")
    .attr("viewBox", `-${width / 2} -${height / 2} ${width} ${height}`)
    .attr("width", width)
    .attr("height", height);

  const g = svg.append("g");

  let root = layout(rootData, Math.min(width, height));
  let focus = root;
  let view;

  render();
  zoomTo([focus.x, focus.y, focus.r * 2]);
  emitCrumb();

  svg.on("click", () => zoom(null, root));

  function layout(data, size) {
    const hierarchy = d3
      .hierarchy(data)
      .sum((d) => (d.children && d.children.length ? 0 : 1))
      .sort((a, b) => b.value - a.value);
    return d3.pack().size([size, size]).padding(PAD)(hierarchy);
  }

  function render() {
    g.selectAll("*").remove();
    const descendants = root.descendants().slice(1); // skip synthetic root

    g.selectAll("circle.node")
      .data(descendants, (d) => nodeId(d))
      .join("circle")
      .attr("class", (d) => `node ${nodeKindClass(d)}`)
      .attr("data-id", (d) => nodeId(d))
      .on("click", (event, d) => {
        event.stopPropagation();
        handleClick(d);
      });

    g.selectAll("text.label")
      .data(descendants, (d) => nodeId(d))
      .join("text")
      .attr("class", "label")
      .text((d) => d.data.label || "");

    applyFocusFade();
  }

  function nodeKindClass(d) {
    if (d.data.kind === "ministerie" || d.data.type === "ministerie") return "ministerie";
    if (d.children && d.children.length) return "onderdeel";
    return "leaf";
  }

  function nodeId(d) {
    return d.data.id || `_${d.depth}_${d.data.label || ""}`;
  }

  async function handleClick(d) {
    if (d.data.bundle && (!d.data.children || d.data.children.length === 0)) {
      try {
        const subtree = await loadJSON(d.data.bundle);
        d.data.children = subtree.children || [];
        for (const key of ["names", "valid_from", "valid_until", "type", "classification"]) {
          if (subtree[key] !== undefined) d.data[key] = subtree[key];
        }
        root = layout(rootData, Math.min(width, height));
        const next = root.find((n) => nodeId(n) === d.data.id) || root;
        focus = next;
        render();
        zoom(null, next);
        emitCrumb();
        return;
      } catch (err) {
        console.error("bundle load failed", err);
        return;
      }
    }
    zoom(null, d === focus ? (d.parent || root) : d);
  }

  function zoom(event, target) {
    focus = target;
    emitCrumb();
    const transition = svg
      .transition()
      .duration(ZOOM_MS)
      .tween("zoom", () => {
        const i = d3.interpolateZoom(view, [focus.x, focus.y, focus.r * 2]);
        return (t) => zoomTo(i(t));
      });
    transition.on("end", applyFocusFade);
    applyFocusFade();
  }

  function zoomTo(v) {
    view = v;
    const k = Math.min(width, height) / v[2];
    g.selectAll("circle.node").attr(
      "transform",
      (d) => `translate(${(d.x - v[0]) * k},${(d.y - v[1]) * k})`,
    );
    g.selectAll("circle.node").attr("r", (d) => d.r * k);
    g.selectAll("text.label")
      .attr("transform", (d) => `translate(${(d.x - v[0]) * k},${(d.y - v[1]) * k})`)
      .attr("font-size", (d) => Math.max(9, Math.min(16, d.r * k * 0.22)));
  }

  function applyFocusFade() {
    const focusSet = new Set();
    let cur = focus;
    while (cur) {
      focusSet.add(cur);
      cur = cur.parent;
    }
    focus.each((n) => focusSet.add(n));

    g.selectAll("circle.node").classed("faded", (d) => !focusSet.has(d));
    g.selectAll("text.label")
      .classed("faded", (d) => !focusSet.has(d))
      .style("display", (d) => labelVisible(d, focusSet) ? null : "none");
  }

  function labelVisible(d, focusSet) {
    if (!focusSet.has(d)) return false;
    const k = Math.min(width, height) / view[2];
    return d.r * k > 18;
  }

  function emitCrumb() {
    if (!onCrumbChange) return;
    const trail = [];
    let cur = focus;
    while (cur) {
      trail.unshift({
        id: cur.data.id || null,
        label: cur.data.label || "Overheid",
        node: cur,
      });
      cur = cur.parent;
    }
    onCrumbChange(trail, (target) => zoom(null, target));
  }

  return {
    focusRoot() {
      zoom(null, root);
    },
  };
}
