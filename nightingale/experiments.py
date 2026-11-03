"""Experimental campaigns: design the runs, analyse the results, decide what to measure next.

* design     - 2-level full or fractional factorial (with generators, defining relation, resolution and alias
               structure), central composite, or a seeded maximin Latin hypercube; bounds and forbidden combinations
               are respected and what a restriction costs (balance, estimable terms) is reported.
* analyze    - main effects, two-factor interactions and (when the design allows) a quadratic response surface by
               least squares, with confidence intervals; an unreplicated saturated design is judged with Lenth's
               pseudo standard error instead of an invented error estimate.
* next_runs  - the run(s) that most reduce uncertainty about the fitted surface (largest prediction variance), or that
               have the best expected improvement for an objective. Each recommendation says why.
* choose_test - among tests with predicted outcomes per hypothesis, the one with the best information gain per cost.
"""
from __future__ import annotations

import ast
import itertools
import math
import re
from typing import Any, Optional

import numpy as np
from scipy import stats

from .linfit import FitError, load_frame, num, ols, tq, to_float
from .texts import t as tx

ROMAN = {3: "III", 4: "IV", 5: "V", 6: "VI", 7: "VII", 8: "VIII", 9: "IX", 10: "X"}
LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ"   # I and O skipped so words stay readable
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Minimum-aberration generators (Box, Hunter and Hunter): added factor -> word of earlier letters.
GENERATORS = {
    (3, 1): ["AB"], (4, 1): ["ABC"], (5, 1): ["ABCD"], (5, 2): ["AB", "AC"], (6, 1): ["ABCDE"],
    (6, 2): ["ABC", "BCD"], (6, 3): ["AB", "AC", "BC"], (7, 1): ["ABCDEF"], (7, 2): ["ABCD", "ABDE"],
    (7, 3): ["ABC", "BCD", "ACD"], (7, 4): ["AB", "AC", "BC", "ABC"], (8, 2): ["ABCD", "ABEF"],
    (8, 3): ["ABC", "ABD", "BCDE"], (8, 4): ["BCD", "ACD", "ABC", "ABD"],
}


class ExperimentError(FitError):
    pass


# ---------------------------------------------------------------- factors and constraints
def check_factors(factors: Any) -> list[dict]:
    if not isinstance(factors, list) or not factors:
        raise ExperimentError(tx("ex.need_factor"))
    if len(factors) > 12:
        raise ExperimentError(tx("ex.max_factors"))
    out, seen = [], set()
    for f in factors:
        if not isinstance(f, dict) or "name" not in f or "low" not in f or "high" not in f:
            raise ExperimentError(tx("ex.factor_fields"))
        nm = str(f["name"])
        if not _IDENT.match(nm):
            raise ExperimentError(tx("ex.factor_name", name=nm))
        if nm in seen:
            raise ExperimentError(tx("ex.factor_twice", name=nm))
        seen.add(nm)
        lo, hi = float(f["low"]), float(f["high"])
        if not (math.isfinite(lo) and math.isfinite(hi)) or hi <= lo:
            raise ExperimentError(tx("ex.factor_bounds", name=nm))
        out.append({"name": nm, "low": lo, "high": hi, "integer": bool(f.get("integer", False))})
    return out


def _eval(node: ast.AST, env: dict[str, np.ndarray]):
    if isinstance(node, ast.Expression):
        return _eval(node.body, env)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return float(node.value)
    if isinstance(node, ast.Name):
        if node.id not in env:
            raise ExperimentError(tx("ex.constraint_unknown", name=node.id, factors=list(env)))
        return env[node.id]
    if isinstance(node, ast.UnaryOp):
        v = _eval(node.operand, env)
        if isinstance(node.op, ast.USub):
            return -v
        if isinstance(node.op, ast.UAdd):
            return v
        if isinstance(node.op, ast.Not):
            return np.logical_not(v)
    if isinstance(node, ast.BinOp):
        a, b = _eval(node.left, env), _eval(node.right, env)
        ops = {ast.Add: np.add, ast.Sub: np.subtract, ast.Mult: np.multiply, ast.Div: np.divide, ast.Pow: np.power}
        if type(node.op) in ops:
            return ops[type(node.op)](a, b)
    if isinstance(node, ast.BoolOp):
        vals = [_eval(v, env) for v in node.values]
        f = np.logical_and if isinstance(node.op, ast.And) else np.logical_or
        r = vals[0]
        for v in vals[1:]:
            r = f(r, v)
        return r
    if isinstance(node, ast.Compare):
        left = _eval(node.left, env)
        res = None
        ops = {ast.Gt: np.greater, ast.GtE: np.greater_equal, ast.Lt: np.less, ast.LtE: np.less_equal,
               ast.Eq: np.equal, ast.NotEq: np.not_equal}
        for op, comp in zip(node.ops, node.comparators):
            right = _eval(comp, env)
            if type(op) not in ops:
                raise ExperimentError(tx("ex.constraint_ops"))
            piece = ops[type(op)](left, right)
            res = piece if res is None else np.logical_and(res, piece)
            left = right
        return res
    raise ExperimentError(tx("ex.constraint_ops_long"))


def forbidden_mask(rows: np.ndarray, factors: list[dict], forbidden: Optional[list]) -> np.ndarray:
    """True for rows that hit any forbidden combination. Entries are expressions ("A > 80 and B < 20") or boxes
    ({"A": [80, 100], "B": [0, 20]}, forbidden when every factor lies in its closed range)."""
    bad = np.zeros(len(rows), bool)
    env = {f["name"]: rows[:, i] for i, f in enumerate(factors)}
    for item in forbidden or []:
        if isinstance(item, str):
            try:
                tree = ast.parse(item.strip(), mode="eval")
            except SyntaxError as exc:
                raise ExperimentError(tx("ex.constraint_invalid", item=item, msg=exc.msg)) from None
            val = _eval(tree, env)
            bad |= np.broadcast_to(np.asarray(val, bool), bad.shape)
        elif isinstance(item, dict):
            m = np.ones(len(rows), bool)
            for nm, rg in item.items():
                if nm not in env:
                    raise ExperimentError(tx("ex.box_unknown", name=nm))
                m &= (env[nm] >= float(rg[0])) & (env[nm] <= float(rg[1]))
            bad |= m
        else:
            raise ExperimentError(tx("ex.forbidden_form"))
    return bad


def decode(coded: np.ndarray, factors: list[dict]) -> np.ndarray:
    lo = np.array([f["low"] for f in factors])
    hi = np.array([f["high"] for f in factors])
    return (lo + hi) / 2 + coded * (hi - lo) / 2


def encode(actual: np.ndarray, factors: list[dict]) -> np.ndarray:
    lo = np.array([f["low"] for f in factors])
    hi = np.array([f["high"] for f in factors])
    return (actual - (lo + hi) / 2) / ((hi - lo) / 2)


