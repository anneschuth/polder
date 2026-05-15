const FUSE_OPTIONS = {
  keys: [
    { name: "label", weight: 2 },
    { name: "label_full", weight: 1 },
  ],
  threshold: 0.35,
  ignoreLocation: true,
  includeScore: true,
};

export function initSearch(onSelect) {
  const input = document.getElementById("search");
  const results = document.getElementById("search-results");

  let entries = [];
  let fuse = null;

  function setEntries(items) {
    entries = items;
    fuse = new Fuse(items, FUSE_OPTIONS);
  }

  function clear() {
    results.innerHTML = "";
    results.hidden = true;
  }

  input.addEventListener("input", () => {
    const q = input.value.trim();
    if (!q || !fuse) {
      clear();
      return;
    }
    const matches = fuse.search(q).slice(0, 12);
    if (!matches.length) {
      clear();
      return;
    }
    renderResults(matches);
  });

  input.addEventListener("blur", () => setTimeout(clear, 150));
  input.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      input.value = "";
      clear();
    }
  });

  function renderResults(matches) {
    results.innerHTML = "";
    for (const { item } of matches) {
      const div = document.createElement("div");
      div.className = "search-result";
      const label = document.createElement("strong");
      label.textContent = item.label || item.id;
      const kind = document.createElement("span");
      kind.className = "kind-tag";
      kind.textContent = item._kind;
      div.appendChild(label);
      div.appendChild(kind);
      if (item.label_full && item.label_full !== item.label) {
        const sub = document.createElement("div");
        sub.className = "muted small";
        sub.textContent = item.label_full;
        div.appendChild(sub);
      }
      div.addEventListener("mousedown", (e) => {
        e.preventDefault();
        input.value = item.label || "";
        clear();
        onSelect(item);
      });
      results.appendChild(div);
    }
    results.hidden = false;
  }

  return {
    setEntries,
    indexFromHierarchy(rootData) {
      const out = [];
      walk(rootData, out);
      setEntries(out);
    },
    findBest(q) {
      if (!fuse || !q) return null;
      const [first] = fuse.search(q);
      return first ? first.item : null;
    },
  };
}

function walk(node, out) {
  if (!node) return;
  if (node.id && node.label && node.kind !== "root") {
    out.push({
      id: node.id,
      label: node.label,
      label_full: node.label_full || node.label,
      _kind: node.kind || (node.type === "ministerie" ? "ministerie" : "node"),
    });
  }
  for (const c of node.children || []) walk(c, out);
  for (const p of node.posten || []) {
    if (p.id && p.label) {
      out.push({ id: p.id, label: p.label, label_full: p.label, _kind: "post" });
    }
    for (const m of p.mandaten || []) {
      if (m.person_id && m.person_label) {
        out.push({
          id: m.person_id,
          label: m.person_label,
          label_full: m.role || m.person_label,
          _kind: "persoon",
        });
      }
    }
  }
}
