import test from "node:test";
import assert from "node:assert/strict";
import {
  RESOLVERS, buildContext, changedFields, collectEditor, formSnapshot, isNoObjectId, parseRoute, routeObjects,
} from "./src/copilot-core.js";

const DATASETS = { datasets: [{ name: "sales", row_count: 10 }, { name: "my data", row_count: 3 }] };
const makeApi = (over = {}) => ({
  datasets: async () => DATASETS,
  dashboardList: async () => ({ dashboards: [{ id: 7, name: "KPIs" }] }),
  dac: { list: async () => ({ dashboards: [{ slug: "weekly", name: "Weekly" }] }) },
  lab: { modelsList: async () => ({ models: [{ id: "mdl_1", name: "churn rf" }, { id: "mdl_2", name: "ltv" }] }) },
  logGet: async (id) => (id === "N-000001" ? { op_id: id, op: "transform" } : null),
  ...over,
});
const noForms = () => {};
const views = (...reports) => ({ provideActive }) => reports.forEach(([kind, ids]) => provideActive(kind, ids));

test("parseRoute follows the hash router (default page datasets) and treats new/blank ids as no object", () => {
  assert.deepEqual(parseRoute("#/datasets/sales"), { page: "datasets", id: "sales", rest: ["sales"] });
  assert.deepEqual(parseRoute("#/datasets/my%20data?x=1"), { page: "datasets", id: "my data", rest: ["my data"] });
  assert.deepEqual(parseRoute("#/lab/models/sales"), { page: "lab", id: "models", rest: ["models", "sales"] });
  assert.equal(parseRoute("#/datasets/new").id, null);
  assert.equal(parseRoute("#/datasets/").id, null);
  assert.equal(parseRoute("").page, "datasets");
  assert.equal(parseRoute("#/settings").id, null);
  assert.equal(parseRoute("#/datasets/%E0%A4%A").id, "%E0%A4%A");
  assert.equal(isNoObjectId("NEW"), true);
  assert.equal(isNoObjectId("sales"), false);
});

test("changedFields reports only differing keys, deeply, including cleared values", () => {
  assert.deepEqual(changedFields({ a: 1, b: [1, 2], c: "x" }, { a: 1, b: [1, 3], c: "" }), { b: [1, 3], c: "" });
  assert.deepEqual(changedFields(undefined, { a: 1 }), { a: 1 });
  assert.deepEqual(changedFields({ a: 1, p: { q: 1 } }, { a: 1, p: { q: 1 } }), {});
});

test("formSnapshot: edit forms carry object_id, create forms do not, long text is clipped", () => {
  const edit = formSnapshot("dashboard-code", { objectId: "weekly", initial: { text: "a" }, current: { text: "b" } });
  assert.deepEqual(edit, { kind: "dashboard-code", object_id: "weekly", dirty: true, fields: { text: "b" } });
  const draft = formSnapshot("chart-draft", { initial: { title: "", kind: "bar" }, current: { title: "Sales", kind: "bar" } });
  assert.deepEqual(draft, { kind: "chart-draft", dirty: true, fields: { title: "Sales" } });
  assert.equal("object_id" in formSnapshot("chart-draft", { objectId: "new", initial: {}, current: {} }), false);
  assert.equal(formSnapshot("x", { objectId: "p", initial: { a: 1 }, current: { a: 1 } }).dirty, false);
  const long = formSnapshot("dashboard-code", { objectId: "w", initial: { text: "" }, current: { text: "y".repeat(7000) } });
  assert.ok(long.fields.text.length < 6100 && long.fields.text.includes("1000 more characters"));
});

test("collectEditor prefers the last dirty answer and gathers deduplicated active objects", () => {
  const { editor, active } = collectEditor(({ provide, provideActive }) => {
    provide({ kind: "chart-draft", dirty: false, fields: {} });
    provide({ kind: "quality-rule-draft", dirty: true, fields: { name: "r" } });
    provide({ kind: "other", dirty: false, fields: {} });
    provideActive("dataset", "sales");
    provideActive("dataset", "sales");
    provideActive("lab-model", ["mdl_1", "new", 2]);
    provideActive("dashboard", null);
    provide(null);
  });
  assert.equal(editor.kind, "quality-rule-draft");
  assert.deepEqual(active, [{ kind: "dataset", id: "sales" }, { kind: "lab-model", id: "mdl_1" }, { kind: "lab-model", id: "2" }]);
  assert.equal(collectEditor(() => { throw new Error("boom"); }).editor, undefined);
});

test("routeObjects: dataset in the datasets route and in the lab dataset segment only", () => {
  assert.deepEqual(routeObjects(parseRoute("#/datasets/sales")), [{ kind: "dataset", id: "sales" }]);
  assert.deepEqual(routeObjects(parseRoute("#/lab/explore/sales")), [{ kind: "dataset", id: "sales" }]);
  assert.deepEqual(routeObjects(parseRoute("#/lab/explore/")), []);
  assert.deepEqual(routeObjects(parseRoute("#/charts/12")), []);
});