# ---------------------------------------------------------------- factorial structure
def _words(gens: list[str], base: int, added: list[int]) -> list[frozenset]:
    """Defining relation words: each added factor letter joined with its generator word, plus all products."""
    base_words = [frozenset(g) | {LETTERS[a]} for g, a in zip(gens, added)]
    out = set()
    for r in range(1, len(base_words) + 1):
        for combo in itertools.combinations(base_words, r):
            w = frozenset()
            for c in combo:
                w = w ^ c
            out.add(w)
    return sorted(out, key=lambda x: (len(x), sorted(x)))


def _resolution(words: list[frozenset]) -> int:
    return min(len(w) for w in words) if words else 99


def _greedy_generators(k: int, p: int) -> list[str]:
    b = k - p
    base = LETTERS[:b]
    chosen: list[str] = []
    for j in range(p):
        best, best_key = None, None
        for r in range(2, b + 1):
            for sub in itertools.combinations(base, r):
                g = "".join(sub)
                trial = chosen + [g]
                words = _words(trial, b, list(range(b, b + len(trial))))
                key = (_resolution(words), -sum(1 for w in words if len(w) == _resolution(words)))
                if best_key is None or key > best_key:
                    best, best_key = g, key
        chosen.append(best)
    return chosen


def fractional_structure(k: int, p: int) -> dict:
    if p == 0:
        return {"generators": [], "words": [], "resolution": None}
    if p >= k or k - p < 2:
        raise ExperimentError(tx("ex.few_base", k=k, p=p))
    gens = GENERATORS.get((k, p)) or _greedy_generators(k, p)
    added = list(range(k - p, k))
    words = _words(gens, k - p, added)
    return {"generators": [f"{LETTERS[a]}={g}" for a, g in zip(added, gens)], "gen_words": gens, "words": words,
            "resolution": _resolution(words)}


def coded_factorial(k: int, p: int = 0) -> tuple[np.ndarray, dict]:
    b = k - p
    n = 2 ** b
    base = np.array([[1 if (i >> j) & 1 else -1 for j in range(b)] for i in range(n)], float)
    st = fractional_structure(k, p)
    cols = [base[:, j] for j in range(b)]
    for g in st.get("gen_words", []):
        col = np.ones(n)
        for ch in g:
            col = col * base[:, LETTERS.index(ch)]
        cols.append(col)
    return np.column_stack(cols), st


def alias_structure(st: dict, names: list[str], k: int, max_len: int = 3) -> list[dict]:
    """Aliases of each main effect and two-factor interaction (words up to ``max_len`` letters)."""
    words = st.get("words") or []
    if not words:
        return []
    lab = lambda w: ":".join(names[LETTERS.index(c)] for c in sorted(w, key=LETTERS.index))  # noqa: E731
    out = []
    effects = [frozenset(LETTERS[i]) for i in range(k)] + [frozenset({LETTERS[i], LETTERS[j]}) for i in range(k) for j in range(i + 1, k)]
    for e in effects:
        al = sorted({e ^ w for w in words if len(e ^ w) <= max_len and (e ^ w) != e}, key=lambda x: (len(x), sorted(x)))
        out.append({"effect": lab(e), "aliased_with": [lab(a) for a in al]})
    return out


def model_columns(coded: np.ndarray, order: str) -> tuple[np.ndarray, list[str], list[str]]:
    """Columns for intercept, main effects, ('interactions') two-factor products, ('quadratic') squares."""
    n, k = coded.shape
    cols, kinds, idx = [np.ones(n)], ["intercept"], [()]
    for i in range(k):
        cols.append(coded[:, i]); kinds.append("main"); idx.append((i,))
    if order in ("interactions", "quadratic"):
        for i in range(k):
            for j in range(i + 1, k):
                cols.append(coded[:, i] * coded[:, j]); kinds.append("interaction"); idx.append((i, j))
    if order == "quadratic":
        for i in range(k):
            cols.append(coded[:, i] ** 2); kinds.append("quadratic"); idx.append((i, i))
    return np.column_stack(cols), kinds, idx


def properties(coded: np.ndarray) -> dict:
    n, k = coded.shape
    out: dict[str, Any] = {"runs": int(n)}
    X1, _, _ = model_columns(coded, "main")
    Xi, _, _ = model_columns(coded, "interactions")
    Xq, _, _ = model_columns(coded, "quadratic")
    rk = lambda M: int(np.linalg.matrix_rank(M))  # noqa: E731
    out["estimable"] = {"main_effects": rk(X1) == X1.shape[1], "two_factor_interactions": rk(Xi) == Xi.shape[1],
                        "quadratic": rk(Xq) == Xq.shape[1]}
    out["rank"] = {"main_effects": rk(X1), "two_factor_interactions": rk(Xi), "quadratic": rk(Xq)}
    out["parameters"] = {"main_effects": X1.shape[1], "two_factor_interactions": Xi.shape[1], "quadratic": Xq.shape[1]}
    if k > 1:
        C = np.corrcoef(coded.T) if np.all(coded.std(0) > 0) else np.eye(k)
        out["max_abs_correlation_between_factors"] = float(np.max(np.abs(C - np.eye(k))))
    else:
        out["max_abs_correlation_between_factors"] = 0.0
    if out["estimable"]["main_effects"]:
        out["d_efficiency_main_effects"] = float(np.linalg.det(X1.T @ X1) ** (1 / X1.shape[1]) / n)
    return out


# ---------------------------------------------------------------- designs
def _maximin_lhs(n: int, k: int, rng: np.random.Generator, tries: int = 100) -> np.ndarray:
    best, best_d = None, -1.0
    for _ in range(tries):
        u = np.column_stack([(rng.permutation(n) + rng.random(n)) / n for _ in range(k)])
        d = np.min(_pdist(u)) if n > 1 else 1.0
        if d > best_d:
            best, best_d = u, d
    return best


def _pdist(u: np.ndarray) -> np.ndarray:
    n = len(u)
    d = np.sqrt(((u[:, None, :] - u[None, :, :]) ** 2).sum(-1))
    return d[np.triu_indices(n, 1)]


def _farthest_point(pool: np.ndarray, n: int) -> np.ndarray:
    c = pool.mean(0)
    chosen = [int(np.argmin(((pool - c) ** 2).sum(1)))]
    dmin = ((pool - pool[chosen[0]]) ** 2).sum(1)
    while len(chosen) < n:
        nxt = int(np.argmax(dmin))
        chosen.append(nxt)
        dmin = np.minimum(dmin, ((pool - pool[nxt]) ** 2).sum(1))
    return pool[chosen]


