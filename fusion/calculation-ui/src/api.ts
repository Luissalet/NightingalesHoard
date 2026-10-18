// Thin fetch wrapper over the backend. All calls are same-origin (the app
// is served from the same host:port it calls), so no base URL is needed.

export class ApiError extends Error {
  code: string;
  status: number;
  constructor(code: string, message: string, status: number) {
    super(message);
    this.code = code;
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(import.meta.env.BASE_URL + path.replace(/^\//, ''), {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
  });
  if (!res.ok) {
    let code = "error";
    let message = res.statusText;
    try {
      const body = await res.json();
      const detail = body.detail ?? body;
      code = detail.error ?? code;
      message = detail.message ?? message;
    } catch {
      // no JSON body
    }
    throw new ApiError(code, message, res.status);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

const post = <T>(path: string, body: unknown) =>
  request<T>(path, { method: "POST", body: JSON.stringify(body) });
const get = <T>(path: string) => request<T>(path);
const del = <T>(path: string) => request<T>(path, { method: "DELETE" });

export interface HealthInfo {
  service: string;
  name: string;
  version: string;
  status: string;
  computations_logged: number;
  datasets_registered: number;
}

export interface CalcResult {
  id: string;
  cite: string;
  input: string;
  exact: string;
  decimal: number | string | null;
  digits: number;
  is_exact: boolean;
  latex: string | null;
  value?: boolean | number;
  factors?: Record<string, number>;
}

export interface MathResult {
  id: string;
  cite: string;
  result: unknown;
  latex?: string;
  numeric?: number | null;
  verified?: boolean | null;
  variables?: string[];
  count?: number;
  steps_hint?: string;
  [key: string]: unknown;
}

export interface UnitsResult {
  id: string;
  cite: string;
  input: string;
  to: string;
  from_magnitude: number;
  from_unit: string;
  to_magnitude: number;
  to_unit: string;
  formatted: string;
  precision_note?: string;
}

export interface DateResult {
  id: string;
  cite: string;
  [key: string]: unknown;
}

export interface StatsResult {
  id: string;
  cite: string;
  test: string;
  interpretation?: string;
  [key: string]: unknown;
}

export interface DatasetSummary {
  name: string;
  source_path: string;
  kind: string;
  linked: boolean;
  row_count: number;
  columns: { name: string; type: string }[];
  updated_at: string;
}

export interface ColumnProfile {
  type: string;
  nulls_pct: number;
  distinct_approx: number;
  min?: unknown;
  max?: unknown;
  mean?: number | null;
  sd?: number | null;
  histogram?: number[];
  top_values?: { value: unknown; count: number }[];
}

export interface DatasetDetail extends DatasetSummary {
  profile: Record<string, ColumnProfile>;
  sample_rows: Record<string, unknown>[];
}

export interface QueryResult {
  id: string;
  cite: string;
  columns: { name: string; type: string }[];
  rows: Record<string, unknown>[];
  row_count: number;
  total_rows: number | null;
  truncated: boolean;
  elapsed_ms: number;
}

export interface ChartResult {
  id: string;
  cite: string;
  spec: object;
  spec_summary: { mark: unknown; encoding: string[] };
  row_count: number;
  truncated: boolean;
  png_base64: string;
}

export interface LogItem {
  seq: number;
  id: string;
  engine: string;
  operation: string;
  input: Record<string, unknown> | null;
  output: Record<string, unknown> | null;
  ok: boolean;
  error: string | null;
  elapsed_ms: number;
  source: "ui" | "agent";
  chart_path: string | null;
  created_at: string;
}

export interface BackendCapability {
  capability: string;
  provider: string | null;
  url: string | null;
  model: string | null;
  api: string | null;
  state: "resolved" | "unavailable";
  reason: string;
}

export interface BackendStatus {
  capabilities: Record<string, BackendCapability>;
  config: {
    only_resident: boolean;
    faustus_urls: string[];
    token_set: boolean;
    /** what data/backend.json holds (never the token) */
    saved: { faustus_url: string | null; capabilities: Record<string, { url: string | null; model: string | null }> };
    /** why data/backend.json is being ignored, if it is */
    error: string | null;
  };
  app: Record<string, { name: string; bundled: boolean }>;
}

export interface BackendConfigUpdate {
  faustus_url?: string;
  faustus_token?: string;
  only_resident?: boolean;
  /** "" clears a field; a missing field is left as it is */
  capabilities?: Record<string, { url?: string; model?: string }>;
}

export interface AskResult {
  id: string;
  cite: string;
  question: string;
  datasets: string[];
  sql: string;
  model: string | null;
  columns: { name: string; type: string }[];
  rows: Record<string, unknown>[];
  row_count: number;
  total_rows: number | null;
  truncated: boolean;
  elapsed_ms: number;
  chart_suggestion: { kind: string; x: string; y: string } | null;
}

export interface Cell {
  id: number;
  engine: string;
  input: string;
  result: Record<string, unknown> | null;
  position: number;
}

export const api = {
  health: () => get<HealthInfo>("/api/health"),

  calc: (expression: string, precision = 15) =>
    post<CalcResult>("/api/ui/calc", { expression, precision }),

  math: (payload: Record<string, unknown>) => post<MathResult>("/api/ui/math", payload),

  unitsConvert: (quantity: string, to: string) =>
    post<UnitsResult>("/api/ui/units_convert", { quantity, to }),

  unitsCheck: (expression: string) => post<Record<string, unknown>>("/api/units/check", { expression }),

  stats: (payload: Record<string, unknown>) => post<StatsResult>("/api/ui/stats", payload),

  dateCalc: (payload: Record<string, unknown>) => post<DateResult>("/api/ui/date_calc", payload),

  datasets: () => get<{ datasets: DatasetSummary[] }>("/api/datasets"),
  datasetDetail: (name: string) => get<DatasetDetail>(`/api/datasets/${encodeURIComponent(name)}`),
  registerDataset: (path: string, name?: string, options?: Record<string, unknown>) =>
    post<Record<string, unknown>>("/api/ui/data_register", { path, name, options }),
  query: (sql: string, limit = 50) => post<QueryResult>("/api/ui/data_query", { sql, limit }),
  chart: (payload: Record<string, unknown>) => post<ChartResult>("/api/ui/data_chart", payload),
  exportCsv: async (sql: string, lang: "en" | "es" = "en"): Promise<Blob> => {
    // a Spanish Excel expects ';' fields and a ',' decimal point
    const res = await fetch(`${import.meta.env.BASE_URL}api/export/csv`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sql, lang }),
    });
    if (!res.ok) {
      let message = res.statusText;
      try {
        message = (await res.json()).message ?? message;
      } catch {
        /* not JSON */
      }
      throw new ApiError("export", message, res.status);
    }
    return res.blob();
  },

