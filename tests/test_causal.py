"""Causal hypothesis, identification and refutation (Hangar F08): a known confounder makes the aggregate
comparison misleading; the graph names the adjustment set, refuses to adjust for a mediator, and says what is
missing when a confounder is not measured."""
import asyncio

import numpy as np
import pytest

from nightingale import causal
from nightingale import effects as ef
from nightingale.agent_tools import call_tool

GRAPH = "season -> discount, season -> sales, discount -> visits -> sales, discount -> sales"


def confounded(n=800, seed=3):
    rng = np.random.default_rng(seed)
    season = rng.normal(0, 1, n)
    discount = (rng.random(n) < 1 / (1 + np.exp(-1.0 * season))).astype(int)
    visits = 1.0 * discount + rng.normal(0, 1, n)
    sales = 10 - 5.0 * season + 1.0 * discount + 1.5 * visits + rng.normal(0, 1, n)   # total effect = 1 + 1.5 = 2.5
    return season, discount, visits, sales


def test_parse_and_d_separation():
    edges = causal.parse_graph("a -> b -> c; a -> c\nd -> c")
    assert edges == [("a", "b"), ("b", "c"), ("a", "c"), ("d", "c")]
    g = causal.Graph(edges)
    assert g.d_separated("a", "d", []) and not g.d_separated("a", "d", ["c"])     # c is a collider
    with pytest.raises(causal.GraphError):
        causal.Graph(causal.parse_graph("x -> y, y -> x"))
    with pytest.raises(causal.GraphError):
        causal.parse_graph("x - y")


def test_identify_adjustment_set_forbidden_and_missing():
    edges = causal.parse_graph(GRAPH)
    r = causal.identify(edges, "discount", "sales", {"season", "discount", "visits", "sales"})
    assert r["identified"] and r["adjustment_set"] == ["season"]
    assert [f["node"] for f in r["forbidden"]] == ["visits"] and r["forbidden"][0]["why"] == "mediator"
    bad = causal.identify(edges, "discount", "sales", {"season", "discount", "visits", "sales"}, listed=["season", "visits"])
    assert bad["listed_check"]["valid"] is False
    hidden = causal.identify(edges, "discount", "sales", {"discount", "visits", "sales"})
    assert hidden["identified"] is False and hidden["unobserved"] == ["season"]
    assert any("season" in m for m in hidden["missing"]) and len(hidden["missing"]) >= 2
    iv = causal.identify(causal.parse_graph("u -> t, u -> y, t -> y, z -> t"), "t", "y", {"t", "y", "z"})
    assert iv["identified"] is False and iv["instruments"] == ["z"]
    collider = causal.identify(causal.parse_graph("t -> y, t -> c, y -> c, a -> t, a -> y"), "t", "y", {"t", "y", "c", "a"}, listed=["a", "c"])
    assert collider["adjustment_set"] == ["a"] and collider["listed_check"]["valid"] is False


def test_known_confounder_aggregate_misleads_and_graph_design_recovers(client):
    season, discount, visits, sales = confounded()
    csv = "season,discount,visits,sales\n" + "\n".join(f"{a:.5f},{b},{c:.5f},{d:.5f}" for a, b, c, d in zip(season, discount, visits, sales))
    client.services.ingest_text(csv, "shop", "csv")
    r = client.post("/api/effect/estimate", json={"method": "adjust", "dataset": "shop", "outcome": "sales", "treatment": "discount",
                                                  "graph": GRAPH, "refute": True})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["effect"]["naive_estimate"] < 0                 # the aggregate says discounts lower sales
    assert b["effect"]["estimate"] == pytest.approx(2.5, abs=0.4)
    assert b["identification"]["adjustment_set"] == ["season"] and b["refutation"]["passed"] is True
    assert b["causal_claim"]["established"] is False and not b["causal_claim"]["assumptions_failed"]
    wrong = client.post("/api/effect/estimate", json={"method": "adjust", "dataset": "shop", "outcome": "sales", "treatment": "discount",
                                                      "graph": GRAPH, "confounders": ["season", "visits"]}).json()
    assert wrong["effect"]["estimate"] == pytest.approx(1.0, abs=0.3)           # adjusting for the mediator hides part of the effect
    assert wrong["causal_claim"]["assumptions_failed"]
    client.services.ingest_text("discount,visits,sales\n" + "\n".join(f"{b},{c:.5f},{d:.5f}" for b, c, d in zip(discount, visits, sales)), "shop2", "csv")
    hidden = client.post("/api/effect/estimate", json={"method": "adjust", "dataset": "shop2", "outcome": "sales", "treatment": "discount",
                                                       "graph": GRAPH}).json()
    assert hidden["identification"]["identified"] is False and any("season" in m for m in hidden["identification"]["missing"])
    assert hidden["causal_claim"]["assumptions_failed"]

    async def go():
        return await call_tool(client.services, "data_effect", {"method": "adjust", "dataset": "shop", "outcome": "sales",
                                                                 "treatment": "discount", "graph": GRAPH, "refute": True})
    out = asyncio.run(go())
    assert out["identification"]["identified"] and out["refutation"]["tests"][0]["passed"]


def test_refutation_flags_a_spurious_effect():
    rng = np.random.default_rng(5)
    n = 400
    t = rng.integers(0, 2, n).astype(float)
    y = rng.normal(0, 1, n)
    r = causal.refute(y, t, np.empty((n, 0)), 0.01)
    assert r["tests"][0]["share_as_large"] > 0.3 and r["tests"][0]["passed"] is False


def test_same_variable_as_treatment_and_outcome_is_refused():
    with pytest.raises(causal.GraphError):
        causal.identify(causal.parse_graph("a -> y"), "y", "y", {"a", "y"})


def test_notes_for_missing_path_and_case_mismatch():
    r = causal.identify(causal.parse_graph("y -> t"), "t", "y", {"t", "y"})
    assert r["no_path"] is True and r["notes"]
    r = causal.identify(causal.parse_graph("A -> t, A -> y, t -> y"), "t", "y", {"a", "t", "y"})
    assert r["identified"] is False and any("a" in n and "case" in n for n in r["notes"])


def test_refutation_does_not_fail_a_true_null_effect():
    rng = np.random.default_rng(9)
    n = 1500
    z = rng.normal(size=n)
    t = (z + rng.normal(size=n) > 0).astype(float)
    y = 2 * z + rng.normal(size=n)                        # the true effect of t is zero
    X = z[:, None]
    est = float(np.linalg.lstsq(np.column_stack([np.ones(n), t, z]), y, rcond=None)[0][1])
    r = causal.refute(y, t, X, est, se=0.06)  # roughly the standard error with n=1500
    assert r["passed"] is True and r["tests"][0].get("not_informative")
    assert causal.refute(y, t, X, est)["tests"][0]["passed"] in (True, False)        # without a se the old rule applies