def design_experiment(factors: list[dict], kind: str = "fractional", fraction: Optional[int] = None,
                      min_resolution: int = 4, max_runs: Optional[int] = None, center_points: Optional[int] = None,
                      variant: str = "face", runs: Optional[int] = None, seed: int = 0, randomize: bool = True,
                      forbidden: Optional[list] = None) -> dict:
    fs = check_factors(factors)
    k = len(fs)
    names = [f["name"] for f in fs]
    rng = np.random.default_rng(seed)
    warnings: list[str] = []
    info: dict[str, Any] = {"kind": kind}
    types: list[str] = []
    if kind in ("full", "fractional"):
        if k < 2:
            raise ExperimentError(tx("ex.full_min"))
        if kind == "full":
            if k > 10:
                raise ExperimentError(tx("ex.full_max"))
            p = 0
        else:
            if k < 3:
                raise ExperimentError(tx("ex.frac_min"))
            if k > 10:
                raise ExperimentError(tx("ex.frac_max"))
            if fraction is not None:
                p = int(fraction)
                if not 1 <= p <= k - 2:
                    raise ExperimentError(tx("ex.fraction_range", max=k - 2, k=k))
            else:
                options = []
                for pp in range(0, k - 1):
                    if k - pp < 2 or 2 ** (k - pp) < k + 1:
                        continue
                    st = fractional_structure(k, pp)
                    res = st["resolution"] if pp else 99
                    options.append((pp, 2 ** (k - pp), res))
                ok = [o for o in options if o[2] >= min_resolution]
                if not ok:
                    raise ExperimentError(tx("ex.no_resolution", k=k, res=min_resolution))
                if max_runs:
                    fit = [o for o in ok if o[1] <= max_runs]
                    if fit:
                        ok = fit
                    else:
                        warnings.append(tx("ex.warn_smallest", res=min_resolution, n=min(o[1] for o in ok), limit=max_runs))
                p = max(ok, key=lambda o: o[0])[0]
        coded, st = coded_factorial(k, p)
        types = ["factorial"] * len(coded)
        info.update({"fraction": p, "base_runs": int(len(coded)),
                     "generators": st.get("generators", []), "defining_relation": ["I = " + "".join(sorted(w, key=LETTERS.index)) for w in st.get("words", [])] if p else [],
                     "resolution": ROMAN.get(st["resolution"]) if p else "full", "resolution_number": st["resolution"] if p else None,
                     "letters": dict(zip(LETTERS, names))})
        if p:
            info["alias_structure"] = alias_structure(st, names, k)
            r = st["resolution"]
            info["interpretation"] = tx({3: "ex.res3", 4: "ex.res4", 5: "ex.res5"}.get(r, "ex.res_other"), r=ROMAN.get(r, r))
            if r <= 3:
                warnings.append(tx("ex.warn_res3"))
        nc = 3 if center_points is None else int(center_points)
        if nc:
            coded = np.vstack([coded, np.zeros((nc, k))])
            types += ["center"] * nc
    elif kind == "ccd":
        if not 2 <= k <= 6:
            raise ExperimentError(tx("ex.ccd_range"))
        if variant not in ("face", "inscribed", "circumscribed"):
            raise ExperimentError(tx("ex.ccd_variant"))
        p = 0 if k <= 4 else 1
        cube, st = coded_factorial(k, p)
        F = len(cube)
        alpha = {"face": 1.0, "circumscribed": F ** 0.25, "inscribed": 1.0}[variant]
        if variant == "inscribed":
            a = F ** 0.25
            cube = cube / a
            axial_val = 1.0
        else:
            axial_val = alpha
        axial = np.zeros((2 * k, k))
        for i in range(k):
            axial[2 * i, i] = -axial_val
            axial[2 * i + 1, i] = axial_val
        nc = (4 if center_points is None else int(center_points))
        coded = np.vstack([cube, axial, np.zeros((nc, k))])
        types = ["factorial"] * F + ["axial"] * (2 * k) + ["center"] * nc
        info.update({"variant": variant, "alpha": float(axial_val), "factorial_fraction": p,
                     "generators": st.get("generators", [])})
        if variant == "circumscribed":
            warnings.append(tx("ex.warn_circ", alpha=axial_val))
    elif kind == "lhs":
        n = int(runs) if runs else max(10, 5 * k)
        if n < k + 1 or n > 2000:
            raise ExperimentError(tx("ex.lhs_runs", lo=k + 1))
        if forbidden:
            pool_n = max(n * 8, 200)
            u = _maximin_lhs(pool_n, k, rng, tries=5)
            actual_pool = decode(2 * u - 1, fs)
            bad = forbidden_mask(actual_pool, fs, forbidden)
            ok = u[~bad]
            if len(ok) < n:
                raise ExperimentError(tx("ex.lhs_pool", ok=len(ok), pool=pool_n, share=len(ok) / pool_n, n=n))
            u = _farthest_point(ok, n)
            info["feasible_fraction"] = float(len(ok) / pool_n)
        else:
            u = _maximin_lhs(n, k, rng)
        coded = 2 * u - 1
        types = ["lhs"] * n
        info["min_pairwise_distance"] = float(np.min(_pdist(u)))
        info["space_filling"] = tx("ex.space_filling")
    else:
        raise ExperimentError(tx("ex.kind"))
    actual = decode(coded, fs)
    for i, f in enumerate(fs):
        if f["integer"]:
            actual[:, i] = np.round(actual[:, i])
    removed = []
    if forbidden and kind != "lhs":
        bad = forbidden_mask(actual, fs, forbidden)
        if bad.any():
            removed = [{"type": types[i], **{nm: float(actual[i, j]) for j, nm in enumerate(names)}} for i in np.where(bad)[0]]
            keep = ~bad
            coded, actual = coded[keep], actual[keep]
            types = [t for t, kp in zip(types, keep) if kp]
            warnings.append(tx("ex.warn_removed", n=len(removed), total=len(removed) + len(coded)))
    if len(coded) == 0:
        raise ExperimentError(tx("ex.all_forbidden"))
    prop = properties(coded)
    if removed:
        prop["note"] = tx("ex.prop_note")
        for key, label in (("main_effects", tx("ex.label_main")), ("two_factor_interactions", tx("ex.label_two"))):
            if not prop["estimable"][key]:
                warnings.append(tx("ex.warn_after_removal", label=label))
    if kind in ("full", "fractional") and len(coded) < k + 1:
        raise ExperimentError(tx("ex.too_few_remain"))
    order = np.arange(len(coded))
    std_order = order + 1
    if randomize:
        order = rng.permutation(len(coded))
    rows = []
    for rank, i in enumerate(order, 1):
        rows.append({"run": rank, "std_order": int(std_order[i]), "type": types[i],
                     **{nm: float(actual[i, j]) for j, nm in enumerate(names)}})
    if kind == "ccd" and variant == "circumscribed":
        out_b = [r["run"] for r in rows if any(r[f["name"]] < f["low"] - 1e-9 or r[f["name"]] > f["high"] + 1e-9 for f in fs)]
        info["runs_outside_bounds"] = out_b
    return {"kind": kind, "factors": fs, "runs": rows, "n_runs": len(rows), "info": info, "properties": prop,
            "removed_runs": removed, "randomized": bool(randomize), "seed": seed, "warnings": warnings,
            "coded": [[float(v) for v in coded[i]] for i in order],
            "note": tx("ex.design_note")}


