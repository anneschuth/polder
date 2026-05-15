const listeners = new Set();

export function getState() {
  return parseHash(location.hash);
}

export function setState(partial, replace = false) {
  const cur = getState();
  const next = { ...cur, ...partial };
  for (const k of Object.keys(next)) {
    if (next[k] == null || next[k] === "") delete next[k];
  }
  const hash = encodeHash(next);
  if (hash === location.hash) return;
  if (replace) history.replaceState(null, "", hash || "#");
  else location.hash = hash;
}

export function onStateChange(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

window.addEventListener("hashchange", () => {
  const state = getState();
  for (const fn of listeners) fn(state);
});

function parseHash(hash) {
  const out = {};
  const raw = (hash || "").replace(/^#/, "");
  if (!raw) return out;
  for (const part of raw.split("&")) {
    const [k, v] = part.split("=");
    if (k) out[decodeURIComponent(k)] = v ? decodeURIComponent(v) : "";
  }
  return out;
}

function encodeHash(obj) {
  const parts = [];
  for (const [k, v] of Object.entries(obj)) {
    parts.push(`${encodeURIComponent(k)}=${encodeURIComponent(v)}`);
  }
  return parts.length ? `#${parts.join("&")}` : "";
}
