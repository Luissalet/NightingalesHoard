"""Every message the analysis modules show to a person is available in Spanish (Spain) and English.

The default is English (agents, MCP and the existing tests); `lang="es"` on the REST routes (or `use_lang` in
code, or the `lang` argument of the agent tools) switches the wording, never the structure or the numbers.
"""
import re
import string

import duckdb
import numpy as np
import pytest

from nightingale import effects as ef
from nightingale import experiments as ex
from nightingale import migrate as mg
from nightingale import process_mining as pm
from nightingale import reconcile as rec
from nightingale import scenarios as sc
from nightingale import texts
from nightingale import twin as tw
from nightingale.agent_tools import call_tool_sync
from nightingale.sheets import explain
from nightingale.texts import t, use_lang

# ----------------------------------------------------------------------------------------------- catalogue


def _fields(text):
    return {f[1] for f in string.Formatter().parse(text) if f[1]}


def test_catalogue_is_complete_and_consistent():
    assert len(texts.CATALOGUE) > 400
    for key, entry in texts.CATALOGUE.items():
        assert set(entry) == {"en", "es"}, key
        assert entry["en"].strip() and entry["es"].strip(), key
        assert _fields(entry["en"]) == _fields(entry["es"]), f"{key}: placeholders differ between languages"
    assert texts.missing("es") == [] and texts.missing("en") == []


def test_spanish_wording_avoids_the_anglicisms_the_interface_avoids():
    banned = re.compile(r"\b(dataset|datasets|bottleneck|cluster|log de|twin|workbook)\b", re.I)
    for key, entry in texts.CATALOGUE.items():
        prose = re.sub(r"«[^»]*»|\{[^}]*\}", "", entry["es"])    # parameter names and placeholders stay as they are
        assert not banned.search(prose), f"{key}: {entry['es']}"


def test_t_default_language_context_and_number_format():
    assert t("pm.warn_few_cases", n=3) == "Only 3 cases: variant shares and waiting statistics are very uncertain."
    assert t("pm.warn_few_cases", "es", n=3).startswith("Solo hay 3 casos")
    assert t("pm.warn_few_cases", "es-ES", n=3).startswith("Solo hay 3 casos")
    assert t("pm.warn_few_cases", "fr", n=3).startswith("Only 3 cases")      # unknown language -> English
    assert texts.get_lang() == "en"
    with use_lang("es"):
        assert texts.get_lang() == "es"
        assert t("pm.too_many_rows", total=1234567, limit=2000000).startswith("1.234.567 filas")
        assert "2,50" in t("tw.warn_leaves", out="y", lo=2.5, hi=3.0) or "2,5" in t("tw.warn_leaves", out="y", lo=2.5, hi=3.0)
        assert t("pm.warn_few_cases", "en", n=3).startswith("Only")          # an explicit language wins
    assert texts.get_lang() == "en"
    assert t("pm.too_many_rows", total=1234567, limit=2000000).startswith("1,234,567 rows")


# ----------------------------------------------------------------------------------------------- process mining
LOG = """case_id,activity,end
C1,Register,2026-01-05 08:00:00
C1,Assess,2026-01-05 10:00:00
C1,Decide,2026-01-05 11:00:00
C2,Register,2026-01-05 09:00:00
C2,Assess,2026-01-05 12:00:00
C2,Decide,2026-01-05 13:00:00
C3,Register,2026-01-05 10:00:00
C3,Assess,2026-01-05 13:30:00
C3,Decide,2026-01-05 15:00:00
C4,Register,2026-01-06 08:00:00
C4,Assess,2026-01-06 10:00:00
C4,Decide,2026-01-06 11:30:00
C5,Register,2026-01-06 09:00:00
C5,Assess,2026-01-06 11:00:00
C5,Decide,2026-01-06 12:00:00
C6,Register,2026-01-06 10:00:00
C6,Assess,2026-01-06 14:00:00
C6,Decide,2026-01-06 16:00:00
"""
PM_MAP = {"case": "case_id", "activity": "activity", "timestamp": "end"}


@pytest.fixture
def pmlog(services):
    services.ingest_text(LOG, "claims", "csv")
    return services


