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
  let viewMode = "tree";

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

    const ministerieNode = findMinisterieRoot(rootHierarchy);
    if (ministerieNode) {
      viewMode = "ministerie";
      layoutMinisterieOrganogram(ministerieNode, rootHierarchy);
    } else {
      viewMode = "tree";
      const layout = d3.tree().nodeSize([COMPOUND_W + sp.sibling, COMPOUND_H + sp.level]);
      layout(rootHierarchy);
      wrapWideRows(rootHierarchy, sp);
    }

    if (!focusNode) focusNode = rootHierarchy;
    else {
      const refreshed = rootHierarchy.find((n) => nodeId(n) === nodeId(focusNode));
      focusNode = refreshed || rootHierarchy;
    }

    renderLinks();
    renderNodes();
    applyFade();
  }

  function findMinisterieRoot(root) {
    // Geeft de eerste ministerie-node terug waar focus op staat, of waar
    // alleen onder gewerkt wordt. We willen alleen ministerie-layout
    // toepassen als de view op ÉÉN ministerie is gefocust (geen multi-min
    // top-level). Detectie: focusNode is een ministerie OF rootHierarchy
    // heeft precies één descendant met kind=ministerie en die is geladen.
    if (!focusNode) return null;
    // Loop omhoog via parents om te zien of focus binnen een ministerie zit.
    let cur = focusNode;
    let candidate = null;
    while (cur) {
      if (cur.data && (cur.data.kind === "ministerie" || cur.data.type === "ministerie")) {
        candidate = cur;
        break;
      }
      cur = cur.parent;
    }
    if (!candidate) return null;
    // Refresh: huidige rootHierarchy heeft een verse boom; pak de
    // overeenkomende node erin op id.
    const fresh = root.find((n) => n.data && n.data.id === candidate.data.id);
    if (!fresh) return null;
    // Vereist: ministerie heeft posten met classification bewindspersoon
    // (anders is bundle nog niet geladen).
    const posten = fresh.data.posten || [];
    if (!posten.some((p) => p.classification === "bewindspersoon")) return null;
    return fresh;
  }

  function layoutMinisterieOrganogram(min, root) {
    // Zone-coordinaten. Origin (0,0) is centrum van de bewindspersonen-rij.
    // Verticale spacing strak conform officieel BZK-organogram.
    const ROW_BW = 0;
    const ROW_SG = 90;
    const ROW_CLUSTERS = 170;
    const ROW_DG = 260;
    const BW_W = 240;
    const BW_GAP = 16;
    const SG_W = 240;
    const SG_GAP = 12;
    const CL_W = 220;
    const CL_GAP = 18;
    const DG_W = 200;
    const DG_GAP = 12;
    const DIR_ROW_H = 22;

    // Reset alle node-posities; later overschrijven we wat wel zichtbaar is.
    root.each((n) => {
      n.x = NaN;
      n.y = NaN;
    });

    // Ministerie zelf staat centraal, vlak boven de bewindspersonen.
    min.x = 0;
    min.y = -70;

    // Bewindspersonen-posten direct onder het ministerie.
    const bewinds = min.children
      ? min.children.filter(
          (c) => c.data && c.data.kind === "post" && c.data.classification === "bewindspersoon",
        )
      : [];
    const bwActive = bewinds.filter((p) => postHasActiveMandaat(p));
    const bwTotalW = bwActive.length * BW_W + Math.max(0, bwActive.length - 1) * BW_GAP;
    bwActive.forEach((p, i) => {
      p.x = -bwTotalW / 2 + i * (BW_W + BW_GAP) + BW_W / 2;
      p.y = ROW_BW;
    });

    // SG-cluster (organisatieonderdeel).
    const sgOrg = min.children
      ? min.children.find(
          (c) => c.data && typeof c.data.id === "string" && c.data.id.startsWith("org:onderdeel-sg-"),
        )
      : null;

    let dgs = [];
    if (sgOrg) {
      // SG-org-tile zelf verbergen — het koppel SG + plv-SG vertegenwoordigt
      // 'm. Lijnen van ministerie naar DG's lopen straks via SG-post als
      // visuele kop.
      sgOrg.x = 0;
      sgOrg.y = (ROW_SG + ROW_DG) / 2;
      sgOrg.data._hidden = true;

      // SG en plv-SG posten zitten als 'post'-children onder sgOrg.
      const sgPosts = sgOrg.children
        ? sgOrg.children.filter((c) => c.data && c.data.kind === "post")
        : [];
      const sgPost = sgPosts.find((p) => p.data.id && /^post:sg-/.test(p.data.id));
      const plvPost = sgPosts.find((p) => p.data.id && /^post:plv-sg-/.test(p.data.id));
      if (plvPost) {
        plvPost.x = -(SG_W + SG_GAP) / 2;
        plvPost.y = ROW_SG;
      }
      if (sgPost) {
        sgPost.x = (SG_W + SG_GAP) / 2;
        sgPost.y = ROW_SG;
      }

      // Clusters (org-onderdelen met "cluster" in slug) onder de SG-rij.
      const clusters = sgOrg.children
        ? sgOrg.children.filter(
            (c) =>
              c.data &&
              c.data.kind !== "post" &&
              typeof c.data.id === "string" &&
              /^org:onderdeel-cluster-/.test(c.data.id),
          )
        : [];
      const clTotalW = clusters.length * CL_W + Math.max(0, clusters.length - 1) * CL_GAP;
      clusters.forEach((c, i) => {
        c.x = -clTotalW / 2 + i * (CL_W + CL_GAP) + CL_W / 2;
        c.y = ROW_CLUSTERS;
        // Cluster-leden (org-children of post-children) als verticale lijst eronder.
        const items = (c.children || []).filter((k) => k.data);
        items.forEach((item, j) => {
          item.x = c.x;
          item.y = c.y + 36 + j * DIR_ROW_H;
        });
      });

      // DG's = overige onderdeel-children van sgOrg (niet-cluster, niet-post).
      dgs = sgOrg.children
        ? sgOrg.children.filter(
            (c) =>
              c.data &&
              c.data.kind !== "post" &&
              !(typeof c.data.id === "string" && /^org:onderdeel-cluster-/.test(c.data.id)),
          )
        : [];
    } else {
      // Geen SG-cluster: DG's hangen mogelijk direct onder het ministerie.
      dgs = min.children
        ? min.children.filter(
            (c) =>
              c.data &&
              c.data.kind !== "post" &&
              !(typeof c.data.id === "string" && /^org:onderdeel-cluster-/.test(c.data.id)),
          )
        : [];
    }

    // DG-rij: kolom per DG, directies eronder als verticale lijst.
    const dgTotalW = dgs.length * DG_W + Math.max(0, dgs.length - 1) * DG_GAP;
    dgs.forEach((dg, i) => {
      dg.x = -dgTotalW / 2 + i * (DG_W + DG_GAP) + DG_W / 2;
      dg.y = ROW_DG;

      // DG's hebben mogelijk een eigen 'post' (DG-zelf) plus directies (org-children).
      const dgPosts = dg.children
        ? dg.children.filter((c) => c.data && c.data.kind === "post")
        : [];
      const dgDirs = dg.children
        ? dg.children.filter((c) => c.data && c.data.kind !== "post")
        : [];

      // DG-post valt samen met DG-tile (toon als compound) — toch zichtbaar
      // maken voor link-render: zet 'm op zelfde positie als DG (verborgen
      // door overlap), maar markeer als _hidden zodat we ‘m later weglaten.
      dgPosts.forEach((p) => {
        p.x = dg.x;
        p.y = dg.y;
        p._hidden = true;
      });

      // Directies als verticale lijst onder de DG.
      dgDirs.forEach((dir, j) => {
        dir.x = dg.x;
        dir.y = dg.y + 36 + j * DIR_ROW_H;
        // Eventuele afdelingen onder directies: zelfde kolom, verder naar onder.
        const sub = dir.children ? dir.children.filter((k) => k.data) : [];
        sub.forEach((s, k) => {
          s.x = dir.x;
          s.y = dir.y + (sub.length > 0 ? 4 : 0) + (j === 0 ? 0 : 0) + 14 + (k + 1) * DIR_ROW_H;
        });
      });
    });

    // Alle nodes zonder x/y worden onzichtbaar (NaN); applyFade markeert 'inactive'.
  }

  function postHasActiveMandaat(postNode) {
    const mandaten = postNode.data && postNode.data.mandaten ? postNode.data.mandaten : [];
    return mandaten.some((m) => isActiveOn(m, date));
  }

  function renderLinks() {
    const allLinks = rootHierarchy.links().filter(
      (l) =>
        Number.isFinite(l.source.x) &&
        Number.isFinite(l.source.y) &&
        Number.isFinite(l.target.x) &&
        Number.isFinite(l.target.y) &&
        !l.target.data._hidden &&
        !l.source.data._hidden,
    );
    const links = linkLayer
      .selectAll("path.link")
      .data(allLinks, (d) => `${nodeId(d.source)}->${nodeId(d.target)}`);
    links.exit().remove();
    const pathFn = viewMode === "ministerie" ? orthogonalLink : linkPath;
    links
      .enter()
      .append("path")
      .attr("class", "link")
      .merge(links)
      .attr("d", (d) => pathFn(d.source, d.target));
  }

  function renderNodes() {
    const visible = rootHierarchy
      .descendants()
      .filter((d) => Number.isFinite(d.x) && Number.isFinite(d.y) && !d.data._hidden);
    const nodes = nodeLayer.selectAll("g.node").data(visible, (d) => nodeId(d));
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
    const subtreeNodes = target
      .descendants()
      .filter((n) => Number.isFinite(n.x) && Number.isFinite(n.y) && !n.data._hidden);
    if (subtreeNodes.length === 0) return;
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

function orthogonalLink(source, target) {
  // Rechte L-vormige verbinding (organogram-stijl): vanaf source verticaal
  // half omlaag, dan horizontaal, dan verticaal naar target.
  const sy = source.y + NODE_H / 2;
  const ty = target.y - NODE_H / 2;
  const my = sy + (ty - sy) * 0.5;
  return `M${source.x},${sy} V${my} H${target.x} V${ty}`;
}

function truncate(s, n) {
  if (s.length <= n) return s;
  return s.slice(0, n - 1) + "…";
}
