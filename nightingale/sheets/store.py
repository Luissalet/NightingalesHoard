"""Compiled workbooks kept in the data directory: <data>/workbooks/<id>/{source.xlsx, meta.json}."""
from __future__ import annotations

import datetime as dt
import json
import re
import shutil
import threading
import uuid
from pathlib import Path
from typing import Any

from .compiler import addr, load, parse_addr, to_json
from .engine import Compiled, export_python
from . import reference

_LOCK = threading.Lock()
_CACHE: dict[str, Compiled] = {}
SUFFIXES = (".xlsx", ".xlsm")


def _slug(text: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").lower()
    return s or "libro"


class WorkbookStore:
    def __init__(self, data_dir: Path):
        self.root = Path(data_dir) / "workbooks"
        self.exports = Path(data_dir) / "exports"

    # -- catalogue --------------------------------------------------------
    def _dir(self, wid: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{12}", wid or ""):
            raise LookupError(f"no compiled workbook {wid!r}")
        d = self.root / wid
        if not (d / "meta.json").exists():
            raise LookupError(f"no compiled workbook {wid!r}")
        return d

    def _meta(self, wid: str) -> dict:
        return json.loads((self._dir(wid) / "meta.json").read_text(encoding="utf-8"))

    def _save_meta(self, wid: str, meta: dict) -> None:
        (self.root / wid / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")

    def list(self) -> list[dict]:
        if not self.root.exists():
            return []
        out = []
        for d in sorted(self.root.iterdir()):
            m = d / "meta.json"
            if m.exists():
                meta = json.loads(m.read_text(encoding="utf-8"))
                s = meta["summary"]
                out.append({"id": meta["id"], "name": meta["name"], "created": meta["created"],
                            "formulas": s["formulas"], "executed": s["executed"], "frozen": len(s["frozen"]),
                            "parameters": len(s["parameters"]), "results": len(s["results"]),
                            "last_check": (meta.get("checks") or [None])[-1]})
        return sorted(out, key=lambda x: x["created"], reverse=True)

    def compiled(self, wid: str) -> Compiled:
        with _LOCK:
            c = _CACHE.get(wid)
        if c is None:
            meta = self._meta(wid)
            c = Compiled(load(self._dir(wid) / "source.xlsx", name=meta["name"]))
            with _LOCK:
                _CACHE[wid] = c
        return c

    @staticmethod
    def user_path(data_dir: Path, path: str) -> Path:
        """A path the user gave, checked with the family's file-safety rules (relative = inside the data dir)."""
        from ..hoard_link import paths as hl_paths
        p = Path(hl_paths.clean_user_path(path)).expanduser()
        if not p.is_absolute():
            p = (Path(data_dir) / p).resolve()
        if not p.is_file():
            raise ValueError(f"no existe el fichero {p}")
        problem = hl_paths.unsafe_file(p, data_dir=Path(data_dir), allow_data_subdir=("files", "exports", "inbox"), lang="es")
        if problem:
            raise ValueError(problem)
        return p

    # -- operations -------------------------------------------------------
    def compile(self, source: Path, name: str | None = None, original: str | None = None) -> dict:
        source = Path(source)
        if source.suffix.lower() not in SUFFIXES:
            raise ValueError("solo se compilan libros .xlsx o .xlsm (los .xls antiguos hay que guardarlos antes como .xlsx)")
        wid = uuid.uuid4().hex[:12]
        d = self.root / wid
        d.mkdir(parents=True)
        try:
            shutil.copyfile(source, d / "source.xlsx")
            model = load(d / "source.xlsx", name=name or source.stem)
            compiled = Compiled(model)
            check = compiled.check_cached()
        except Exception:
            shutil.rmtree(d, ignore_errors=True)
            raise
        meta = {"id": wid, "name": model.name, "original": original or str(source),
                "created": dt.datetime.now().isoformat(timespec="seconds"), "summary": model.summary(),
                "checks": [{"kind": "cached", "at": dt.datetime.now().isoformat(timespec="seconds"),
                            **{k: check[k] for k in ("compared", "agree", "difference_count")}}]}
        self._save_meta(wid, meta)
        with _LOCK:
            _CACHE[wid] = compiled
        return {**meta, "cached_check": check}

    def get(self, wid: str) -> dict:
        return self._meta(wid)

    def delete(self, wid: str) -> dict:
        d = self._dir(wid)
        shutil.rmtree(d)
        with _LOCK:
            _CACHE.pop(wid, None)
        return {"deleted": wid}

    def evaluate(self, wid: str, inputs: dict[str, Any] | None = None, as_of: str | None = None) -> dict:
        c = self.compiled(wid)
        moment = dt.datetime.fromisoformat(as_of) if as_of else None
        r = c.evaluate(inputs or {}, as_of=moment)
        r.pop("values", None)
        return {"workbook": wid, **r}

    def check(self, wid: str) -> dict:
        r = self.compiled(wid).check_cached()
        self._record(wid, {"kind": "cached", **{k: r[k] for k in ("compared", "agree", "difference_count")}})
        return {"workbook": wid, **r}

    def verify(self, wid: str, *, engine: str | None = None, cases: list[dict] | None = None, n: int = 12,
               seed: int = 7) -> dict:
        c = self.compiled(wid)
        r = reference.verify(c.model, self._dir(wid) / "source.xlsx", cases=cases, n=n, seed=seed, engine=engine)
        if r.get("status") in ("agree", "differences"):
            self._record(wid, {"kind": r["engine"], "cases": r["cases"], "compared": r["compared"], "agree": r["agree"],
                               "cases_with_differences": r["cases_with_differences"]})
        return {"workbook": wid, **r}

    def _record(self, wid: str, entry: dict) -> None:
        meta = self._meta(wid)
        meta.setdefault("checks", []).append({"at": dt.datetime.now().isoformat(timespec="seconds"), **entry})
        meta["checks"] = meta["checks"][-20:]
        self._save_meta(wid, meta)

    def export(self, wid: str) -> dict:
        c = self.compiled(wid)
        code = export_python(c.model)
        self.exports.mkdir(parents=True, exist_ok=True)
        path = self.exports / f"{_slug(c.model.name)}_calculo.py"
        path.write_text(code, encoding="utf-8")
        return {"workbook": wid, "path": str(path), "filename": path.name, "bytes": len(code.encode("utf-8")),
                "usage": f"python {path.name} '{{\"{addr(c.model.parameters[0]) if c.model.parameters else 'Hoja!A1'}\": 1}}'"}

    def export_code(self, wid: str) -> tuple[str, str]:
        c = self.compiled(wid)
        return f"{_slug(c.model.name)}_calculo.py", export_python(c.model)

    def trace(self, wid: str, cell: str) -> dict:
        """Formula of a cell, the formulas it goes through and the parameters it finally depends on."""
        c = self.compiled(wid)
        m = c.model
        key = c.resolve(cell)
        if key not in m.cells:
            raise LookupError(f"{cell} está vacía")
        seen, chain, params = set(), [], set()
        stack = [key]
        while stack:
            k = stack.pop()
            if k in seen:
                continue
            seen.add(k)
            cl = m.cells.get(k)
            if cl is None:
                continue
            if cl.formula:
                chain.append(m.describe(k) | ({"frozen": cl.frozen} if cl.frozen else {}))
                from .parser import walk
                if cl.ast is not None:
                    for n in walk(cl.ast):
                        if n[0] == "ref":
                            stack.append((n[1], n[2], n[3]))
                        elif n[0] == "range":
                            stack.extend(x for x in m.cells if x[0] == n[1] and n[2] <= x[1] <= n[4] and n[3] <= x[2] <= n[5])
            elif k in m.parameters:
                params.add(k)
        return {"workbook": wid, "cell": addr(key), "label": m.labels.get(key), "formulas": chain,
                "parameters": [m.describe(k) for k in sorted(params)]}

    # -- explain: sensitivity, scenarios, units ------------------------------------
    def sensitivity(self, wid: str, output: str | None = None, delta: float = 0.1,
                    steps: dict[str, float] | None = None) -> dict:
        from . import explain
        return {"workbook": wid, **explain.sensitivity(self.compiled(wid), output, delta, steps)}

    def scenarios(self, wid: str) -> dict:
        return dict(self._meta(wid).get("scenarios") or {})

    def scenario_list(self, wid: str) -> dict:
        sc = self.scenarios(wid)
        return {"workbook": wid, "scenarios": [{"name": n, **v} for n, v in sc.items()]}

    def scenario_save(self, wid: str, name: str, inputs: dict[str, Any], note: str | None = None) -> dict:
        name = (name or "").strip()
        if not name or name.lower() == "base":
            raise ValueError("give the scenario a name other than 'Base'")
        if not inputs:
            raise ValueError("inputs are required: {cell address or label: value}")
        applied = self.compiled(wid).evaluate(inputs)["inputs"]  # validates every reference and value
        meta = self._meta(wid)
        meta.setdefault("scenarios", {})[name] = {"inputs": applied, **({"note": note} if note else {})}
        self._save_meta(wid, meta)
        return {"workbook": wid, "saved": name, "inputs": applied}

    def scenario_delete(self, wid: str, name: str) -> dict:
        meta = self._meta(wid)
        if name not in (meta.get("scenarios") or {}):
            raise LookupError(f"no scenario {name!r}")
        del meta["scenarios"][name]
        self._save_meta(wid, meta)
        return {"workbook": wid, "deleted": name}

    def scenario_compare(self, wid: str, names: list[str] | None = None) -> dict:
        from . import explain
        sc = self.scenarios(wid)
        if names:
            missing = [n for n in names if n not in sc]
            if missing:
                raise LookupError(f"no scenario {missing[0]!r}")
            sc = {n: sc[n] for n in names}
        if not sc:
            raise ValueError("no scenarios saved yet; save one first")
        return {"workbook": wid, **explain.compare_scenarios(self.compiled(wid), {n: v["inputs"] for n, v in sc.items()})}

    def units_declared(self, wid: str) -> dict:
        return dict(self._meta(wid).get("units") or {})

    def units_set(self, wid: str, units: dict[str, str | None]) -> dict:
        """Merge declared units into the workbook ({cell: unit}); None or '' removes one."""
        from . import explain
        c = self.compiled(wid)
        meta = self._meta(wid)
        cur = dict(meta.get("units") or {})
        for ref, text in units.items():
            key = c.resolve(ref)
            cell = c.model.cells.get(key)
            if cell is None:
                raise LookupError(f"{ref} is empty")
            if cell.formula and not cell.frozen:
                raise ValueError(f"{addr(key)} is a formula: its unit is inferred, declare the units of the inputs")
            if text in (None, ""):
                cur.pop(addr(key), None)
            else:
                explain.parse_unit(text)
                cur[addr(key)] = text
        meta["units"] = cur
        self._save_meta(wid, meta)
        return self.units_report(wid)

    def units_report(self, wid: str) -> dict:
        from . import explain
        return {"workbook": wid, "declared": self.units_declared(wid),
                **explain.check_units(self.compiled(wid), self.units_declared(wid))}
