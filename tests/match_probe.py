"""Collect Excel's approximate MATCH answers on unsorted, mixed data: python tests/match_probe.py OUT.json"""
import json
import random
import sys
import tempfile
from pathlib import Path

import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from nightingale.sheets.reference import recalc  # noqa: E402

rng = random.Random(11)
POOL = [0, 1, 2, 3, 5, 8, -1, 2.5, "a", "b", "m", "z", True, False, None]
wb = openpyxl.Workbook()
ws = wb.active
rows = []
for r in range(1, 401):
    n = rng.randint(1, 10)
    mode = rng.random()
    vals = [rng.choice(POOL) for _ in range(n)]
    if mode < 0.3:
        nums = sorted(rng.sample(range(-5, 20), n))
        vals = nums if rng.random() < 0.5 else nums[::-1]
        for i in range(n):
            if rng.random() < 0.2:
                vals[i] = rng.choice(["a", None, True])
    for c, v in enumerate(vals, start=1):
        if v is not None:
            ws.cell(r, c, v)
    t = rng.choice([0, 1, 2, 2.5, 4, 7, -2, 30, "a", "c", "zz", True, False])
    kind = rng.choice([1, -1])
    tt = '"' + t + '"' if isinstance(t, str) else ("TRUE" if t is True else "FALSE" if t is False else repr(t))
    ws.cell(r, 12, f"=MATCH({tt},A{r}:J{r},{kind})")
    rows.append({"values": vals + [None] * (10 - n), "target": t, "type": kind})
tmp = Path(tempfile.mkdtemp())
p = tmp / "probe.xlsx"
wb.save(p)
outs, err = recalc([p], sys.argv[2] if len(sys.argv) > 2 else "excel", tmp, 600)
assert not err, err
res = openpyxl.load_workbook(outs[0], data_only=True).active
for i, row in enumerate(rows, start=1):
    v = res.cell(i, 12).value
    row["excel"] = v
Path(sys.argv[1]).write_text(json.dumps(rows), encoding="utf-8")
print("ok", len(rows))