# ---------------------------------------------------------------- model fitting
def _choose_terms(coded: np.ndarray, order: str):
    """Pick the estimable terms: intercept, mains, then interactions, then squares while rank < distinct settings."""
    n, k = coded.shape
    rounded = np.round(coded, 9)
    distinct = len({tuple(r) for r in rounded})
    levels = [len(set(rounded[:, j])) for j in range(k)]
    if order == "auto":
        order = "quadratic" if max(levels) >= 3 and n >= 1 + 2 * k + k * (k - 1) // 2 + 1 else "interactions"
        if k == 1:
            order = "quadratic" if levels[0] >= 3 else "main"
    X, kinds, idx = model_columns(coded, order)
    keep, rank, aliased, dropped = [0], 1, {}, []
    sel = X[:, [0]]
    for c in range(1, X.shape[1]):
        if kinds[c] == "quadratic" and levels[idx[c][0]] < 3:
            dropped.append((c, tx("ex.drop_levels")))
            continue
        if kinds[c] != "main" and rank >= distinct:
            dropped.append((c, tx("ex.drop_dof")))
            continue
        trial = np.hstack([sel, X[:, [c]]])
        if np.linalg.matrix_rank(trial) > rank:
            keep.append(c)
            sel = trial
            rank += 1
        else:
            # find the kept column it equals (up to sign) so the alias can be stated
            tgt = None
            for kc in keep:
                a, b = X[:, kc], X[:, c]
                if np.linalg.norm(a) > 0 and np.linalg.norm(b) > 0 and abs(abs(a @ b) / (np.linalg.norm(a) * np.linalg.norm(b)) - 1) < 1e-9:
                    tgt = kc
                    break
            if tgt is not None:
                aliased.setdefault(tgt, []).append(c)
            dropped.append((c, tx("ex.drop_aliased") if tgt is not None else tx("ex.drop_dependent")))
    quad_kept = [c for c in keep if kinds[c] == "quadratic"]
    if len(quad_kept) == 1 and aliased.get(quad_kept[0]):
        # squares of different factors are the same column (only centre points add a third level): a single pooled
        # curvature term is not a per-factor result, so it is left to the curvature test
        c = quad_kept[0]
        keep.remove(c)
        aliased.pop(c)
        dropped.append((c, tx("ex.drop_squares")))
        order = "interactions" if "interaction" in kinds else "main"
    return order, X, kinds, idx, keep, aliased, dropped, distinct


def _term_name(names: list[str], ix: tuple) -> str:
    if len(ix) == 0:
        return "intercept"
    if len(ix) == 1:
        return names[ix[0]]
    if ix[0] == ix[1]:
        return f"{names[ix[0]]}^2"
    return f"{names[ix[0]]}:{names[ix[1]]}"


def fit_surface(coded: np.ndarray, y: np.ndarray, names: list[str], order: str = "auto") -> dict:
    order_used, X, kinds, idx, keep, aliased, dropped, distinct = _choose_terms(coded, order)
    Xk = X[:, keep]
    n = len(y)
    fit = ols(Xk, y)
    labels = []
    for c in keep:
        nm = _term_name(names, idx[c])
        if c in aliased:
            nm += " (+ " + ", ".join(_term_name(names, idx[a]) for a in aliased[c]) + ")"
        labels.append(nm)
    return {"order": order_used, "keep": keep, "kinds": [kinds[c] for c in keep], "idx": [idx[c] for c in keep],
            "labels": labels, "fit": fit, "aliased": {_term_name(names, idx[c]): [_term_name(names, idx[a]) for a in v] for c, v in aliased.items()},
            "dropped": [{"term": _term_name(names, idx[c]), "reason": r} for c, r in dropped], "distinct": distinct, "n": n,
            "dof": fit["dof"], "names": names}


def _row_features(s: dict, coded: np.ndarray) -> np.ndarray:
    coded = np.atleast_2d(coded)
    cols = []
    for ix in s["idx"]:
        if len(ix) == 0:
            cols.append(np.ones(len(coded)))
        elif len(ix) == 1:
            cols.append(coded[:, ix[0]])
        else:
            cols.append(coded[:, ix[0]] * coded[:, ix[1]])
    return np.column_stack(cols)


def predict_surface(s: dict, coded: np.ndarray, level: float = 0.95) -> dict:
    F = _row_features(s, coded)
    fit = s["fit"]
    mean = F @ fit["beta"]
    var_unit = np.einsum("ij,jk,ik->i", F, fit["bread"], F)
    if fit["dof"] > 0:
        sd_mean = np.sqrt(fit["sigma2"] * var_unit)
        t = tq(fit["dof"], level)
        sd_pred = np.sqrt(fit["sigma2"] * (1 + var_unit))
        return {"mean": mean, "se_mean": sd_mean, "ci_half": t * sd_mean, "pi_half": t * sd_pred, "rel_var": var_unit}
    return {"mean": mean, "se_mean": None, "ci_half": None, "pi_half": None, "rel_var": var_unit}


def lenth(effects: np.ndarray, level: float = 0.95) -> dict:
    a = np.abs(effects)
    s0 = 1.5 * np.median(a)
    pse = 1.5 * np.median(a[a < 2.5 * s0]) if np.any(a < 2.5 * s0) else s0
    m = len(a)
    d = m / 3.0
    me = float(stats.t.ppf(0.5 + level / 2, d) * pse)
    sme = float(stats.t.ppf((1 + level ** (1 / m)) / 2, d) * pse)
    return {"pse": float(pse), "df": d, "margin_of_error": me, "simultaneous_margin": sme}


