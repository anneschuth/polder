import { loadJSON } from "./fetcher.js";

let panelEl = null;

export async function openPersonPanel(personId) {
  closePersonPanel();
  const slug = personId.replace(/^person:/, "");
  let data;
  try {
    data = await loadJSON(`person/${slug}.json`);
  } catch (err) {
    console.error("person bundle load failed", err);
    return;
  }

  const panel = document.createElement("aside");
  panel.className = "side-panel";

  const header = document.createElement("header");
  header.className = "side-header";
  const title = document.createElement("h2");
  title.textContent = data.name?.full || data.id;
  const meta = document.createElement("div");
  meta.className = "muted";
  meta.textContent = data.birth_year ? `geboren ${data.birth_year}` : "";
  const close = document.createElement("button");
  close.className = "overlay-close";
  close.setAttribute("aria-label", "Sluiten");
  close.textContent = "×";
  close.addEventListener("click", closePersonPanel);
  header.appendChild(title);
  if (meta.textContent) header.appendChild(meta);
  header.appendChild(close);

  const list = document.createElement("ol");
  list.className = "mandaten-timeline";

  const mandaten = [...(data.mandaten || [])].sort((a, b) =>
    (b.start_date || "").localeCompare(a.start_date || ""),
  );
  for (const m of mandaten) {
    const li = document.createElement("li");
    const role = document.createElement("div");
    role.className = "mandaat-role";
    role.textContent = m.role || m.post_id || "—";
    const dates = document.createElement("div");
    dates.className = "muted small";
    dates.textContent = `${m.start_date || "?"} → ${m.end_date || "heden"}`;
    const org = document.createElement("div");
    org.className = "muted small";
    org.textContent = m.organization_id || "";
    li.appendChild(role);
    li.appendChild(dates);
    li.appendChild(org);

    const sources = m.sources || [];
    if (sources.length) {
      const ul = document.createElement("ul");
      ul.className = "sources";
      for (const s of sources) {
        const sli = document.createElement("li");
        const a = document.createElement("a");
        a.href = s.url || "#";
        a.target = "_blank";
        a.rel = "noopener";
        a.textContent = s.id || s.url || "bron";
        sli.appendChild(a);
        ul.appendChild(sli);
      }
      li.appendChild(ul);
    }

    list.appendChild(li);
  }

  panel.appendChild(header);
  panel.appendChild(list);
  document.body.appendChild(panel);
  panelEl = panel;

  document.addEventListener("keydown", onEsc);
}

function onEsc(e) {
  if (e.key === "Escape") closePersonPanel();
}

export function closePersonPanel() {
  if (!panelEl) return;
  panelEl.remove();
  panelEl = null;
  document.removeEventListener("keydown", onEsc);
}
