const cache = new Map();

function dataBase() {
  const meta = document.querySelector('meta[name="organogram-data-base"]');
  return (meta && meta.content) || 'data';
}

export async function loadJSON(path) {
  if (cache.has(path)) return cache.get(path);
  const promise = fetch(`${dataBase()}/${path}`).then((r) => {
    if (!r.ok) throw new Error(`fetch failed: ${path} (${r.status})`);
    return r.json();
  });
  cache.set(path, promise);
  return promise;
}

export function preload(path) {
  if (!cache.has(path)) loadJSON(path).catch(() => cache.delete(path));
}
