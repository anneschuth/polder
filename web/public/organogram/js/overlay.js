let overlayEl = null;

export function openFlatOverlay(data, onItemClick) {
  closeOverlay();

  const overlay = document.createElement("div");
  overlay.className = "overlay-backdrop";
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) closeOverlay();
  });

  const panel = document.createElement("div");
  panel.className = "overlay-panel";
  panel.addEventListener("click", (e) => e.stopPropagation());

  const header = document.createElement("header");
  header.className = "overlay-header";
  const title = document.createElement("h2");
  title.textContent = `${data.label} (${data.count})`;
  const close = document.createElement("button");
  close.className = "overlay-close";
  close.textContent = "×";
  close.setAttribute("aria-label", "Sluiten");
  close.addEventListener("click", closeOverlay);
  header.appendChild(title);
  header.appendChild(close);

  const filter = document.createElement("input");
  filter.type = "search";
  filter.placeholder = "Filteren…";
  filter.className = "overlay-filter";

  const list = document.createElement("ul");
  list.className = "overlay-list";

  const items = data.items || [];
  renderItems(list, items, onItemClick);

  filter.addEventListener("input", () => {
    const q = filter.value.trim().toLowerCase();
    const filtered = q
      ? items.filter(
          (it) =>
            (it.label || "").toLowerCase().includes(q) ||
            (it.label_full || "").toLowerCase().includes(q),
        )
      : items;
    renderItems(list, filtered, onItemClick);
  });

  panel.appendChild(header);
  panel.appendChild(filter);
  panel.appendChild(list);
  overlay.appendChild(panel);
  document.body.appendChild(overlay);
  overlayEl = overlay;

  document.addEventListener("keydown", onEsc);
  filter.focus();
}

function renderItems(list, items, onItemClick) {
  list.innerHTML = "";
  for (const it of items) {
    const li = document.createElement("li");
    const clickable = typeof onItemClick === "function" && it.id;
    // Klikbare items zijn een <button> (toetsenbord + screenreader);
    // niet-resolvebare items blijven platte tekst.
    const row = document.createElement(clickable ? "button" : "div");
    row.className = "overlay-item";
    if (clickable) {
      row.type = "button";
      row.addEventListener("click", () => onItemClick(it));
    }
    const strong = document.createElement("strong");
    strong.textContent = it.label || it.id;
    row.appendChild(strong);
    if (it.label_full && it.label_full !== it.label) {
      const span = document.createElement("span");
      span.className = "muted";
      span.textContent = it.label_full;
      row.appendChild(span);
    }
    li.appendChild(row);
    list.appendChild(li);
  }
}

function onEsc(e) {
  if (e.key === "Escape") closeOverlay();
}

function closeOverlay() {
  if (!overlayEl) return;
  overlayEl.remove();
  overlayEl = null;
  document.removeEventListener("keydown", onEsc);
}
