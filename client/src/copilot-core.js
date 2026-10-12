// Pure helpers behind window.hoardCopilot: no DOM or React at import time, so they run under plain `node --test`.

export const CONTEXT_REQUEST = "hoard:copilot-context-request";
export const REFRESH_EVENT = "nightingale:copilot-updated";

const NO_OBJECT = new Set(["", "new", "undefined", "null"]);
const MAX_FIELD_CHARS = 6000;

/** True when a route segment names no stored object ("new", empty, placeholders). */
export function isNoObjectId(id) {
  return id === undefined || id === null || NO_OBJECT.has(String(id).trim().toLowerCase());
}

const decode = (segment) => {
  if (!segment) return "";
  try { return decodeURIComponent(segment); } catch { return segment; }
};

/** Same grammar as the app's hash router: `#/<page>/<param>/<more>?query`. The page defaults to "datasets". */
export function parseRoute(hash) {
  const [path] = String(hash || "").replace(/^#\/?/, "").split("?");
  const parts = path.split("/");
  const rest = parts.slice(1).map(decode);
  const id = rest[0] || null;
  return { page: parts[0] || "datasets", id: isNoObjectId(id) ? null : id, rest };
}

const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

/** Keys of `current` whose value differs from `initial` (deep compare); a key missing from `current` is not reported. */
export function changedFields(initial, current) {
  const base = initial || {};
  return Object.fromEntries(Object.entries(current || {}).filter(([key, value]) => !same(value, base[key])));
}

const clip = (value) => (typeof value === "string" && value.length > MAX_FIELD_CHARS
  ? `${value.slice(0, MAX_FIELD_CHARS)}… [${value.length - MAX_FIELD_CHARS} more characters]`
  : value);

/** Snapshot a form hands to the copilot. `objectId` is omitted for create forms; very long text fields are clipped. */
export function formSnapshot(kind, { objectId, initial, current } = {}) {
  const fields = changedFields(initial, current);
  const shown = Object.fromEntries(Object.entries(fields).map(([k, v]) => [k, clip(v)]));
  return { kind, ...(isNoObjectId(objectId) ? {} : { object_id: String(objectId) }), dirty: Object.keys(fields).length > 0, fields: shown };
}

/**
 * Ask every mounted form and view (they answer synchronously). `editor` is the most relevant form answer (the last
 * dirty one, else the last); `active` lists the `{ kind, id }` objects the visible views say they are showing.
 */
export function collectEditor(dispatch) {
  const answers = [];
  const active = [];
  try {
    dispatch({
      provide: (snapshot) => { if (snapshot && typeof snapshot === "object" && snapshot.kind) answers.push(snapshot); },
      provideActive: (kind, ids) => {
        for (const id of [].concat(ids ?? [])) {
          if (kind && !isNoObjectId(id)) active.push({ kind: String(kind), id: String(id) });
        }
      },
    });
  } catch { /* a broken form must not break the turn */ }
  const dirty = answers.filter((a) => a.dirty);
  const editor = dirty.length ? dirty[dirty.length - 1] : answers[answers.length - 1];
  const seen = new Set();
  return { editor, active: active.filter((o) => { const key = `${o.kind}:${o.id}`; return seen.has(key) ? false : (seen.add(key), true); }) };
}

const hashFor = (path, id) => `#/${path}/${encodeURIComponent(id)}`;

/**
 * How each object kind is read through the app's client API (real calls only) and turned into an active object.
 * A resolver returns `{ title, url }` or null when the id does not exist.
 */
export const RESOLVERS = {
  dataset: async (api, id) => {
    const found = ((await api.datasets())?.datasets || []).find((d) => d.name === id);
    return found && { title: found.name, url: hashFor("datasets", found.name) };
  },
  dashboard: async (api, id) => {
    const found = ((await api.dashboardList())?.dashboards || []).find((d) => String(d.id) === id);
    return found && { title: found.name || id, url: "#/dashboards" };
  },
  "dashboard-code": async (api, id) => {
    const found = ((await api.dac.list())?.dashboards || []).find((d) => d.slug === id);
    return found && { title: found.name || id, url: "#/dashboards-code" };
  },
  "lab-model": async (api, id) => {
    const found = ((await api.lab.modelsList())?.models || []).find((m) => String(m.id) === id);
    return found && { title: found.name || id, url: "#/lab/models" };
  },
  "log-entry": async (api, id) => {
    const entry = await api.logGet(id);
    return entry && entry.op_id && { title: `${entry.op_id} ${entry.op || ""}`.trim(), url: "#/log" };
  },
};

/** Objects implied by the route alone, used for a kind the mounted views did not report. */
export function routeObjects(route) {
  if (route.page === "datasets" && route.id) return [{ kind: "dataset", id: route.id }];
  if (route.page === "lab" && route.rest[1] && !isNoObjectId(route.rest[1])) return [{ kind: "dataset", id: route.rest[1] }];
  return [];
}

/**
 * Build `{ project?, active_objects?, editor? }` for the current view. Every part is optional: a failing read omits
 * that object, an id that does not resolve (or "new") means "no object", and the promise never rejects. Nightingale
 * has no project concept, so `project` is never set.
 *  - api: the app's client API
 *  - ask: `(detail) => void` that dispatches the context request to the mounted forms and views
 */
export async function buildContext({ hash, api, ask }) {
  const out = { active_objects: [] }; // possibly empty: overrides the launcher's guesses from the URL
  let route = { page: "datasets", id: null, rest: [] };
  try { route = parseRoute(hash); } catch { /* default route */ }

  let editor;
  let wanted = [];
  try {
    const collected = collectEditor(ask);
    editor = collected.editor;
    wanted = collected.active;
  } catch { /* omit the editor part */ }
  try {
    const reported = new Set(wanted.map((o) => o.kind));
    wanted = wanted.concat(routeObjects(route).filter((o) => !reported.has(o.kind)));
  } catch { /* no route objects */ }

  const objects = [];
  for (const { kind, id } of wanted.slice(0, 8)) {
    const resolve = RESOLVERS[kind];
    if (!resolve) continue;
    try {
      const hit = await resolve(api, id);
      if (hit) objects.push({ kind, object_id: id, title: hit.title, ...(hit.url ? { url: hit.url } : {}) });
    } catch { /* unresolved id or backend down: no object */ }
  }
  out.active_objects = objects; // possibly empty: overrides the launcher's guesses from the URL
  if (editor) out.editor = editor;
  return out;
}
