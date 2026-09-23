import React, { useEffect, useMemo, useState } from "react";
import { api } from "../../api.js";
import { useApp } from "../../App.jsx";
import { EmptyState } from "../../components/ui.jsx";
import DataGrid from "../../components/DataGrid.jsx";
import { STEP_OPS, STEP_FIELDS, buildParams, paramsToValues, StepFieldsGrid } from "../../components/StepToolbar.jsx";
import { SectionCard } from "./labShared.jsx";
import { formatNumber } from "../../format.js";

let nextLocalId = 1;

/** Builds the `/api/lab/datasets/{name}/pipeline/apply` graph payload from an
 * edited linear step list — the same shape `nightingale/lab/pipeline.py`'s
 * `validate_graph` expects, including the extra `dataset_ref` node/edge a
 * join or union step needs. Node ids here are throwaway (recomputed on every
 * apply), only their uniqueness and the edge chain matter. */
function buildGraphPayload(steps) {
  const nodes = [{ id: "n0", kind: "source", params: {} }];
  const edges = [];
  let prev = "n0";
  const refs = new Set();
  steps.forEach((s, i) => {
    const id = `n${i + 1}`;
    nodes.push({ id, kind: s.op, params: s.params });
    const other = s.params?.other_dataset;
    if (other && (s.op === "join" || s.op === "union")) {
      const refId = `dataset:${other}`;
      if (!refs.has(refId)) {
        nodes.push({ id: refId, kind: "dataset_ref", dataset: other });
        refs.add(refId);
      }
      edges.push([refId, id]);
    }
    edges.push([prev, id]);
    prev = id;
  });
  return { nodes, edges };
}