def analyze_experiment(X_actual: np.ndarray, y: np.ndarray, factors: list[dict], response: str, model: str = "auto",
                       level: float = 0.95, surface_grid: int = 15) -> dict:
    fs = factors
    names = [f["name"] for f in fs]
    k = len(fs)
    n = len(y)
    if n < k + 2:
        raise ExperimentError(tx("ex.too_few_runs", n=n, k=k, need=k + 2))
    if model not in ("auto", "main", "interactions", "quadratic"):
        raise ExperimentError(tx("ex.model_kind"))
    coded = encode(X_actual, fs)
    s = fit_surface(coded, y, names, model)
    fit = s["fit"]
    warnings: list[str] = []
    notes: list[str] = []
    dof = fit["dof"]
    terms = []
    saturated = dof <= 0
    coefs = np.array(fit["beta"])
    L = None
    key0 = [tuple(r) for r in np.round(coded, 9)]
    grp0: dict = {}
    for kk, v in zip(key0, y):
        grp0.setdefault(kk, []).append(v)
    has_pure_error = any(len(g) > 1 for g in grp0.values())
    eff_idx = [i for i, kd in enumerate(s["kinds"]) if kd != "intercept"]
    few = (not saturated) and dof < 3 and not has_pure_error and len(eff_idx) >= 6
    if saturated or few:
        if len(eff_idx) >= 4:
            L = lenth(2 * coefs[eff_idx], level)
    if few:
        warnings.append(tx("ex.warn_lenth_few", dof=dof))
    if saturated:
        warnings.append(tx("ex.warn_saturated_lenth" if L else "ex.warn_saturated"))
    else:
        if dof <= 2 and not few:
            warnings.append(tx("ex.warn_dof", dof=dof))
    t = tq(dof, level) if dof > 0 else float("nan")
    for i, (kd, lab) in enumerate(zip(s["kinds"], s["labels"])):
        b = float(coefs[i])
        row = {"term": lab, "kind": kd, "coefficient": b}
        if kd != "intercept":
            row["effect"] = 2 * b if kd in ("main", "interaction") else None
        if dof > 0:
            se = float(fit["se"][i])
            tv = b / se if se > 0 else float("nan")
            row.update({"se": se, "ci_low": b - t * se, "ci_high": b + t * se, "t": num(tv),
                        "p_value": float(2 * stats.t.sf(abs(tv), dof)) if math.isfinite(tv) else None})
            row["significant"] = bool(row["p_value"] is not None and row["p_value"] < 1 - level) if kd != "intercept" else None
        if L and kd != "intercept" and (dof <= 0 or few):
            e = abs(2 * b)
            row["significant"] = bool(e > L["margin_of_error"])
            row["significant_simultaneous"] = bool(e > L["simultaneous_margin"])
        terms.append(row)
    # replicates, pure error and lack of fit
    key = [tuple(r) for r in np.round(coded, 9)]
    groups: dict = {}
    for kk, v in zip(key, y):
        groups.setdefault(kk, []).append(v)
    df_pe = sum(len(g) - 1 for g in groups.values())
    lof = None
    if df_pe > 0:
        sspe = sum(float(np.sum((np.array(g) - np.mean(g)) ** 2)) for g in groups.values())
        df_lof = dof - df_pe
        out_pe = {"df": df_pe, "sd": math.sqrt(sspe / df_pe) if sspe >= 0 else None}
        if df_lof > 0:
            F = ((fit["rss"] - sspe) / df_lof) / (sspe / df_pe) if sspe > 0 else float("inf")
            lof = {"pure_error": out_pe, "lack_of_fit_df": df_lof, "F": num(F) if math.isfinite(F) else None,
                   "p_value": float(stats.f.sf(F, df_lof, df_pe)) if math.isfinite(F) else 0.0}
            if lof["p_value"] < 0.05:
                warnings.append(tx("ex.warn_lof", p=lof["p_value"]))
        else:
            lof = {"pure_error": out_pe}
    # curvature with centre points on a model that has no squares
    curv = None
    is_center = np.all(np.abs(coded) < 1e-9, axis=1)
    if is_center.sum() >= 1 and "quadratic" not in s["kinds"] and (~is_center).sum() >= 2:
        yc, yf = y[is_center], y[~is_center]
        diff = float(yf.mean() - yc.mean())
        if is_center.sum() >= 2:
            v = np.var(yc, ddof=1)
            se = math.sqrt(v * (1 / len(yc) + 1 / len(yf)))
            dfc = len(yc) - 1
        elif dof > 0:
            se = math.sqrt(fit["sigma2"] * (1 / len(yc) + 1 / len(yf)))
            dfc = dof
        else:
            se, dfc = float("nan"), 0
        tv = diff / se if se and math.isfinite(se) and se > 0 else float("nan")
        curv = {"factorial_mean_minus_center_mean": diff, "centre_points": int(is_center.sum()),
                "p_value": float(2 * stats.t.sf(abs(tv), dfc)) if math.isfinite(tv) and dfc > 0 else None}
        if curv["p_value"] is not None and curv["p_value"] < 0.05:
            warnings.append(tx("ex.warn_curv", p=curv["p_value"]))
        elif curv["p_value"] is None:
            notes.append(tx("ex.note_center_untested"))
    elif is_center.sum() == 0 and "quadratic" not in s["kinds"]:
        notes.append(tx("ex.note_no_center"))
    if s["aliased"]:
        notes.append(tx("ex.note_aliased", items="; ".join(tx("ex.alias_item", a=a, b=", ".join(b))
                                                          for a, b in s["aliased"].items())))
    if s["dropped"]:
        notes.append(tx("ex.note_dropped", items="; ".join(f"{d['term']} ({d['reason']})" for d in s["dropped"][:12])))
    if dof > 0 and s["order"] != "main":
        notes.append(tx("ex.note_interval"))
    res = fit["resid"]
    normal = None
    if 8 <= n <= 5000 and dof >= 8:
        sw = stats.shapiro(res)
        normal = {"shapiro_p": float(sw.pvalue)}
        if sw.pvalue < 0.01:
            warnings.append(tx("ex.warn_normal", p=sw.pvalue))
    stats_out = {"n": n, "terms": int(fit["rank"]), "residual_df": int(dof), "r2": num(fit["r2"]), "adj_r2": num(fit["adj_r2"]),
                 "rmse": num(math.sqrt(fit["sigma2"])) if dof > 0 else None, "model": s["order"]}
    out: dict[str, Any] = {"response": response, "factors": fs, "model": s["order"], "terms": terms, "fit": stats_out,
                           "coding": tx("ex.coding"),
                           "level": level, "lenth": L, "lack_of_fit": lof, "curvature": curv, "residual_check": normal,
                           "warnings": warnings, "notes": notes,
                           "residuals": [{"fitted": float(f), "residual": float(r)} for f, r in zip(y - res, res)]}
    # ranking of influential terms
    ranked = sorted([x for x in terms if x["kind"] != "intercept"], key=lambda x: -abs(x["coefficient"]))
    out["ranking"] = [x["term"] for x in ranked]
    # response surface: chart grid over the two largest main effects, others at their centre
    mains = [x for x in terms if x["kind"] == "main"]
    if k >= 2 and mains and dof > 0:
        top = sorted(range(k), key=lambda j: -abs(next((t_["coefficient"] for t_ in terms if t_["term"] == names[j]), 0.0)))[:2]
        g = np.linspace(-1, 1, surface_grid)
        gx, gy = np.meshgrid(g, g)
        pts = np.zeros((gx.size, k))
        pts[:, top[0]] = gx.ravel()
        pts[:, top[1]] = gy.ravel()
        pr = predict_surface(s, pts, level)
        xs = decode(np.zeros((surface_grid, k)) + np.where(np.arange(k) == top[0], g[:, None], 0), fs)[:, top[0]]
        ys = decode(np.zeros((surface_grid, k)) + np.where(np.arange(k) == top[1], g[:, None], 0), fs)[:, top[1]]
        out["surface"] = {"x": names[top[0]], "y": names[top[1]], "x_values": [float(v) for v in xs], "y_values": [float(v) for v in ys],
                          "mean": [[float(pr["mean"][i * surface_grid + j]) for j in range(surface_grid)] for i in range(surface_grid)],
                          "ci_half": [[float(pr["ci_half"][i * surface_grid + j]) for j in range(surface_grid)] for i in range(surface_grid)],
                          "others_held_at": tx("ex.others_at")}
    # main effect predictions with confidence at the centre of the other factors
    if dof > 0:
        me = []
        for j in range(k):
            lo_pt, hi_pt = np.zeros(k), np.zeros(k)
            lo_pt[j], hi_pt[j] = -1, 1
            pr = predict_surface(s, np.vstack([lo_pt, hi_pt]), level)
            me.append({"factor": names[j], "at_low": float(pr["mean"][0]), "at_high": float(pr["mean"][1]),
                       "ci_half_low": float(pr["ci_half"][0]), "ci_half_high": float(pr["ci_half"][1])})
        out["main_effect_predictions"] = me
    # stationary point of a quadratic surface
    if "quadratic" in s["kinds"] and dof > 0:
        b = np.zeros(k)
        B = np.zeros((k, k))
        for i, (kd, ix) in enumerate(zip(s["kinds"], s["idx"])):
            if kd == "main":
                b[ix[0]] = coefs[i]
            elif kd == "quadratic":
                B[ix[0], ix[0]] = coefs[i]
            elif kd == "interaction":
                B[ix[0], ix[1]] = B[ix[1], ix[0]] = coefs[i] / 2
        try:
            ev = np.linalg.eigvalsh(B)
            if np.all(np.abs(ev) > 1e-9):
                xs_ = -0.5 * np.linalg.solve(B, b)
                kind = "maximum" if np.all(ev < 0) else "minimum" if np.all(ev > 0) else "saddle point"
                pr = predict_surface(s, xs_[None], level)
                inside = bool(np.all(np.abs(xs_) <= 1 + 1e-9))
                out["stationary_point"] = {"type": kind, "inside_region": inside, "settings": {nm: float(v) for nm, v in zip(names, decode(xs_[None], fs)[0])},
                                           "predicted": float(pr["mean"][0]), "ci_half": float(pr["ci_half"][0]),
                                           "note": None if inside else tx("ex.stationary_note")}
                if not inside:
                    warnings.append(tx("ex.warn_stationary"))
        except np.linalg.LinAlgError:
            pass
    best = int(np.argmax(y)), int(np.argmin(y))
    out["observed_extremes"] = {"max": {"run_index": best[0], "value": float(y[best[0]]), "settings": {nm: float(X_actual[best[0], j]) for j, nm in enumerate(names)}},
                                "min": {"run_index": best[1], "value": float(y[best[1]]), "settings": {nm: float(X_actual[best[1], j]) for j, nm in enumerate(names)}}}
    out["_surface"] = s
    return out


