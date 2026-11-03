"""Invented print-shop orders for the event-log tests: Recepción, Revisión (skipped by repeat orders), Impresión
(one printer, the slow step) and Entrega, written as lifecycle start/complete rows with the operator or machine."""
import heapq
import random
from datetime import datetime, timedelta

T0 = datetime(2026, 1, 5, 8, 0, 0)
STATIONS = {"Recepción": (2, 0.10), "Revisión": (2, 0.50), "Impresión": (1, 0.55), "Entrega": (3, 0.30)}
SKIP_REVIEW = 0.2


def generate(n=900, seed=11, rate=1.0, drift_after=None, drift_rate=None):
    rng = random.Random(seed)
    free = {a: [0.0] * c for a, (c, _m) in STATIONS.items()}
    heap, seq, t = [], 0, 0.0
    for i in range(n):
        r = drift_rate if drift_after is not None and i >= drift_after else rate
        t += rng.expovariate(r)
        seq += 1
        heapq.heappush(heap, (t, seq, i, "Recepción"))
    rows = []
    while heap:
        arr, _s, case, act = heapq.heappop(heap)
        servers = free[act]
        k = min(range(len(servers)), key=lambda j: servers[j])
        start = max(arr, servers[k])
        end = start + rng.expovariate(1 / STATIONS[act][1])
        servers[k] = end
        rows.append((f"P{case:04d}", act, start, end, f"{act[:3]}-{k + 1}"))
        nxt = {"Recepción": "Impresión" if rng.random() < SKIP_REVIEW else "Revisión", "Revisión": "Impresión",
               "Impresión": "Entrega"}.get(act)
        if nxt:
            seq += 1
            heapq.heappush(heap, (end, seq, case, nxt))
    return rows


def lifecycle_csv(rows, seed=3):
    fmt = lambda h: (T0 + timedelta(hours=h)).strftime("%Y-%m-%d %H:%M:%S.%f")  # noqa: E731
    out = []
    for c, a, s, e, r in rows:
        out.append(f"{c},{a},{fmt(s)},start,{r}")
        out.append(f"{c},{a},{fmt(e)},complete,{r}")
    random.Random(seed).shuffle(out)
    return "pedido,actividad,momento,ciclo,operario\n" + "\n".join(out)


MAPPING = {"case": "pedido", "activity": "actividad", "timestamp": "momento", "lifecycle": "ciclo",
           "resource": "operario"}
OPTIONS = {"end_activities": ["Entrega"], "expected": ["Recepción", "Revisión", "Impresión", "Entrega"]}
