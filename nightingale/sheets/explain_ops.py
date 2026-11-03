"""Explain operations as logged analysis steps; shared by the agent tool and the HTTP routes."""
from __future__ import annotations

from typing import Any


def _logged(services, op: str, source: str, wid: str, params: dict, fn, summary):
    def do():
        r = fn()
        r["_log_summary"] = summary(r)
        return r

    return services._log(op, source, None, {"workbook": wid, **params}, do)


def sensitivity(services, st, wid: str, output=None, delta: float = 0.1, steps=None, source: str = "ui") -> dict:
    return _logged(services, "workbook_sensitivity", source, wid, {"output": output, "delta": delta, "steps": steps},
                   lambda: st.sensitivity(wid, output, delta, steps),
                   lambda r: f"sensitivity of {len(r['targets'])} output(s), {len(r['skipped'])} parameter(s) skipped")


def scenario_save(services, st, wid: str, name: str, inputs: dict, note=None, source: str = "ui") -> dict:
    return _logged(services, "workbook_scenario_save", source, wid, {"scenario": name, "inputs": inputs},
                   lambda: st.scenario_save(wid, name, inputs, note), lambda r: f"saved scenario {r['saved']!r}")


def scenario_delete(services, st, wid: str, name: str, source: str = "ui") -> dict:
    return _logged(services, "workbook_scenario_delete", source, wid, {"scenario": name},
                   lambda: st.scenario_delete(wid, name), lambda r: f"deleted scenario {r['deleted']!r}")


def scenario_compare(services, st, wid: str, names=None, source: str = "ui") -> dict:
    return _logged(services, "workbook_scenario_compare", source, wid, {"scenarios": names},
                   lambda: st.scenario_compare(wid, names),
                   lambda r: f"compared base with {len(r['columns']) - 1} scenario(s) on {len(r['rows'])} result(s)")


def units(services, st, wid: str, mapping=None, source: str = "ui") -> dict:
    if mapping:
        return _logged(services, "workbook_units", source, wid, {"units": mapping}, lambda: st.units_set(wid, mapping),
                       lambda r: f"declared units; {len(r['errors'])} unit error(s)")
    return _logged(services, "workbook_units_check", source, wid, {}, lambda: st.units_report(wid),
                   lambda r: f"unit check: {len(r['errors'])} unit error(s)")


def run(services, st, wid: str, a: Any, source: str = "agent") -> dict:
    if a.action == "sensitivity":
        return sensitivity(services, st, wid, a.output, a.delta, a.steps, source)
    if a.action == "units":
        return units(services, st, wid, a.units, source)
    if a.op == "list":
        return st.scenario_list(wid)
    if a.op == "save":
        if not a.scenario:
            raise ValueError("scenario (the name) is required to save")
        return scenario_save(services, st, wid, a.scenario, a.inputs or {}, a.note, source)
    if a.op == "delete":
        if not a.scenario:
            raise ValueError("scenario (the name) is required to delete")
        return scenario_delete(services, st, wid, a.scenario, source)
    return scenario_compare(services, st, wid, a.scenario_names, source)