# ---------------------------------------------------------------- what to measure next
def candidate_points(factors: list[dict], forbidden: Optional[list], given: Optional[list], seed: int = 0,
                     n_random: int = 1500) -> np.ndarray:
    k = len(factors)
    names = [f["name"] for f in factors]
    if given:
        pts = []
        for c in given:
            if not isinstance(c, dict) or any(nm not in c for nm in names):
                raise ExperimentError(tx("ex.cand_all", names=names))
            pts.append([float(c[nm]) for nm in names])
        pts = np.array(pts)
        for j, f in enumerate(factors):
            if np.any(pts[:, j] < f["low"] - 1e-9) or np.any(pts[:, j] > f["high"] + 1e-9):
                raise ExperimentError(tx("ex.cand_bounds", name=f["name"], lo=f["low"], hi=f["high"]))
    else:
        rng = np.random.default_rng(seed)
        parts = [decode(2 * rng.random((n_random, k)) - 1, factors), decode(np.zeros((1, k)), factors)]
        if k <= 7:
            parts.append(decode(np.array(list(itertools.product([-1, 1], repeat=k)), float), factors))
        for f_i in range(k):   # faces centres and edge midpoints help when the surface is curved
            for sgn in (-1, 1):
                c = np.zeros((1, k)); c[0, f_i] = sgn
                parts.append(decode(c, factors))
        pts = np.vstack(parts)
    for j, f in enumerate(factors):
        if f.get("integer"):
            pts[:, j] = np.round(pts[:, j])
    if forbidden:
        pts = pts[~forbidden_mask(pts, factors, forbidden)]
    if len(pts) == 0:
        raise ExperimentError(tx("ex.cand_none"))
    return np.unique(np.round(pts, 12), axis=0)


def next_runs(X_actual: np.ndarray, y: np.ndarray, factors: list[dict], response: str, objective: str = "explore",
              n: int = 1, model: str = "auto", forbidden: Optional[list] = None, candidates: Optional[list] = None,
              seed: int = 0, xi: float = 0.0) -> dict:
    if objective not in ("explore", "maximize", "minimize"):
        raise ExperimentError(tx("ex.objective"))
    if not 1 <= n <= 20:
        raise ExperimentError(tx("ex.n_range"))
    names = [f["name"] for f in factors]
    coded = encode(X_actual, factors)
    s = fit_surface(coded, y, names, model)
    fit = s["fit"]
    dof = fit["dof"]
    warnings: list[str] = []
    sigma = math.sqrt(fit["sigma2"]) if dof > 0 else 1.0
    relative = dof <= 0
    if relative:
        warnings.append(tx("ex.warn_relative"))
    elif dof <= 2:
        warnings.append(tx("ex.warn_shaky", dof=dof))
    pts = candidate_points(factors, forbidden, candidates, seed)
    C = encode(pts, factors)
    F = _row_features(s, C)
    bread = fit["bread"].copy()
    mu = F @ fit["beta"]
    var_unit0 = np.einsum("ij,jk,ik->i", F, bread, F)
    best_obs = float(np.max(y) if objective == "maximize" else np.min(y)) if objective != "explore" else None
    rows, used = [], set()
    var_unit = var_unit0.copy()
    cur_bread = bread
    max_before = float(math.sqrt(var_unit0.max()) * sigma)
    nearest_all = lambda c: float(np.min(np.linalg.norm(coded - c, axis=1)))  # noqa: E731
    ei0 = None
    median_sd = float(np.median(np.sqrt(var_unit0)) * sigma)
    centre_sd = float(math.sqrt(float(_row_features(s, np.zeros((1, len(factors))))[0] @ bread @ _row_features(s, np.zeros((1, len(factors))))[0])) * sigma)
    for r in range(1, n + 1):
        sd = np.sqrt(np.maximum(var_unit, 0)) * sigma
        if objective == "explore":
            score = var_unit.copy()
        else:
            imp = (mu - best_obs - xi) if objective == "maximize" else (best_obs - mu - xi)
            with np.errstate(divide="ignore", invalid="ignore"):
                z = np.where(sd > 0, imp / sd, 0.0)
            score = np.where(sd > 0, imp * stats.norm.cdf(z) + sd * stats.norm.pdf(z), np.maximum(imp, 0))
            if r == 1:
                ei0 = score.copy()
        score = score.copy()
        for u in used:
            score[u] = -np.inf
        i = int(np.argmax(score))
        if not np.isfinite(score[i]):
            break
        if objective != "explore" and score[i] <= 1e-12 * max(1.0, abs(best_obs or 1)):
            warnings.append(tx("ex.warn_no_ei"))
            objective_row = "explore"
            score_alt = var_unit.copy()
            for u in used:
                score_alt[u] = -np.inf
            i = int(np.argmax(score_alt))
        else:
            objective_row = objective
        used.add(i)
        x = F[i]
        row = {"rank": r, "settings": {nm: float(pts[i, j]) for j, nm in enumerate(names)},
               "coded": [float(v) for v in C[i]], "predicted_mean": float(mu[i]),
               "predicted_sd": float(math.sqrt(max(var_unit[i], 0)) * sigma), "relative_variance": float(var_unit[i]),
               "nearest_existing_run_distance": nearest_all(C[i]) if r == 1 else float(min(nearest_all(C[i]), min((np.linalg.norm(C[i] - C[u]) for u in used if u != i), default=np.inf))),
               "objective": objective_row}
        if objective != "explore":
            row["expected_improvement"] = float(score[i]) if objective_row == objective else 0.0
        # sequential update: variance only (the mean is held), as a measured point would add information
        v = cur_bread @ x
        cur_bread = cur_bread - np.outer(v, v) / (1.0 + float(x @ v))
        var_unit = np.einsum("ij,jk,ik->i", F, cur_bread, F)
        row["max_prediction_sd_after"] = float(math.sqrt(max(var_unit.max(), 0)) * sigma)
        rows.append(row)
    for row in rows:
        sdv = row["predicted_sd"]
        parts = []
        if row["objective"] == "explore":
            parts.append(tx("ex.why_explore", n=len(pts), sd=sdv, units="" if relative else tx("ex.units_suffix"),
                            median=median_sd, centre=centre_sd))
        else:
            parts.append(tx("ex.why_best", objective=objective, ei=row["expected_improvement"],
                            mean=row["predicted_mean"], best=best_obs, sd=sdv))
        d = row["nearest_existing_run_distance"]
        if d < 1e-9:
            row["replicate"] = True
            parts.append(tx("ex.why_repeat"))
        else:
            parts.append(tx("ex.why_distance", d=d) if np.isfinite(d) else "")
        parts.append(tx("ex.why_lower", before=max_before, after=row["max_prediction_sd_after"]))
        row["why"] = "; ".join(p for p in parts if p) + "."
        outside = any(row["coded"][j] > 1.0 + 1e-9 or row["coded"][j] < -1.0 - 1e-9 for j in range(len(names)))
        if outside:
            row["why"] += tx("ex.why_outside")
    alt_idx = np.argsort(-(var_unit0 if objective == "explore" else (ei0 if ei0 is not None else var_unit0)))[:6]
    alts = [{"settings": {nm: float(pts[i, j]) for j, nm in enumerate(names)}, "predicted_mean": float(mu[i]),
             "predicted_sd": float(math.sqrt(max(var_unit0[i], 0)) * sigma),
             **({"expected_improvement": float(ei0[i])} if ei0 is not None else {})} for i in alt_idx]
    s_out = {"model": s["order"], "terms": s["labels"], "residual_df": int(dof), "noise_sd": None if relative else float(sigma),
             "n_runs": int(len(y)), "candidates_considered": int(len(pts))}
    s_out["caveat"] = tx("ex.next_caveat")
    return {"objective": objective, "response": response, "recommendations": rows, "alternatives": alts, "model": s_out,
            "max_prediction_sd_before": max_before, "warnings": warnings}