def test_process_discover_conform_and_errors_in_both_languages(pmlog):
    en = pm.discover_process(pmlog, "claims", PM_MAP)
    with use_lang("es"):
        es = pm.discover_process(pmlog, "claims", PM_MAP)
        conform = pm.conform_process(pmlog, "claims", PM_MAP, {"steps": ["Nothing", "Else"]})
        with pytest.raises(pm.ProcessError, match="obligatorio"):
            pm.discover_process(pmlog, "claims", {"case": "case_id"})
        with pytest.raises(pm.ProcessError, match="el umbral debe estar entre 0 y 1"):
            pm.discover_process(pmlog, "claims", PM_MAP, threshold=3)
    assert "Only 6 cases: variant shares and waiting statistics are very uncertain." in en["warnings"]
    assert "Solo hay 6 casos: las proporciones de variantes y las estadísticas de espera son muy inciertas." in es["warnings"]
    assert any(w.startswith("Sin columna de inicio") for w in es["warnings"])
    assert en["bottleneck_verdict"].startswith(("'", "Waiting")) and es["bottleneck_verdict"] != en["bottleneck_verdict"]
    assert es["caveat"].startswith("Resultados descriptivos") and en["caveat"].startswith("Descriptive")
    assert es["graph"]["method"].startswith("Dependencia") and en["graph"]["method"].startswith("Heuristic")
    assert any("Ninguna actividad de la referencia" in w for w in conform["warnings"])
    # the structure and the numbers do not depend on the language
    strip = lambda r: {k: v for k, v in r.items() if k not in {"warnings", "caveat", "bottleneck_verdict", "graph", "log_id"}}  # noqa: E731
    assert strip(en) == strip(es)
    assert en["graph"]["edges"] == es["graph"]["edges"] and en["throughput"] == es["throughput"]
    with pytest.raises(pm.ProcessError, match="mapping: 'activity' is required"):
        pm.discover_process(pmlog, "claims", {"case": "case_id"})
    assert pm.describe_change({"type": "add_resource", "activity": "A", "count": 2}) == "add 2 resource(s) at A"
    with use_lang("es"):
        assert pm.describe_change({"type": "add_resource", "activity": "A", "count": 2}) == "añadir 2 recurso(s) en A"
        assert pm.describe_change({"type": "scale_duration", "activity": "A", "factor": 0.7}) == "escalar las duraciones de A por 0,7"


def test_process_rest_follows_the_lang_query_param(client):
    client.services.ingest_text(LOG, "claims", "csv")
    body = {"dataset": "claims", "mapping": PM_MAP}
    es = client.post("/api/process/discover?lang=es", json=body).json()
    en = client.post("/api/process/discover", json=body).json()
    assert es["warnings"][0].startswith("Solo hay 6 casos") and en["warnings"][0].startswith("Only 6 cases")
    bad = client.post("/api/process/discover?lang=es", json={"dataset": "claims", "mapping": {"case": "case_id"}})
    assert bad.status_code == 400 and "obligatorio" in bad.json()["error"]
    bad = client.post("/api/process/discover", json={"dataset": "claims", "mapping": {"case": "case_id"}})
    assert bad.status_code == 400 and "is required" in bad.json()["error"]
    # a request must not leak its language into the next one
    assert client.post("/api/process/discover", json=body).json()["warnings"][0].startswith("Only 6 cases")
    suggest = client.post("/api/process/suggest?lang=es", json={"dataset": "claims"}).json()
    assert suggest["mapping"]["case"] == "case_id"


def test_agent_tools_accept_an_optional_lang(client):
    client.services.ingest_text(LOG, "claims", "csv")
    args = {"action": "discover", "dataset": "claims", "mapping": PM_MAP}
    assert call_tool_sync(client.services, "data_process", args)["warnings"][0].startswith("Only 6 cases")
    assert call_tool_sync(client.services, "data_process", {**args, "lang": "es"})["warnings"][0].startswith("Solo hay 6")
    assert texts.get_lang() == "en"


# ----------------------------------------------------------------------------------------------- scenarios
MODEL = {"horizon_hours": 50, "arrivals": {"type": "poisson", "rate": 1.0},
         "stations": [{"name": "A", "servers": 1, "service": {"dist": "fixed", "value": 0.5}}]}