test("views report what they show; every object is read through the client API", async () => {
  const ctx = await buildContext({ hash: "#/dashboards", api: makeApi(), ask: views(["dataset", "my data"], ["dashboard", 7], ["dashboard-code", "weekly"], ["lab-model", ["mdl_2"]], ["log-entry", "N-000001"]) });
  assert.deepEqual(ctx.active_objects.map((o) => [o.kind, o.object_id, o.title]), [
    ["dataset", "my data", "my data"], ["dashboard", "7", "KPIs"], ["dashboard-code", "weekly", "Weekly"], ["lab-model", "mdl_2", "ltv"], ["log-entry", "N-000001", "N-000001 transform"],
  ]);
  assert.equal(ctx.active_objects[0].url, "#/datasets/my%20data");
  assert.equal("project" in ctx, false);
  assert.equal("editor" in ctx, false);
});

test("the route supplies the dataset when no view reported one", async () => {
  const ctx = await buildContext({ hash: "#/datasets/sales", api: makeApi(), ask: noForms });
  assert.deepEqual(ctx.active_objects.map((o) => [o.kind, o.object_id]), [["dataset", "sales"]]);
  const reported = await buildContext({ hash: "#/datasets/sales", api: makeApi(), ask: views(["dataset", "my data"]) });
  assert.deepEqual(reported.active_objects.map((o) => o.object_id), ["my data"]);
});

test("new, blank and unresolvable ids mean no object and never throw", async () => {
  const calls = [];
  const api = makeApi({ datasets: async () => { calls.push("datasets"); return DATASETS; } });
  assert.deepEqual(await buildContext({ hash: "#/datasets/new", api, ask: views(["dataset", "new"], ["dashboard", ""], ["dashboard-code", null]) }), {});
  assert.deepEqual(calls, []);
  assert.deepEqual(await buildContext({ hash: "#/datasets/gone", api, ask: views(["dataset", "gone"], ["dashboard", 99], ["log-entry", "N-9"], ["mystery", "x"]) }), {});
  assert.deepEqual(calls, ["datasets"]);
});

test("a rejecting or malformed API loses only that object", async () => {
  const api = makeApi({ datasets: async () => { throw new Error("down"); }, dashboardList: async () => null, dac: { list: async () => { throw new Error("x"); } } });
  const ctx = await buildContext({ hash: "#/dashboards", api, ask: views(["dataset", "sales"], ["dashboard", 7], ["dashboard-code", "weekly"], ["lab-model", "mdl_1"]) });
  assert.deepEqual(ctx.active_objects.map((o) => o.kind), ["lab-model"]);
});

test("create forms are reported on any route, including with no object", async () => {
  const ctx = await buildContext({ hash: "#/charts", api: makeApi(), ask: ({ provide }) => {
    provide(formSnapshot("chart-draft", { initial: { title: "", kind: "bar" }, current: { title: "Revenue", kind: "line" } }));
  } });
  assert.deepEqual(ctx, { editor: { kind: "chart-draft", dirty: true, fields: { title: "Revenue", kind: "line" } } });
});

test("an edit form carries its object id next to the active object", async () => {
  const ctx = await buildContext({ hash: "#/dashboards-code", api: makeApi(), ask: ({ provide, provideActive }) => {
    provideActive("dashboard-code", "weekly");
    provide(formSnapshot("dashboard-code", { objectId: "weekly", initial: { text: "a: 1" }, current: { text: "a: 2" } }));
  } });
  assert.equal(ctx.editor.object_id, "weekly");
  assert.deepEqual(ctx.editor.fields, { text: "a: 2" });
  assert.equal(ctx.active_objects[0].title, "Weekly");
});

test("a failing form still leaves context resolved", async () => {
  assert.deepEqual(await buildContext({ hash: "#/log", api: makeApi(), ask: () => { throw new Error("form"); } }), {});
  assert.deepEqual(await buildContext({ hash: undefined, api: {}, ask: noForms }), {});
});

test("RESOLVERS cover exactly the kinds the views report", () => {
  assert.deepEqual(Object.keys(RESOLVERS).sort(), ["dashboard", "dashboard-code", "dataset", "lab-model", "log-entry"]);
});

test("window.hoardCopilot: context() never rejects and onTurnEnd fires the refresh event", async () => {
  const target = new EventTarget();
  const hits = [];
  globalThis.window = Object.assign(target, { location: { hash: "#/datasets/new", origin: "http://x.test" } });
  globalThis.fetch = async () => { throw new Error("offline"); };
  await import("./src/hoard-copilot.js");
  const hc = globalThis.window.hoardCopilot;
  assert.equal(typeof hc.context, "function");
  target.addEventListener("hoard:copilot-context-request", (e) => e.detail.provide({ kind: "chart-draft", dirty: true, fields: { title: "N" } }));
  assert.deepEqual(await hc.context(), { editor: { kind: "chart-draft", dirty: true, fields: { title: "N" } } });
  globalThis.window.location.hash = "#/datasets/sales";     // backend unreachable: the API rejects, context still resolves
  assert.deepEqual(await hc.context(), { editor: { kind: "chart-draft", dirty: true, fields: { title: "N" } } });
  target.addEventListener("nightingale:copilot-updated", () => hits.push(1));
  hc.onTurnEnd();
  assert.equal(hits.length, 1);
});