# ---------------------------------------------------------------- choosing among discrete hypotheses
def _entropy(p: np.ndarray) -> float:
    p = p[p > 0]
    return float(-(p * np.log2(p)).sum())


def _outcome_matrix(h_names: list[str], test: dict) -> tuple[list[str], np.ndarray]:
    outs = test.get("outcomes")
    if not isinstance(outs, dict):
        raise ExperimentError(tx("ex.test_outcomes", test=test.get("name")))
    missing = [h for h in h_names if h not in outs]
    if missing:
        raise ExperimentError(tx("ex.test_missing", test=test.get("name"), h=missing[0]))
    labels: list[str] = []
    dist = {}
    for h in h_names:
        o = outs[h]
        if isinstance(o, str):
            o = {o: 1.0}
        if not isinstance(o, dict) or not o:
            raise ExperimentError(tx("ex.test_label", test=test.get("name"), h=h))
        tot = sum(float(v) for v in o.values())
        if abs(tot - 1) > 1e-6 or any(float(v) < 0 for v in o.values()):
            raise ExperimentError(tx("ex.test_probs", test=test.get("name"), h=h, tot=tot))
        dist[h] = {str(a): float(b) for a, b in o.items()}
        for a in dist[h]:
            if a not in labels:
                labels.append(a)
    M = np.array([[dist[h].get(a, 0.0) for a in labels] for h in h_names])
    return labels, M


def _posterior(prior: np.ndarray, M: np.ndarray):
    joint = prior[:, None] * M
    p_out = joint.sum(0)
    post = np.where(p_out > 0, joint / np.where(p_out > 0, p_out, 1), 0.0)
    return p_out, post


def choose_test(hypotheses: list, tests: list, threshold: float = 0.95, max_depth: int = 3) -> dict:
    if not isinstance(hypotheses, list) or len(hypotheses) < 2:
        raise ExperimentError(tx("ex.two_hyp"))
    if not isinstance(tests, list) or not tests:
        raise ExperimentError(tx("ex.one_test"))
    h_names, prior = [], []
    for h in hypotheses:
        if isinstance(h, str):
            h_names.append(h); prior.append(None)
        else:
            h_names.append(str(h["name"])); prior.append(h.get("prior"))
    if len(set(h_names)) != len(h_names):
        raise ExperimentError(tx("ex.hyp_unique"))
    if any(p is None for p in prior):
        if any(p is not None for p in prior):
            raise ExperimentError(tx("ex.prior_all"))
        prior_a = np.full(len(h_names), 1 / len(h_names))
    else:
        prior_a = np.array([float(p) for p in prior])
        if np.any(prior_a < 0) or prior_a.sum() <= 0:
            raise ExperimentError(tx("ex.prior_nonneg"))
        prior_a = prior_a / prior_a.sum()
    parsed = []
    names = set()
    for t in tests:
        nm = str(t.get("name"))
        if nm in names:
            raise ExperimentError(tx("ex.test_repeated", name=nm))
        names.add(nm)
        cost = float(t.get("cost", 1.0))
        if not cost > 0:
            raise ExperimentError(tx("ex.test_cost", name=nm))
        labels, M = _outcome_matrix(h_names, t)
        parsed.append({"name": nm, "cost": cost, "labels": labels, "M": M})
    H0 = _entropy(prior_a)

    def evaluate(t, pri):
        p_out, post = _posterior(pri, t["M"])
        H_after = float(sum(po * _entropy(post[:, j]) for j, po in enumerate(p_out)))
        return _entropy(pri) - H_after, p_out, post

    rows = []
    for t in parsed:
        ig, p_out, post = evaluate(t, prior_a)
        sep = []
        n_h = len(h_names)
        for a in range(n_h):
            for b in range(a + 1, n_h):
                tv = 0.5 * float(np.abs(t["M"][a] - t["M"][b]).sum())
                sep.append({"pair": [h_names[a], h_names[b]], "distinguishability": tv})
        full = bool(abs(ig - H0) < 1e-9)
        rows.append({"test": t["name"], "cost": t["cost"], "information_gain_bits": ig, "gain_per_cost": ig / t["cost"],
                     "fully_separates": full, "separation": sep,
                     "outcomes": [{"outcome": lab, "probability": float(p_out[j]),
                                   "posterior": {h: float(post[i, j]) for i, h in enumerate(h_names)}} for j, lab in enumerate(t["labels"])]})
    rows.sort(key=lambda r: (-r["gain_per_cost"], r["cost"]))
    best = rows[0]
    if best["information_gain_bits"] <= 1e-12:
        raise ExperimentError(tx("ex.no_discrim"))
    full_tests = sorted([r for r in rows if r["fully_separates"]], key=lambda r: r["cost"])

    def plan(pri, left, depth):
        if depth == 0 or float(pri.max()) >= threshold or not left:
            return None
        cand = []
        for t in left:
            ig, p_out, post = evaluate(t, pri)
            if ig > 1e-9:
                cand.append((ig / t["cost"], -t["cost"], t, p_out, post))
        if not cand:
            return None
        cand.sort(key=lambda c: (c[0], c[1]), reverse=True)
        _, _, t, p_out, post = cand[0]
        rest = [x for x in left if x is not t]
        node = {"test": t["name"], "cost": t["cost"], "branches": []}
        exp_cost = t["cost"]
        for j, lab in enumerate(t["labels"]):
            if p_out[j] <= 1e-12:
                continue
            sub = plan(post[:, j], rest, depth - 1)
            br = {"outcome": lab, "probability": float(p_out[j]), "posterior": {h: float(post[i, j]) for i, h in enumerate(h_names)},
                  "next": sub}
            if sub:
                exp_cost += p_out[j] * sub["expected_cost"]
            node["branches"].append(br)
        node["expected_cost"] = float(exp_cost)
        return node

    tree = plan(prior_a, parsed, max_depth)
    reason = tx("ex.choose_reason", test=best["test"], gain=best["information_gain_bits"], h0=H0, cost=best["cost"],
                gpc=best["gain_per_cost"])
    if best["fully_separates"]:
        reason += tx("ex.choose_single")
    elif full_tests:
        reason += tx("ex.choose_partial", test=full_tests[0]["test"], cost=full_tests[0]["cost"])
    else:
        reason += tx("ex.choose_none")
    return {"hypotheses": h_names, "prior": {h: float(p) for h, p in zip(h_names, prior_a)}, "uncertainty_bits": H0,
            "recommendation": {"test": best["test"], "cost": best["cost"], "information_gain_bits": best["information_gain_bits"],
                               "gain_per_cost": best["gain_per_cost"], "fully_separates": best["fully_separates"], "why": reason},
            "cheapest_fully_separating": None if not full_tests else {"test": full_tests[0]["test"], "cost": full_tests[0]["cost"]},
            "ranking": rows, "plan": tree, "stop_when_posterior_at_least": threshold,
            "assumptions": tx("ex.assumptions")}


