"""Independent check: recalculate fresh cases with LibreOffice and compare with the compiled model.

The cases change the parameters (never the formulas), are generated from a
seed and are not the values the model was compiled from, so a difference is a
real disagreement between the two engines.
"""
from __future__ import annotations

import datetime as dt
import os
import random
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import openpyxl

from . import runtime as rt
from .compiler import Model, addr, from_json, to_json
from .engine import Compiled, _close

_CANDIDATES = [
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    "/usr/bin/soffice",
    "/usr/lib/libreoffice/program/soffice",
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
]


def find_soffice() -> str | None:
    env = os.environ.get("NIGHTINGALE_SOFFICE")
    if env and Path(env).exists():
        return env
    for name in ("soffice", "soffice.exe", "libreoffice"):
        p = shutil.which(name)
        if p:
            return p
    return next((c for c in _CANDIDATES if Path(c).exists()), None)


_EXCEL_PS = r"""
param([string]$List)
$ErrorActionPreference = 'Stop'
$xl = New-Object -ComObject Excel.Application
try {
  $xl.Visible = $false; $xl.DisplayAlerts = $false; $xl.ScreenUpdating = $false
  $xl.AutomationSecurity = 3
  foreach ($f in Get-Content -LiteralPath $List -Encoding UTF8) {
    if (-not $f) { continue }
    $wb = $xl.Workbooks.Open($f, 0, $false)
    $xl.CalculateFull()
    $wb.Save()
    $wb.Close($false)
  }
} finally {
  $xl.Quit()
  [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($xl)
}
"""


def excel_available() -> bool:
    if os.name != "nt" or os.environ.get("NIGHTINGALE_NO_EXCEL"):
        return False
    try:
        import winreg
        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "Excel.Application"))
        return True
    except OSError:
        return False


def engines() -> list[str]:
    out = []
    if excel_available():
        out.append("excel")
    if find_soffice():
        out.append("libreoffice")
    return out


def recalc(files: list[Path], engine: str, workdir: Path, timeout: float) -> tuple[list[Path], str | None]:
    """Recalculate copies of ``files`` with ``engine``; returns the recalculated paths (same order)."""
    if engine == "excel":
        lst = workdir / "files.txt"
        lst.write_text("\n".join(str(f.resolve()) for f in files), encoding="utf-8")
        ps1 = workdir / "recalc.ps1"
        ps1.write_text(_EXCEL_PS, encoding="utf-8-sig")
        try:
            r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1), "-List", str(lst)],
                               capture_output=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            return [], f"Excel no terminó en {timeout:.0f} s"
        if r.returncode != 0:
            return [], "Excel falló: " + r.stderr.decode("utf-8", "replace")[-400:]
        return list(files), None
    soffice = find_soffice()
    out = workdir / "out"
    out.mkdir(exist_ok=True)
    profile = (workdir / "profile").resolve().as_uri()
    cmd = [soffice, f"-env:UserInstallation={profile}", "--headless", "--norestore", "--calc",
           "--convert-to", "xlsx", "--outdir", str(out)] + [str(p) for p in files]
    try:
        subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return [], f"LibreOffice no terminó en {timeout:.0f} s"
    return [out / f.name for f in files], None


def make_cases(model: Model, n: int, seed: int = 7) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    cases = []
    for _ in range(n):
        case = {}
        for k in model.parameters:
            c = model.cells[k]
            v = c.value
            if k in model.options and model.options[k]:
                case[addr(k)] = to_json(rng.choice(model.options[k]))
            elif isinstance(v, bool):
                case[addr(k)] = rng.random() < 0.5
            elif isinstance(v, float):
                from .compiler import is_date_format
                if is_date_format(c.number_format):
                    case[addr(k)] = v + rng.randint(-60, 60)
                elif v.is_integer():
                    case[addr(k)] = int(max(0, round(v * rng.uniform(0.3, 2.5)))) if v >= 0 else int(round(v * rng.uniform(0.3, 2.5)))
                else:
                    digits = max(0, min(6, len(repr(v).split(".")[1]) if "." in repr(v) else 0))
                    case[addr(k)] = round(v * rng.uniform(0.5, 1.5), digits)
        cases.append(case)
    return cases


def _write_case(src: Path, dst: Path, model: Model, compiled: Compiled, case: dict) -> None:
    wb = openpyxl.load_workbook(src)
    for ref, v in case.items():
        key = compiled.resolve(ref)
        cell = model.cells.get(key)
        val = from_json(v)
        if cell is not None:
            from .compiler import is_date_format
            if isinstance(val, float) and is_date_format(cell.number_format):
                val = rt.from_serial(val)
        if isinstance(val, rt.XlError):
            val = val.code
        wb[key[0]].cell(key[1], key[2]).value = val
    wb.save(dst)


def verify(model: Model, source: Path, *, cases: list[dict] | None = None, n: int = 12, seed: int = 7,
           timeout: float = 300, engine: str | None = None) -> dict:
    available = engines()
    if engine is None:
        engine = available[0] if available else None
    if engine is None or engine not in available:
        return {"status": "unavailable", "available": available,
                "reason": "No hay Excel ni LibreOffice en este equipo para recalcular de forma independiente."}
    compiled = Compiled(model)
    cases = cases if cases is not None else make_cases(model, n, seed)
    frozen_up = compiled.frozen_upstream()
    from .compiler import _plain
    with tempfile.TemporaryDirectory(prefix="nightingale-ref-") as tmp:
        tmp = Path(tmp)
        inputs = []
        for i, case in enumerate(cases):
            p = tmp / f"case{i:03d}.xlsx"
            _write_case(source, p, model, compiled, case)
            inputs.append(p)
        outputs, err = recalc(inputs, engine, tmp, timeout)
        if err:
            return {"status": "inconclusive", "engine": engine, "reason": err}
        report = []
        totals = {"cases": len(cases), "compared": 0, "agree": 0}
        for i, case in enumerate(cases):
            p = outputs[i]
            if not p.exists():
                report.append({"case": i, "inputs": case, "error": f"{engine} no produjo resultado"})
                continue
            wb = openpyxl.load_workbook(p, data_only=True)
            mine = compiled.evaluate(case)["values"]
            diffs = []
            for k in model.results:
                c = model.cells[k]
                if c.frozen or c.volatile or k in frozen_up:
                    continue
                ref = _plain(wb[k[0]].cell(k[1], k[2]).value)
                got = mine.get(k)
                totals["compared"] += 1
                if _close(got, ref):
                    totals["agree"] += 1
                else:
                    diffs.append({"cell": addr(k), "label": model.labels.get(k), "formula": c.formula,
                                  engine: to_json(ref), "nightingale": to_json(got)})
            report.append({"case": i, "inputs": case, "differences": diffs})
    bad = [r for r in report if r.get("differences") or r.get("error")]
    note = "Resultados congelados, volátiles o que dependen de celdas congeladas no se comparan."
    if engine == "libreoffice":
        note += " LibreOffice no es Excel: trata VERDADERO/FALSO como números y algunos códigos de error difieren."
    return {"status": "agree" if not bad else "differences", "engine": engine, **totals,
            "cases_with_differences": len(bad), "report": report, "note": note}