def test_scenarios_warnings_and_errors():
    en = sc.run_model(MODEL, replications=3, seed=1)
    with use_lang("es"):
        es = sc.run_model(MODEL, replications=3, seed=1)
        with pytest.raises(sc.ScenarioError, match="horizon_hours debe ser mayor que 0"):
            sc.run_model({**MODEL, "horizon_hours": -1})
        with pytest.raises(sc.ScenarioError, match="estación 'A': servers debe ser un número entero de al menos 1"):
            sc.run_model({**MODEL, "stations": [{"name": "A", "servers": 0, "service": 1}]})
        with pytest.raises(sc.ScenarioError, match="distribución desconocida"):
            sc.run_model({**MODEL, "stations": [{"name": "A", "service": {"dist": "x"}}]})
        cmp_ = sc.compare_models(MODEL, [{"name": "v", "overrides": {}}], 3, 1)
        fit = sc.fit_durations([1.0, 2.0, 3.0], "col")
    assert "Fewer than 5 replications: the intervals are very wide or missing." in en["warnings"]
    assert "Menos de 5 réplicas: los intervalos son muy anchos o no existen." in es["warnings"]
    assert en["caveat"].startswith("Scenarios under") and es["caveat"].startswith("Escenarios bajo")
    assert cmp_["method"].startswith("Números aleatorios comunes")
    assert fit["warnings"][0].startswith("col: solo hay 3 observaciones") and "muestra pequeña" in fit["suggestion"]["reason"]
    assert en["metrics"] == es["metrics"]
    with pytest.raises(sc.ScenarioError, match="horizon_hours must be greater than 0"):
        sc.run_model({**MODEL, "horizon_hours": -1})


def test_scenarios_rest_errors_follow_lang(client):
    es = client.post("/api/scenarios/run?lang=es", json={"model": {**MODEL, "horizon_hours": -1}})
    en = client.post("/api/scenarios/run", json={"model": {**MODEL, "horizon_hours": -1}})
    assert es.status_code == en.status_code == 400
    assert "debe ser mayor que 0" in es.json()["error"] and "must be greater than 0" in en.json()["error"]
    missing = client.get("/api/scenarios/models/nothere?lang=es")
    assert missing.status_code == 404 and "modelo de escenario desconocido" in missing.json()["error"]


# ----------------------------------------------------------------------------------------------- digital twin
def test_twin_errors_and_extrapolation_refusal():
    t_ = np.arange(100.0)
    with use_lang("es"):
        with pytest.raises(tw.TwinError, match="no varía en el bloque de ajuste"):
            tw.fit_series(t_, np.sin(t_), np.ones(100), ["y"], ["u"], "arx")
        with pytest.raises(tw.TwinError, match="filas utilizables"):
            tw.fit_series(t_[:20], np.sin(t_[:20]), np.cos(t_[:20]), ["y"], ["u"], "arx")
        with pytest.raises(tw.TwinError, match="model debe ser"):
            tw.fit_series(t_, np.sin(t_), np.cos(t_), ["y"], ["u"], "nope")
    with pytest.raises(tw.TwinError, match="usable rows"):
        tw.fit_series(t_[:20], np.sin(t_[:20]), np.cos(t_[:20]), ["y"], ["u"], "arx")