# ---------------------------------------------------------------- service wrappers
def _frame(services, dataset: str, factors: list[dict], response: str):
    spec = {f["name"]: "num" for f in factors}
    spec[response] = "num"
    cols, _ = load_frame(services, dataset, spec)
    X = np.column_stack([to_float(cols[f["name"]]) for f in factors])
    y = to_float(cols[response])
    keep = np.isfinite(X).all(1) & np.isfinite(y)
    return X[keep], y[keep], int(len(y) - keep.sum())


def _factors_from_data(services, dataset: str, factors: list) -> list[dict]:
    """Factors may omit low/high: then the observed range of the column is used (and said so)."""
    out = []
    need = [f for f in factors if isinstance(f, dict) and ("low" not in f or "high" not in f)]
    if need:
        cols, _ = load_frame(services, dataset, {f["name"]: "num" for f in need})
    for f in factors:
        if isinstance(f, str):
            f = {"name": f}
        if "low" not in f or "high" not in f:
            v = to_float(cols[f["name"]]) if need and f["name"] in cols else to_float(load_frame(services, dataset, {f["name"]: "num"})[0][f["name"]])
            v = v[np.isfinite(v)]
            if len(v) == 0:
                raise ExperimentError(tx("ex.no_numeric", name=f["name"]))
            f = {**f, "low": float(v.min()), "high": float(v.max()), "_inferred": True}
        out.append(f)
    res = check_factors(out)
    for r, f in zip(res, out):
        if f.get("_inferred"):
            r["range_inferred_from_data"] = True
    return res


def design(services, factors, kind="fractional", save_as: Optional[str] = None, source: str = "ui", **kw) -> dict:
    def do():
        res = design_experiment(factors, kind, **kw)
        if save_as:
            import csv
            import io
            buf = io.StringIO()
            names = [f["name"] for f in res["factors"]]
            w = csv.writer(buf, lineterminator="\n")
            w.writerow(["run", "std_order", "type"] + names)
            for r in res["runs"]:
                w.writerow([r["run"], r["std_order"], r["type"]] + [r[n] for n in names])
            saved = services.ingest_text(buf.getvalue(), save_as, "csv", None, source=source)
            res["saved_dataset"] = saved.get("name", save_as)
        res["_log_summary"] = (f"designed {res['kind']} experiment: {len(res['factors'])} factors, {res['n_runs']} runs"
                               + (f", resolution {res['info'].get('resolution')}" if res["info"].get("resolution") else ""))
        return res
    return services._log("experiment_design", source, None, {"kind": kind, "factors": [f.get("name") for f in factors] if isinstance(factors, list) else None}, do)


def analyze(services, dataset: str, factors, response: str, model: str = "auto", level: float = 0.95, source: str = "ui") -> dict:
    def do():
        fs = _factors_from_data(services, dataset, factors)
        X, y, dropped = _frame(services, dataset, fs, response)
        res = analyze_experiment(X, y, fs, response, model, level)
        res.pop("_surface", None)
        res["dataset"] = dataset
        if dropped:
            res["warnings"].append(tx("ex.warn_ignored", n=dropped))
        if any(f.get("range_inferred_from_data") for f in fs):
            res["warnings"].append(tx("ex.warn_inferred"))
        top = res["ranking"][:2]
        res["_log_summary"] = (f"analysed {response} on {dataset}: {res['model']} model, R2 {res['fit']['r2'] if res['fit']['r2'] is None else round(res['fit']['r2'], 3)}"
                               f", leading terms {', '.join(top)}")
        return res
    return services._log("experiment_analyze", source, dataset, {"response": response, "model": model}, do)


def next_run(services, dataset: str, factors, response: str, objective: str = "explore", n: int = 1, model: str = "auto",
             forbidden: Optional[list] = None, candidates: Optional[list] = None, seed: int = 0, source: str = "ui") -> dict:
    def do():
        fs = _factors_from_data(services, dataset, factors)
        X, y, _ = _frame(services, dataset, fs, response)
        res = next_runs(X, y, fs, response, objective, n, model, forbidden, candidates, seed)
        res["dataset"] = dataset
        first = res["recommendations"][0]
        res["_log_summary"] = f"recommended {len(res['recommendations'])} next run(s) for {objective}: " + ", ".join(f"{k}={v:.4g}" for k, v in first["settings"].items())
        return res
    return services._log("experiment_next", source, dataset, {"response": response, "objective": objective, "n": n}, do)


def choose(services, hypotheses, tests, threshold: float = 0.95, source: str = "ui") -> dict:
    def do():
        res = choose_test(hypotheses, tests, threshold)
        res["_log_summary"] = f"chose test {res['recommendation']['test']!r} among {len(res['ranking'])}: {res['recommendation']['information_gain_bits']:.2f} bits"
        return res
    return services._log("experiment_choose_test", source, None, {"hypotheses": len(hypotheses) if isinstance(hypotheses, list) else None}, do)