  log: (params: { limit?: number; engine?: string; source?: string; query?: string } = {}) => {
    const q = new URLSearchParams();
    if (params.limit) q.set("limit", String(params.limit));
    if (params.engine) q.set("engine", params.engine);
    if (params.source) q.set("source", params.source);
    if (params.query) q.set("query", params.query);
    return get<{ items: LogItem[] }>(`/api/log?${q.toString()}`);
  },
  logItem: (id: string) => get<LogItem>(`/api/log/${id}`),
  rerun: (id: string) => post<Record<string, unknown>>(`/api/log/${id}/rerun`, {}),
  agentCalls: (limit = 30) => get<{ items: LogItem[] }>(`/api/agent-calls?limit=${limit}`),

  cells: () => get<{ cells: Cell[] }>("/api/cells"),
  appContext: (kind: string, object_id: string) =>
    post<{ project: null; active_objects: { kind: string; object_id: string; title: string }[] }>("/api/ui/app_context", { kind, object_id }),
  patchCell: (id: number, patch: { engine?: string; input?: string }) =>
    post<Cell>("/api/ui/notebook_cell_patch", { id, patch }),
  createCell: (engine: string, input: string) => post<Cell>("/api/cells", { engine, input }),
  deleteCell: (id: number) => del<{ deleted: boolean }>(`/api/cells/${id}`),

  backend: () => get<BackendStatus>("/api/backend"),
  backendConfig: (body: BackendConfigUpdate) => request<{ ok: boolean; token_set: boolean }>("/api/backend/config", { method: "PUT", body: JSON.stringify(body) }),
  backendRecheck: () => post<Record<string, BackendCapability>>("/api/backend/recheck", {}),

  ask: (question: string, datasets: string[]) => post<AskResult>("/api/ui/data_ask", { question, datasets }),
};