def _arx(n=300):
    rng = np.random.default_rng(0)
    u = np.repeat(rng.uniform(-1, 1, n // 8 + 1), 8)[:n]
    y = np.zeros(n)
    for k in range(2, n):
        y[k] = 0.7 * y[k - 1] + 0.5 * u[k - 1] + 0.2 * u[k - 2] + rng.normal(0, 0.02)
    return np.arange(n) * 60.0, y, u


def test_twin_fit_and_simulate_messages():
    t_, y, u = _arx()
    en = tw.fit_series(t_, y, u, ["y"], ["u"], "arx", n_boot=40, seed=1)
    with use_lang("es"):
        es = tw.fit_series(t_, y, u, ["y"], ["u"], "arx", n_boot=40, seed=1)
    en, es = en["report"], es["report"]
    assert es["caveat"].startswith("Un gemelo es un modelo lineal")
    assert es["structure"]["form"].startswith("ARX multisalida")
    assert es["nrmse_definition"].startswith("RMSE dividido por el rango")
    assert en["caveat"].startswith("A twin is a linear model") and en["structure"]["form"].startswith("multi-output ARX")
    assert en["validation"] == es["validation"] and en["parameters"] == es["parameters"]


# ----------------------------------------------------------------------------------------------- experiments
FACTORS = [{"name": "A", "low": 0, "high": 10}, {"name": "B", "low": 0, "high": 10}, {"name": "C", "low": 0, "high": 10}]


def test_experiment_design_and_choice_messages():
    en = ex.design_experiment(FACTORS, "ccd", variant="circumscribed")
    with use_lang("es"):
        es = ex.design_experiment(FACTORS, "ccd", variant="circumscribed")
        frac = ex.design_experiment([{"name": n, "low": 0, "high": 1} for n in "ABCDE"], "fractional", seed=1)
        with pytest.raises(ex.ExperimentError, match="todas las ejecuciones previstas están prohibidas"):
            ex.design_experiment(FACTORS, "full", forbidden=["A >= 0"])
        with pytest.raises(ex.ExperimentError, match="indica al menos un factor"):
            ex.design_experiment([], "full")
        choice = ex.choose_test(["h1", "h2"], [{"name": "t1", "cost": 1, "outcomes": {"h1": "x", "h2": "y"}}])
        with pytest.raises(ex.ExperimentError, match="indica al menos dos hipótesis"):
            ex.choose_test(["h1"], [])
    assert any("outside the stated bounds" in w for w in en["warnings"])
    assert any("fuera de los límites indicados" in w for w in es["warnings"])
    assert es["note"].startswith("Ejecuta en el orden") and en["note"].startswith("Run in the")
    assert frac["info"]["interpretation"].startswith("Resolución")
    assert "ganancia de información por unidad de coste" in choice["recommendation"]["why"]
    assert choice["assumptions"].startswith("Las probabilidades")
    assert en["runs"] == es["runs"] and en["properties"] == es["properties"]


def test_experiment_analysis_warnings():
    rng = np.random.default_rng(1)
    fs = [{"name": "A", "low": 0, "high": 10}, {"name": "B", "low": 0, "high": 10}]
    d = ex.design_experiment(fs, "full", center_points=0, seed=2)
    X = np.array([[r["A"], r["B"]] for r in d["runs"]])
    y = 3 + 2 * X[:, 0] - X[:, 1] + rng.normal(0, 0.1, len(X))
    en = ex.analyze_experiment(X, y, fs, "y")
    with use_lang("es"):
        es = ex.analyze_experiment(X, y, fs, "y")
    assert any("No centre points" in n for n in en["notes"]) and any("No hay puntos centrales" in n for n in es["notes"])
    assert es["coding"].startswith("codificado de -1 a +1") and en["coding"].startswith("coded -1..+1")
    assert en["terms"] == es["terms"] and en["fit"] == es["fit"]


# ----------------------------------------------------------------------------------------------- effect of a change
def _panel(seed=3, units=40, periods=12, t0=8):
    rng = np.random.default_rng(seed)
    rows = []
    for u in range(units):
        treated = u < units // 2
        a = rng.normal(0, 2)
        for k in range(periods):
            rows.append((u, treated, k, 10 + a + 0.5 * k + (3 if treated else 0) + (5 if (treated and k >= t0) else 0)
                         + rng.normal(0, 1)))
    R = np.array(rows, float)
    return R[:, 3], R[:, 1] > 0, R[:, 2], np.array([str(int(v)) for v in R[:, 0]])


def test_effect_assumptions_verdict_and_errors():
    y, tr, t_, unit = _panel()
    few = t_ >= 6
    en = ef.did_effect(y[few], tr[few], t_[few], 8.0, unit=unit[few])
    with use_lang("es"):
        es = ef.did_effect(y[few], tr[few], t_[few], 8.0, unit=unit[few])
        full = ef.did_effect(y, tr, t_, 8.0, unit=unit)
        with pytest.raises(ef.EffectError, match="está vacía"):
            ef.did_effect(y[t_ < 8], tr[t_ < 8], t_[t_ < 8], 8.0)
    assert en["assumptions"][0]["name"] == "parallel pre-trends" and en["assumptions"][0]["status"] == "not_testable"
    assert es["assumptions"][0]["name"] == "tendencias previas paralelas" and es["assumptions"][0]["status"] == "not_testable"
    assert es["assumptions"][0]["statement"].startswith("Sin el cambio, los grupos")
    assert any("No se puede contrastar el paralelismo" in w for w in es["warnings"])
    assert es["causal_claim"]["text"].startswith("Esto es una estimación") and en["causal_claim"]["text"].startswith("This is")
    assert all(a["name"] and a["detail"] for a in full["assumptions"])
    assert en["effect"] == es["effect"] and en["causal_claim"]["established"] is False


def test_effect_series_errors_in_spanish():
    t_ = np.arange(4.0)
    with use_lang("es"):
        with pytest.raises(ef.EffectError, match="una serie temporal interrumpida necesita al menos 5 puntos"):
            ef.its_effect(np.arange(4.0), t_, 2.0)
        with pytest.raises(ef.EffectError, match="el tratamiento no varía|son pocas"):
            ef.adjust_effect(np.arange(10.0), np.ones(10), np.empty((10, 0)), [])
    with pytest.raises(ef.EffectError, match="an interrupted time series needs at least 5 points"):
        ef.its_effect(np.arange(4.0), t_, 2.0)


# ----------------------------------------------------------------------------------------------- migration
CUSTOMERS = "id,name,balance\n1,Acme,100.5\n2,Beta,abc\n1,Acme again,5\n,Nameless,7\n"


def _mig_spec():
    return {"name": "m1", "tables": [{"name": "customers", "source": "customers", "primary_key": ["id"],
                                      "columns": [{"name": "id", "type": "INTEGER"},
                                                  {"name": "name", "type": "TEXT", "required": True},
                                                  {"name": "balance", "type": "REAL"}]}]}


def test_migration_rejection_details_explanation_and_errors(services):
    services.ingest_text(CUSTOMERS, "customers", "csv")
    en = mg.rehearse_spec(services, _mig_spec(), sample=20)
    with use_lang("es"):
        es = mg.rehearse_spec(services, _mig_spec(), sample=20)
        sug = mg.suggest_spec(services, "customers")
        with pytest.raises(mg.MigrateError, match="la especificación necesita al menos una tabla"):
            mg.rehearse_spec(services, {"name": "bad", "tables": []})
    cast_en = next(s for t_ in en["tables"] for s in t_["samples"].get("cast_failure", []))
    cast_es = next(s for t_ in es["tables"] for s in t_["samples"].get("cast_failure", []))
    assert "is not a valid REAL" in cast_en["detail"] and "no es un valor REAL válido" in cast_es["detail"]
    dup_es = next(s for t_ in es["tables"] for s in t_["samples"].get("duplicate_primary_key", []))
    assert "ya la usa una fila anterior" in dup_es["detail"]
    assert en["explanation"][0].startswith("The source datasets") and es["explanation"][0].startswith("Los conjuntos de datos")
    assert len(en["explanation"]) == len(es["explanation"])
    assert es["summary"] == en["summary"] and en["load_order"] == es["load_order"]
    assert sug["note"].startswith("Esto es una sugerencia")
    assert "tipo nativo" in str(sug["evidence"]) and "ausentes" in str(sug["evidence"])


def test_migration_rest_errors_follow_lang(client):
    bad = {"spec": {"name": "bad", "tables": []}}
    es = client.post("/api/migrate/rehearse?lang=es", json=bad)
    en = client.post("/api/migrate/rehearse", json=bad)
    assert es.status_code == en.status_code == 400
    assert "al menos una tabla" in es.json()["error"] and "at least one table" in en.json()["error"]
    nf = client.post("/api/migrate/rehearse?lang=es", json={"name": "nothere"})
    assert nf.status_code in (400, 404) and "nothere" in nf.json()["error"] and "no hay ninguna" in nf.json()["error"]


# ----------------------------------------------------------------------------------------------- reconciliation
def test_reconcile_explanation_notes_and_errors():
    con = duckdb.connect()
    con.execute("CREATE TABLE a AS SELECT * FROM (VALUES ('k1', 1.0), ('k2', 0.0), ('k2', 5.0)) t(id, v)")
    con.execute("CREATE TABLE b AS SELECT * FROM (VALUES ('k1', 1.0), ('k2', NULL)) t(id, v)")
    en = rec.reconcile(con, "a", "b", [{"left": "id", "right": "id"}], options={"trim": True}, sample=5)
    with use_lang("es"):
        es = rec.reconcile(con, "a", "b", [{"left": "id", "right": "id"}], options={"trim": True, "tolerance": 0.5}, sample=5)
        sug = rec.suggest(con, "a", "b")
        with pytest.raises(rec.ReconcileError, match="elige al menos un par de claves"):
            rec.reconcile(con, "a", "b", [])
        with pytest.raises(rec.ReconcileError, match="la tabla izquierda no tiene la columna 'zz'"):
            rec.reconcile(con, "a", "b", [{"left": "zz", "right": "id"}])
    assert en["explanation"][0] == "Key: left ['id'] = right ['id'], normalised with trim. A missing key component never matches."
    assert es["explanation"][0] == "Clave: izquierda ['id'] = derecha ['id'], normalizada con trim. Un componente de clave ausente nunca coincide."
    assert es["explanation"][-1] == "Los números a ±0,5 cuentan como equal_within_tolerance, aparte de la igualdad exacta."
    assert sug["note"].startswith("Solo son sugerencias")
    with pytest.raises(rec.ReconcileError, match="choose at least one key pair"):
        rec.reconcile(con, "a", "b", [])


def test_reconcile_rest_follows_lang(client):
    client.services.ingest_text("code,x\nA,1\nB,2\n", "l", "csv")
    client.services.ingest_text("code,x\nA,1\nC,3\n", "r", "csv")
    body = {"left": "l", "right": "r", "keys": [{"left": "nope", "right": "code"}]}
    es = client.post("/api/reconcile/run?lang=es", json=body)
    assert es.status_code == 400 and "la tabla izquierda no tiene la columna 'nope'" in es.json()["error"]
    en = client.post("/api/reconcile/run", json=body)
    assert "left table has no column 'nope'" in en.json()["error"]
    ok = client.post("/api/reconcile/run?lang=es", json={**body, "keys": [{"left": "code", "right": "code"}]}).json()
    assert ok["explanation"][0].startswith("Clave: izquierda")


# ----------------------------------------------------------------------------------------------- workbook explain
@pytest.fixture()
def comp(tmp_path):
    from tests.test_sheets_explain import build  # noqa: PLC0415
    from nightingale.sheets.compiler import load
    from nightingale.sheets.engine import Compiled
    return Compiled(load(build(tmp_path / "w.xlsx")))


def test_workbook_explain_messages(comp):
    en = explain.sensitivity(comp, "Price", 0.1)
    with use_lang("es"):
        es = explain.sensitivity(comp, "Price", 0.1)
        units = explain.check_units(comp, {"Rate": "EUR/h", "Hours": "h", "Length": "m"})
        with pytest.raises(ValueError, match="delta debe ser un número positivo"):
            explain.sensitivity(comp, "Price", -1)
        with pytest.raises(LookupError, match="está vacía"):
            explain.sensitivity(comp, "Inputs!Z99")
    reasons_en = {s["reason"] for s in en["skipped"]}
    reasons_es = {s["reason"] for s in es["skipped"]}
    assert reasons_en and reasons_en != reasons_es
    assert any(r.startswith(("el valor base es 0", "se elige de una lista", "no numérico")) for r in reasons_es)
    assert any(r.startswith(("base value is 0", "chosen from a list", "not numeric")) for r in reasons_en)
    assert units["note"].startswith("Las celdas cuyas entradas") and any(
        e["message"].startswith("no se pueden combinar") for e in units["errors"])
    assert [r["output"] for r in en["targets"]] == [r["output"] for r in es["targets"]]
    with pytest.raises(ValueError, match="delta must be a positive number"):
        explain.sensitivity(comp, "Price", -1)
