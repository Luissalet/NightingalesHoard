// Thin fetch wrapper: JSON in/out, `{ error }` bodies become exceptions.
async function request(method, path, { params, body } = {}) {
  const url = new URL(import.meta.env.BASE_URL + path.replace(/^\//, ''), window.location.origin);
  for (const [key, value] of Object.entries(params || {})) {
    if (value !== undefined && value !== null && value !== "") url.searchParams.set(key, value);
  }
  const response = await fetch(url, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const text = await response.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { error: text };
  }
  if (!response.ok) throw new Error((data && data.error) || `Error ${response.status}`);
  return data;
}

const get = (path, params) => request("GET", path, { params });
const post = (path, body) => request("POST", path, { body: body ?? {} });
const del = (path, params) => request("DELETE", path, { params });
async function download(path, body) {
  const url = new URL(import.meta.env.BASE_URL + path, window.location.origin);
  const response = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.error || `Error ${response.status}`);
  }
  return response.blob();
}

export const api = {
  health: () => get("/api/health"),
  status: () => get("/api/status"),

  sources: () => get("/api/sources"),
  ingest: (body) => post("/api/sources/ingest", body),
  hoardPresets: (lang) => get("/api/sources/hoard-presets", { lang }),

  datasets: () => get("/api/datasets"),
  refresh: (name) => post(`/api/datasets/${encodeURIComponent(name)}/refresh`),
  profile: (name, version) => get(`/api/datasets/${encodeURIComponent(name)}/profile`, { version }),
  frequencies: (name, column, version, offset = 0, limit = 50) => get(`/api/datasets/${encodeURIComponent(name)}/frequencies`, { column, version, offset, limit }),
  compareVersions: (name, version_a, version_b) => get(`/api/datasets/${encodeURIComponent(name)}/versions/compare`, { version_a, version_b }),
  compareRows: (name, body) => post(`/api/datasets/${encodeURIComponent(name)}/versions/rows`, body),
  correlation: (name) => get(`/api/datasets/${encodeURIComponent(name)}/correlation`),
  preview: (name, version, limit) => get(`/api/datasets/${encodeURIComponent(name)}/preview`, { version, limit }),
  lineage: (name) => get(`/api/datasets/${encodeURIComponent(name)}/lineage`),
  recipe: (name, action) => get(`/api/datasets/${encodeURIComponent(name)}/recipe`, { action }),
  transform: (name, op, params, preview) => post(`/api/datasets/${encodeURIComponent(name)}/transform`, { op, params, preview }),
  undo: (name, steps = 1) => post(`/api/datasets/${encodeURIComponent(name)}/undo`, { steps }),
  redo: (name, steps = 1) => post(`/api/datasets/${encodeURIComponent(name)}/redo`, { steps }),
  joinPreview: (name, other_dataset, on, how) => post(`/api/datasets/${encodeURIComponent(name)}/join-preview`, { other_dataset, on, how }),
  datasetDependents: (name) => get(`/api/datasets/${encodeURIComponent(name)}/dependents`),
  datasetDelete: (name, force) => del(`/api/datasets/${encodeURIComponent(name)}`, force ? { force: "true" } : undefined),

  workbooks: () => get("/api/sheets"),
  workbook: (id) => get(`/api/sheets/${id}`),
  workbookCompile: (body) => post("/api/sheets/compile", body),
  workbookUpload: async (file) => {
    const form = new FormData();
    form.append("file", file);
    const r = await fetch(new URL(import.meta.env.BASE_URL + "api/sheets/upload", window.location.origin), { method: "POST", body: form });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || `Error ${r.status}`);
    return data;
  },
  workbookEvaluate: (id, inputs, as_of) => post(`/api/sheets/${id}/evaluate`, { inputs, as_of }),
  workbookCheck: (id) => post(`/api/sheets/${id}/check`),
  workbookVerify: (id, body) => post(`/api/sheets/${id}/verify`, body),
  workbookTrace: (id, cell) => get(`/api/sheets/${id}/trace`, { cell }),
  workbookDelete: (id) => del(`/api/sheets/${id}`),

  query: (sql, limit) => post("/api/query", { sql, limit }),

  qualityDefine: (name, body) => post(`/api/datasets/${encodeURIComponent(name)}/quality`, body),
  qualityReport: (name) => get(`/api/datasets/${encodeURIComponent(name)}/quality`),
  qualityRun: (name, rule_id) => post(`/api/datasets/${encodeURIComponent(name)}/quality/run`, { rule_id }),
  qualityDelete: (ruleId) => del(`/api/quality/${ruleId}`),

  chartCreate: (body) => post("/api/charts", body),
  chartList: () => get("/api/charts"),
  chartGet: (id, image, lang) => get(`/api/charts/${id}`, { image, lang }),
  chartDelete: (id) => del(`/api/charts/${id}`),

  dashboardCreate: (body) => post("/api/dashboards", body),
  dashboardList: () => get("/api/dashboards"),
  dashboardGet: (id) => get(`/api/dashboards/${id}`),
  dashboardAddItem: (id, item) => post(`/api/dashboards/${id}/items`, { item }),
  dashboardAction: (id, action) => post(`/api/dashboards/${id}/actions`, { action }),
  dashboardUndo: (id) => post(`/api/dashboards/${id}/undo`, {}),
  dashboardRedo: (id) => post(`/api/dashboards/${id}/redo`, {}),
  dashboardHistory: (id, limit = 20) => get(`/api/dashboards/${id}/history`, { limit }),

  dac: {
    semanticGet: () => get("/api/dac/semantic"),
    semanticPut: (text) => request("PUT", "/api/dac/semantic", { body: { text } }),
    semanticValidate: (text) => post("/api/dac/semantic/validate", { text }),
    semanticSuggest: (dataset) => get("/api/dac/semantic/suggest", { dataset }),

    list: () => get("/api/dac/dashboards"),
    create: (text) => post("/api/dac/dashboards", { text }),
    get: (slug) => get(`/api/dac/dashboards/${encodeURIComponent(slug)}`),
    put: (slug, text) => request("PUT", `/api/dac/dashboards/${encodeURIComponent(slug)}`, { body: { text } }),
    delete: (slug) => del(`/api/dac/dashboards/${encodeURIComponent(slug)}`),
    rename: (slug, name) => post(`/api/dac/dashboards/${encodeURIComponent(slug)}/rename`, { name }),
    validate: (slug, text) => post(`/api/dac/dashboards/${encodeURIComponent(slug)}/validate`, text !== undefined ? { text } : {}),
    render: (slug, filters) => post(`/api/dac/dashboards/${encodeURIComponent(slug)}/render`, { filters: filters || {} }),
    history: (slug) => get(`/api/dac/dashboards/${encodeURIComponent(slug)}/history`),
    diff: (slug, a, b) => get(`/api/dac/dashboards/${encodeURIComponent(slug)}/diff`, { a, b }),
    export: (slug, filters) => post(`/api/dac/dashboards/${encodeURIComponent(slug)}/export`, { filters: filters || {} }),
    importFromItems: (dashboardId) => post("/api/dac/dashboards/import", { dashboard_id: dashboardId }),
  },

  modelTrain: (body) => post("/api/models/train", body),
  modelCluster: (body) => post("/api/models/cluster", body),
  modelPca: (body) => post("/api/models/pca", body),
  modelAnomaly: (body) => post("/api/models/anomaly", body),
  modelForecast: (body) => post("/api/models/forecast", body),
  models: (dataset) => get("/api/models", { dataset }),

  exportDataset: (body) => post("/api/export", body),
  exportDownload: (body) => download("api/export/download", body),
  workbookInventory: (dataset) => get(`/api/workbooks/${encodeURIComponent(dataset)}`),
  workbookDownload: (body) => download("api/export/workbook/download", body),

  log: (params) => get("/api/log", params),
  logGet: (id) => get(`/api/log/${id}`),

  ask: (question, datasets) => post("/api/ask", { question, datasets }),
  askAvailable: () => get("/api/ask/available"),

  lab: {
    eda: (name) => get(`/api/lab/datasets/${encodeURIComponent(name)}/eda`),
    qualityScore: (name) => get(`/api/lab/datasets/${encodeURIComponent(name)}/quality-score`),
    drift: (body) => post("/api/lab/drift", body),
    compareCurves: (body) => post("/api/lab/compare-curves", body),

    backends: (task) => get("/api/lab/backends", { task }),
    train: (body) => post("/api/lab/models/train", body),
    tune: (body) => post("/api/lab/models/tune", body),
    modelsList: (dataset) => get("/api/lab/models", { dataset }),
    modelGet: (id) => get(`/api/lab/models/${id}`),
    modelsCompare: (modelIds) => post("/api/lab/models/compare", { model_ids: modelIds }),
    modelDelete: (id) => del(`/api/lab/models/${id}`),
    evaluate: (id, body) => post(`/api/lab/models/${id}/evaluate`, body),
    explain: (id, body) => post(`/api/lab/models/${id}/explain`, body),
    optimize: (id, body) => post(`/api/lab/models/${id}/optimize`, body),
    pareto: (body) => post("/api/lab/pareto", body),
    report: (body) => post("/api/lab/report", body),
    reportDownloadUrl: (path) => `${import.meta.env.BASE_URL}api/lab/report/download?path=${encodeURIComponent(path)}`,

    pipelineGraph: (name) => get(`/api/lab/datasets/${encodeURIComponent(name)}/pipeline`),
    pipelineApply: (name, graph, dryRun) => post(`/api/lab/datasets/${encodeURIComponent(name)}/pipeline/apply`, { graph, dry_run: dryRun }),
  },
};
