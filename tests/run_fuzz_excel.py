"""Manual fuzz against a real engine: python tests/run_fuzz_excel.py START END [engine]"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from sheets_fuzz import build  # noqa: E402
from nightingale.sheets.compiler import load  # noqa: E402
from nightingale.sheets.engine import Compiled  # noqa: E402
from nightingale.sheets.reference import engines, recalc  # noqa: E402

a, b = int(sys.argv[1]), int(sys.argv[2])
engine = sys.argv[3] if len(sys.argv) > 3 else engines()[0]
tmp = Path(tempfile.mkdtemp(prefix="fuzz-"))
files = []
for s in range(a, b):
    p = tmp / f"f{s}.xlsx"
    build(p, s)
    files.append(p)
outs, err = recalc(files, engine, tmp, 900)
if err:
    print("ERROR", err)
    sys.exit(1)
tot = agree = 0
for f in outs:
    r = Compiled(load(f)).check_cached()
    tot += r["compared"]
    agree += r["agree"]
    for d in r["differences"]:
        print(f.name, json.dumps(d, ensure_ascii=False))
print("engine", engine, "compared", tot, "agree", agree)