export default function LabPipeline({ dataset, datasets }) {
  const { t, lang, notify, refreshDatasets } = useApp();
  const [steps, setSteps] = useState([]);
  const [selected, setSelected] = useState(null);
  const [addOp, setAddOp] = useState(STEP_OPS[0]);
  const [preview, setPreview] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const columnNames = useMemo(() => (datasets.find((d) => d.name === dataset)?.columns || []).map((c) => c.name), [datasets, dataset]);

  const load = async () => {
    if (!dataset) return;
    try {
      const g = await api.lab.pipelineGraph(dataset);
      const chain = g.nodes.filter((n) => n.kind !== "source" && n.kind !== "dataset_ref");
      setSteps(chain.map((n) => ({ localId: nextLocalId++, op: n.kind, params: n.params || {} })));
      setSelected(null);
      setPreview(null);
      setError(null);
    } catch (e) {
      setError(e.message);
    }
  };
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dataset]);

  const selectedStep = steps.find((s) => s.localId === selected);

  const addStep = () => {
    const s = { localId: nextLocalId++, op: addOp, params: {} };
    setSteps((old) => [...old, s]);
    setSelected(s.localId);
    setPreview(null);
  };
  const removeStep = (localId) => {
    setSteps((old) => old.filter((s) => s.localId !== localId));
    if (selected === localId) setSelected(null);
    setPreview(null);
  };
  const moveStep = (localId, dir) => {
    setSteps((old) => {
      const idx = old.findIndex((s) => s.localId === localId);
      const swap = idx + dir;
      if (swap < 0 || swap >= old.length) return old;
      const copy = [...old];
      [copy[idx], copy[swap]] = [copy[swap], copy[idx]];
      return copy;
    });
    setPreview(null);
  };
  const updateSelectedParams = (name, value) => {
    setSteps((old) => old.map((s) => (s.localId === selected ? { ...s, formValues: { ...(s.formValues || paramsToValues(s.op, s.params)), [name]: value } } : s)));
  };
  const commitSelectedForm = () => {
    setSteps((old) => old.map((s) => {
      if (s.localId !== selected || !s.formValues) return s;
      try {
        return { ...s, params: buildParams(s.op, s.formValues) };
      } catch (e) {
        setError(e.message);
        return s;
      }
    }));
    setPreview(null);
  };

  const runPreview = async () => {
    setBusy(true);
    setError(null);
    try {
      const graph = buildGraphPayload(steps);
      const r = await api.lab.pipelineApply(dataset, graph, true);
      setPreview(r);
    } catch (e) {
      setError(e.message);
      setPreview(null);
    } finally {
      setBusy(false);
    }
  };

  const apply = async () => {
    // eslint-disable-next-line no-alert
    if (!window.confirm(t("lab_pipeline_apply_confirm"))) return;
    setBusy(true);
    setError(null);
    try {
      const graph = buildGraphPayload(steps);
      await api.lab.pipelineApply(dataset, graph, false);
      notify(t("lab_pipeline_applied"));
      await refreshDatasets();
      await load();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  if (!dataset) return <EmptyState>{t("lab_explore_empty")}</EmptyState>;

  return (
    <div className="flex flex-col gap-4" data-testid="lab-pipeline">
      <SectionCard title={t("lab_pipeline_title")}>
        <div className="flex flex-wrap items-center gap-2 overflow-auto pb-2" data-testid="lab-pipeline-chain">
          <NodeChip label={t("lab_pipeline_source")} active={false} onClick={() => setSelected(null)} />
          {steps.map((s, i) => (
            <React.Fragment key={s.localId}>
              <span className="help">→</span>
              <NodeChip
                label={s.op}
                active={selected === s.localId}
                onClick={() => setSelected(s.localId)}
                onRemove={() => removeStep(s.localId)}
                onUp={i > 0 ? () => moveStep(s.localId, -1) : null}
                onDown={i < steps.length - 1 ? () => moveStep(s.localId, 1) : null}
                testId={`lab-pipeline-node-${i}`}
              />
            </React.Fragment>
          ))}
          <span className="help">→</span>
          <div className="flex items-center gap-1">
            <select className="field" style={{ width: "auto" }} value={addOp} onChange={(e) => setAddOp(e.target.value)}>
              {STEP_OPS.map((o) => (
                <option key={o} value={o}>{o}</option>
              ))}
            </select>
            <button type="button" className="btn btn-sm" onClick={addStep} data-testid="lab-pipeline-add">+ {t("lab_pipeline_add_step")}</button>
          </div>
        </div>

        {selectedStep ? (
          <div className="panel flex flex-col gap-2" data-testid="lab-pipeline-params">
            <div className="text-[13px] font-semibold">{t("lab_pipeline_params")}: {selectedStep.op}</div>
            <StepFieldsGrid
              fields={STEP_FIELDS[selectedStep.op] || []}
              values={selectedStep.formValues || paramsToValues(selectedStep.op, selectedStep.params)}
              setField={updateSelectedParams}
              columnNames={columnNames}
            />
            <button type="button" className="btn btn-sm self-start" onClick={commitSelectedForm} data-testid="lab-pipeline-commit">{t("common_save")}</button>
          </div>
        ) : (
          <div className="help">{t("lab_pipeline_select_node")}</div>
        )}

        {error && <div className="help" style={{ color: "var(--danger-ink)" }}>{error}</div>}
        <div className="flex gap-2">
          <button type="button" className="btn" onClick={runPreview} disabled={busy} data-testid="lab-pipeline-preview">{t("lab_pipeline_preview")}</button>
          <button type="button" className="btn btn-primary" onClick={apply} disabled={busy || !preview} data-testid="lab-pipeline-apply">{t("lab_pipeline_apply")}</button>
        </div>

        {preview && (
          <div className="flex flex-col gap-2" data-testid="lab-pipeline-preview-result">
            <div className="help">{t("lab_pipeline_preview_result")}: <span className="num">{formatNumber(preview.row_count, lang)}</span> {t("common_rows")} · <span className="num">{preview.columns.length}</span> {t("common_columns")}</div>
            {preview.preview_rows?.length > 0 && (
              <DataGrid columns={preview.columns} rows={preview.preview_rows} lang={lang} height={280} />
            )}
          </div>
        )}
      </SectionCard>
    </div>
  );
}

function NodeChip({ label, active, onClick, onRemove, onUp, onDown, testId }) {
  return (
    <div
      className="flex items-center gap-1 rounded-md px-2 py-1 text-[12px]"
      style={{ background: active ? "var(--nav-active)" : "var(--white)", border: "1px solid var(--line)", cursor: "pointer" }}
      onClick={onClick}
      data-testid={testId}
    >
      <span className="font-medium">{label}</span>
      {onUp && <button type="button" className="btn-link text-xs" onClick={(e) => { e.stopPropagation(); onUp(); }} title="up">↑</button>}
      {onDown && <button type="button" className="btn-link text-xs" onClick={(e) => { e.stopPropagation(); onDown(); }} title="down">↓</button>}
      {onRemove && <button type="button" className="btn-link text-xs" onClick={(e) => { e.stopPropagation(); onRemove(); }} title="remove">×</button>}
    </div>
  );
}
