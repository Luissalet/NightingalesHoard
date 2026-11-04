"""Causal hypothesis, identification and refutation for the effect of a change (Hangar F08).

The analyst writes the causal hypothesis as a small graph (``z -> treatment, z -> outcome, treatment -> m,
m -> outcome``). Nodes that are not columns of the dataset are *unobserved*. From the graph:

* ``identify`` applies the back-door criterion (d-separation by moralising the ancestral graph): it finds a
  minimal adjustment set among the observed variables that are not descendants of the treatment, names the
  variables that must **not** be adjusted for (mediators, other descendants of the treatment, colliders that a
  listed set would open), checks a set the analyst listed, and when no observed set works says the effect is
  not identified and which measurement or design change is missing (measure the unobserved common cause,
  randomise the treatment, or use a candidate instrument with a design that supports it).
* ``refute`` re-estimates under changes that should not matter: a permuted (placebo) treatment should give no
  effect, an added random common cause should not move the estimate, and random 80 % subsets should agree.

A graph is a hypothesis written by a person; the checks here say what follows from it, not whether it is true.
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

import numpy as np

from .linfit import FitError, coef_table, ols
from .texts import register, t as tx

register({
    "cg.bad_edge": {"en": "could not read the edge {edge!r}: write it as 'a -> b'",
                    "es": "no se pudo leer la arista {edge!r}: escríbela como «a -> b»"},
    "cg.cycle": {"en": "the causal graph has a cycle through {node}; a hypothesis for one effect must be acyclic",
                 "es": "el grafo causal tiene un ciclo que pasa por {node}; la hipótesis para un efecto debe ser acíclica"},
    "cg.identified": {"en": "Identified by the back-door criterion: adjust for {set}.",
                      "es": "Identificado por el criterio de la puerta trasera: ajustar por {set}."},
    "cg.identified_empty": {"en": "Identified without adjustment: no back-door path connects the treatment and the outcome in the graph.",
                            "es": "Identificado sin ajuste: en el grafo ningún camino por la puerta trasera une tratamiento y resultado."},
    "cg.not_identified": {"en": "Not identified from these data: the back-door path(s) through {nodes} cannot be blocked with measured columns.",
                          "es": "No identificado con estos datos: el camino o caminos por la puerta trasera a través de {nodes} no se pueden bloquear con columnas medidas."},
    "cg.missing_measure": {"en": "Measure {node} (a common cause of the treatment and the outcome that is not in the data).",
                           "es": "Medir {node} (causa común del tratamiento y del resultado que no está en los datos)."},
    "cg.missing_randomise": {"en": "Randomise who gets the treatment, even in a small pilot: randomisation cuts every back-door path.",
                             "es": "Asignar el tratamiento al azar, aunque sea en un piloto pequeño: el azar corta todos los caminos por la puerta trasera."},
    "cg.missing_instrument": {"en": "{node} affects the outcome only through the treatment in this graph: it could serve as an instrument, with a design that supports it (not estimated here).",
                              "es": "{node} solo afecta al resultado a través del tratamiento en este grafo: podría servir como instrumento, con un diseño que lo respalde (no se estima aquí)."},
    "cg.forbidden": {"en": "{node} is affected by the treatment ({why}); adjusting for it would bias the effect.",
                     "es": "{node} depende del tratamiento ({why}); ajustar por ella sesgaría el efecto."},
    "cg.why_mediator": {"en": "a mediator on the way to the outcome", "es": "un mediador en el camino hacia el resultado"},
    "cg.why_descendant": {"en": "a consequence of the treatment", "es": "una consecuencia del tratamiento"},
    "cg.listed_ok": {"en": "The listed confounders block every back-door path in the graph.",
                     "es": "Los factores de confusión indicados bloquean todos los caminos por la puerta trasera del grafo."},
    "cg.listed_bad": {"en": "The listed confounders do not satisfy the back-door criterion in the graph{extra}.",
                      "es": "Los factores de confusión indicados no cumplen el criterio de la puerta trasera en el grafo{extra}."},
    "cg.listed_collider": {"en": "; adjusting for {nodes} opens a path through a common effect",
                           "es": "; ajustar por {nodes} abre un camino a través de un efecto común"},
    "cg.not_in_graph": {"en": "{nodes} not in the graph; they are treated as unrelated to the treatment and the outcome",
                        "es": "{nodes} no está en el grafo; se trata como ajeno al tratamiento y al resultado"},
    "ef.a_identification": {"en": "Identification", "es": "Identificación"},
    "ef.s_identification": {"en": "Under the stated causal graph, the adjustment set blocks every back-door path and contains no consequence of the treatment.",
                            "es": "Con el grafo causal indicado, el conjunto de ajuste bloquea todos los caminos por la puerta trasera y no contiene consecuencias del tratamiento."},
    "ef.a_graph": {"en": "Causal graph", "es": "Grafo causal"},
    "ef.s_graph": {"en": "The graph contains every common cause of the treatment and the outcome.",
                   "es": "El grafo contiene todas las causas comunes del tratamiento y del resultado."},
    "ef.d_graph": {"en": "a hypothesis written by a person; the data cannot confirm it.",
                   "es": "es una hipótesis escrita por una persona; los datos no pueden confirmarla."},
    "ef.a_refutation": {"en": "Refutation tests", "es": "Pruebas de refutación"},
    "ef.s_refutation": {"en": "Changes that should not matter (placebo treatment, random common cause, subsets) do not move the estimate.",
                        "es": "Cambios que no deberían importar (tratamiento placebo, causa común aleatoria, submuestras) no mueven la estimación."},
    "cg.r_placebo": {"en": "Placebo treatment (treatment shuffled {k} times)", "es": "Tratamiento placebo (tratamiento barajado {k} veces)"},
    "cg.r_placebo_d": {"en": "mean effect {mean:.4g}; {share:.0%} of the shuffles reach the size of the real estimate",
                       "es": "efecto medio {mean:.4g}; el {share:.0%} de las barajas alcanza el tamaño de la estimación real"},
    "cg.r_random": {"en": "Random common cause added", "es": "Causa común aleatoria añadida"},
    "cg.r_random_d": {"en": "estimate {new:.4g} against {old:.4g} ({rel:+.1%})", "es": "estimación {new:.4g} frente a {old:.4g} ({rel:+.1%})"},
    "cg.r_subset": {"en": "Random 80% subsets ({k})", "es": "Submuestras aleatorias del 80 % ({k})"},
    "cg.r_subset_d": {"en": "estimates from {lo:.4g} to {hi:.4g}; {share:.0%} on the same side of zero",
                      "es": "estimaciones de {lo:.4g} a {hi:.4g}; el {share:.0%} del mismo signo"},
    "cg.warn_not_identified": {"en": "The effect is not identified under the stated graph: the number shown is still confounded. See what is missing.",
                               "es": "El efecto no está identificado con el grafo indicado: la cifra mostrada sigue confundida. Mira qué falta."},
    "cg.same": {"en": "the treatment and the outcome are the same variable ({node}); choose a cause and a different outcome",
                "es": "el tratamiento y el resultado son la misma variable ({node}); elige una causa y un resultado distinto"},
    "cg.no_path": {"en": "The graph has no directed path from {t} to {y}: under it the treatment does not affect the outcome, so any non-zero estimate is confounding or noise.",
                   "es": "El grafo no tiene ningún camino dirigido de {t} a {y}: según él, el tratamiento no afecta al resultado, así que cualquier estimación distinta de cero es confusión o ruido."},
    "cg.name_hint": {"en": "{node} is not a column and is treated as unobserved; did you mean the column {col}? Names are case-sensitive.",
                     "es": "{node} no es una columna y se trata como no observada; ¿querías decir la columna {col}? Los nombres distinguen mayúsculas."},
    "cg.r_null": {"en": "no effect distinguishable from zero (the test is not informative)",
                  "es": "no hay un efecto distinguible de cero (la prueba no es informativa)"},
    "cg.warn_refuted": {"en": "A refutation test failed: {names}.", "es": "Falló una prueba de refutación: {names}."},
})

_EDGE = re.compile(r"^\s*(.+?)\s*(?:->|→|=>)\s*(.+?)\s*$")


class GraphError(FitError):
    pass


def parse_graph(spec) -> list[tuple[str, str]]:
    """Edges from 'a -> b' lines or comma/semicolon separated items, a chain 'a -> b -> c', or a list of pairs."""
    if isinstance(spec, (list, tuple)):
        items = [f"{e[0]} -> {e[1]}" if isinstance(e, (list, tuple)) else str(e) for e in spec]
    else:
        items = re.split(r"[\n;,]+", str(spec or ""))
    edges: list[tuple[str, str]] = []
    for raw in items:
        raw = raw.strip()
        if not raw or raw.startswith("#"):
            continue
        parts = [p.strip().strip("`'\"") for p in re.split(r"\s*(?:->|→|=>)\s*", raw)]
        if len(parts) < 2 or any(not p for p in parts):
            raise GraphError(tx("cg.bad_edge", edge=raw))
        for a, b in zip(parts, parts[1:]):
            if (a, b) not in edges:
                edges.append((a, b))
    return edges


class Graph:
    def __init__(self, edges: Iterable[tuple[str, str]], extra_nodes: Iterable[str] = ()):
        self.edges = list(edges)
        self.nodes = sorted({n for e in self.edges for n in e} | set(extra_nodes))
        self.pa = {n: set() for n in self.nodes}
        self.ch = {n: set() for n in self.nodes}
        for a, b in self.edges:
            self.pa[b].add(a)
            self.ch[a].add(b)
        self._check_acyclic()

    def _check_acyclic(self):
        state: dict[str, int] = {}

        def visit(n):
            state[n] = 1
            for c in self.ch[n]:
                if state.get(c) == 1:
                    raise GraphError(tx("cg.cycle", node=c))
                if not state.get(c):
                    visit(c)
            state[n] = 2
        for n in self.nodes:
            if not state.get(n):
                visit(n)

    def ancestors(self, nodes: Iterable[str]) -> set[str]:
        out, stack = set(), list(nodes)
        while stack:
            n = stack.pop()
            if n in out:
                continue
            out.add(n)
            stack.extend(self.pa.get(n, ()))
        return out

    def descendants(self, node: str) -> set[str]:
        out, stack = set(), [node]
        while stack:
            n = stack.pop()
            if n in out:
                continue
            out.add(n)
            stack.extend(self.ch.get(n, ()))
        return out - {node}

    def without_out_edges(self, node: str) -> "Graph":
        return Graph([(a, b) for a, b in self.edges if a != node], self.nodes)

    def d_separated(self, x: str, y: str, z: Iterable[str]) -> bool:
        """Lauritzen: moralise the ancestral graph of {x, y} | z, drop z, check connectivity."""
        z = set(z)
        anc = self.ancestors({x, y} | z)
        adj = {n: set() for n in anc}
        for n in anc:
            ps = [p for p in self.pa[n] if p in anc]
            for p in ps:
                adj[n].add(p); adj[p].add(n)
            for i, p in enumerate(ps):
                for q in ps[i + 1:]:
                    adj[p].add(q); adj[q].add(p)
        seen, stack = set(), [x]
        while stack:
            n = stack.pop()
            if n == y:
                return False
            if n in seen or n in z:
                continue
            seen.add(n)
            stack.extend(m for m in adj[n] if m not in z)
        return True


def _backdoor_ok(g: Graph, t: str, y: str, z: set[str]) -> bool:
    if z & (g.descendants(t) | {t, y}):
        return False
    return g.without_out_edges(t).d_separated(t, y, z)


def identify(edges, treatment: str, outcome: str, observed: Iterable[str], listed: Optional[list[str]] = None) -> dict:
    observed = set(observed)
    if treatment == outcome:
        raise GraphError(tx("cg.same", node=treatment))
    g = Graph(edges, [treatment, outcome])
    desc = g.descendants(treatment)
    mediators = {n for n in desc if outcome in g.descendants(n)} - {outcome}
    forbidden = [{"node": n, "why": "mediator" if n in mediators else "descendant",
                  "text": tx("cg.forbidden", node=n, why=tx("cg.why_mediator" if n in mediators else "cg.why_descendant"))}
                 for n in sorted(desc - {outcome}) if n in observed]
    unobserved = sorted(n for n in g.nodes if n not in observed)
    candidates = (g.ancestors({treatment, outcome}) & observed) - desc - {treatment, outcome}
    out: dict = {"treatment": treatment, "outcome": outcome, "edges": [list(e) for e in g.edges], "unobserved": unobserved,
                 "forbidden": forbidden, "missing": [], "instruments": [], "notes": []}
    if outcome not in desc:
        out["no_path"] = True
        out["notes"].append(tx("cg.no_path", t=treatment, y=outcome))
    lower = {c.lower(): c for c in observed}
    for n in unobserved:
        if n.lower() in lower and lower[n.lower()] != n:
            out["notes"].append(tx("cg.name_hint", node=n, col=lower[n.lower()]))
    if _backdoor_ok(g, treatment, outcome, candidates):
        z = set(candidates)
        for n in sorted(candidates, key=lambda n: (-len(g.descendants(n)), n)):
            if _backdoor_ok(g, treatment, outcome, z - {n}):
                z.discard(n)
        out.update(identified=True, adjustment_set=sorted(z),
                   text=tx("cg.identified", set=", ".join(sorted(z))) if z else tx("cg.identified_empty"))
    else:
        gm = g.without_out_edges(treatment)
        culprits = sorted(n for n in unobserved if n not in desc and treatment in g.descendants(n) and outcome in gm.descendants(n))
        culprits = culprits or unobserved
        out.update(identified=False, adjustment_set=sorted(candidates), text=tx("cg.not_identified", nodes=", ".join(culprits) or "?"))
        out["missing"] = [tx("cg.missing_measure", node=n) for n in culprits] + [tx("cg.missing_randomise")]
        for n in sorted(observed & set(g.nodes)):
            if n in (treatment, outcome) or n in desc or treatment not in g.ch[n]:
                continue
            if gm.d_separated(n, outcome, set()):
                out["instruments"].append(n)
                out["missing"].append(tx("cg.missing_instrument", node=n))
    if listed is not None:
        listed_set = {c for c in listed}
        not_in = sorted(c for c in listed_set if c not in g.nodes)
        ok = _backdoor_ok(g, treatment, outcome, listed_set & set(g.nodes))
        extra = ""
        if not ok:
            opened = sorted(c for c in listed_set & set(g.nodes) if c not in desc and
                            g.without_out_edges(treatment).d_separated(treatment, outcome, (listed_set & set(g.nodes)) - {c}))
            if opened:
                extra = tx("cg.listed_collider", nodes=", ".join(opened))
        out["listed_check"] = {"set": sorted(listed_set), "valid": ok, "text": tx("cg.listed_ok") if ok else tx("cg.listed_bad", extra=extra),
                               "not_in_graph": not_in, "note": tx("cg.not_in_graph", nodes=", ".join(not_in)) if not_in else ""}
    return out


def _treat_coef(y, treat, X) -> float:
    cols = [np.ones(len(y)), treat] + ([X] if X.size else [])
    XA = np.column_stack([c if c.ndim == 2 else c[:, None] for c in cols])
    f = ols(XA, y, "HC3")
    return float(coef_table(f, ["i", "t"] + [f"x{j}" for j in range(XA.shape[1] - 2)], 0.95)[1]["estimate"])


def refute(y, treat, X, estimate: float, seed: int = 7, k: int = 50, se: Optional[float] = None) -> dict:
    """Placebo treatment, random common cause and random-subset checks on a regression-adjusted estimate."""
    y = np.asarray(y, float); treat = np.asarray(treat, float)
    X = X if X.size else np.empty((len(y), 0))
    keep = np.isfinite(y) & np.isfinite(treat) & (np.isfinite(X).all(1) if X.shape[1] else True)
    y, treat, X = y[keep], treat[keep], X[keep]
    rng = np.random.default_rng(seed)
    n = len(y)
    # an estimate that is indistinguishable from zero cannot be told apart from a placebo or flip sign across subsets
    null = bool(se and se > 0 and abs(estimate) < 1.96 * se)
    plac = np.array([_treat_coef(y, rng.permutation(treat), X) for _ in range(k)])
    share = float(np.mean(np.abs(plac) >= abs(estimate))) if estimate else 1.0
    placebo = {"name": tx("cg.r_placebo", k=k), "mean": float(plac.mean()), "share_as_large": share,
               "passed": bool(share < 0.05 or null), "detail": tx("cg.r_placebo_d", mean=float(plac.mean()), share=share)}
    if null:
        placebo["not_informative"] = True
        placebo["detail"] += "; " + tx("cg.r_null")
    Xr = np.column_stack([X, rng.normal(size=n)]) if X.shape[1] else rng.normal(size=(n, 1))
    new = _treat_coef(y, treat, Xr)
    rel = (new - estimate) / abs(estimate) if estimate else 0.0
    random_cc = {"name": tx("cg.r_random"), "estimate": new, "relative_change": rel,
                 "passed": bool(abs(rel) < 0.1 or (null and abs(new - estimate) < 0.25 * se)),
                 "detail": tx("cg.r_random_d", new=new, old=estimate, rel=rel)}
    sub = []
    for _ in range(20):
        idx = rng.choice(n, size=max(int(0.8 * n), 8), replace=False)
        try:
            sub.append(_treat_coef(y[idx], treat[idx], X[idx]))
        except Exception:  # noqa: BLE001 - a degenerate subset is skipped
            continue
    sub = np.array(sub) if sub else np.array([estimate])
    same = float(np.mean(np.sign(sub) == np.sign(estimate))) if estimate else 1.0
    subset = {"name": tx("cg.r_subset", k=len(sub)), "low": float(sub.min()), "high": float(sub.max()), "same_sign": same,
              "passed": bool(same >= 0.9 or null), "detail": tx("cg.r_subset_d", lo=float(sub.min()), hi=float(sub.max()), share=same)}
    if null:
        subset["not_informative"] = True
        subset["detail"] += "; " + tx("cg.r_null")
    tests = [placebo, random_cc, subset]
    return {"tests": tests, "passed": all(t_["passed"] for t_ in tests), "failed": [t_["name"] for t_ in tests if not t_["passed"]]}
