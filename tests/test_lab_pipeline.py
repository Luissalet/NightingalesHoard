"""Visual pipeline graph: building a node/edge graph from a recorded recipe,
and validating an edited graph back into an ordered, applicable step list —
reusing `workbench/steps.py`, never a second transform engine."""

import pytest

from nightingale.lab import LabError, pipeline


def test_dataset_graph_from_linear_recipe():
    steps = [
        {"version": 0, "op": "ingest", "params": {}},
        {"version": 1, "op": "filter", "params": {"expr": "amount > 0"}},
        {"version": 2, "op": "sort", "params": {"by": [{"column": "amount"}]}},
    ]
    graph = pipeline.dataset_graph(2, steps)
    ids = [n["id"] for n in graph["nodes"]]
    assert ids == ["v0", "step_1", "step_2"]
    assert graph["edges"] == [["v0", "step_1"], ["step_1", "step_2"]]
    assert graph["tip"] == "step_2"


def test_dataset_graph_adds_dataset_ref_node_for_join():
    steps = [
        {"version": 0, "op": "ingest", "params": {}},
        {"version": 1, "op": "join", "params": {"other_dataset": "orders", "on": [{"left": "id", "right": "id"}]}},
    ]
    graph = pipeline.dataset_graph(1, steps)
    ref_nodes = [n for n in graph["nodes"] if n["kind"] == "dataset_ref"]
    assert len(ref_nodes) == 1
    assert ref_nodes[0]["dataset"] == "orders"
    assert ["dataset:orders", "step_1"] in graph["edges"]


def test_validate_graph_round_trips_a_linear_chain():
    steps = [
        {"version": 0, "op": "ingest", "params": {}},
        {"version": 1, "op": "filter", "params": {"expr": "amount > 0"}},
        {"version": 2, "op": "sort", "params": {"by": [{"column": "amount"}]}},
    ]
    graph = pipeline.dataset_graph(2, steps)
    ordered = pipeline.validate_graph(graph)
    assert ordered == [{"op": "filter", "params": {"expr": "amount > 0"}},
                        {"op": "sort", "params": {"by": [{"column": "amount"}]}}]


def test_validate_graph_edited_params_are_honored():
    graph = {
        "nodes": [{"id": "v0", "kind": "source", "params": {}},
                  {"id": "step_1", "kind": "filter", "params": {"expr": "amount > 100"}}],
        "edges": [["v0", "step_1"]],
    }
    ordered = pipeline.validate_graph(graph)
    assert ordered == [{"op": "filter", "params": {"expr": "amount > 100"}}]


def test_validate_graph_reordering_steps_changes_the_chain():
    graph = {
        "nodes": [{"id": "v0", "kind": "source", "params": {}},
                  {"id": "s2", "kind": "sort", "params": {"by": [{"column": "x"}]}},
                  {"id": "s1", "kind": "filter", "params": {"expr": "x > 0"}}],
        "edges": [["v0", "s1"], ["s1", "s2"]],  # filter first, sort second
    }
    ordered = pipeline.validate_graph(graph)
    assert [s["op"] for s in ordered] == ["filter", "sort"]


def test_validate_graph_requires_exactly_one_source():
    graph = {"nodes": [{"id": "a", "kind": "filter", "params": {}}], "edges": []}
    with pytest.raises(LabError):
        pipeline.validate_graph(graph)


def test_validate_graph_rejects_branching():
    graph = {
        "nodes": [{"id": "v0", "kind": "source", "params": {}},
                  {"id": "s1", "kind": "filter", "params": {"expr": "x>0"}},
                  {"id": "s2", "kind": "sort", "params": {"by": ["x"]}}],
        "edges": [["v0", "s1"], ["v0", "s2"]],  # v0 branches into two steps
    }
    with pytest.raises(LabError):
        pipeline.validate_graph(graph)


def test_validate_graph_rejects_cycle():
    graph = {
        "nodes": [{"id": "v0", "kind": "source", "params": {}},
                  {"id": "s1", "kind": "filter", "params": {}},
                  {"id": "s2", "kind": "sort", "params": {}}],
        "edges": [["v0", "s1"], ["s1", "s2"], ["s2", "s1"]],
    }
    with pytest.raises(LabError):
        pipeline.validate_graph(graph)


def test_validate_graph_unknown_step_kind_raises():
    graph = {
        "nodes": [{"id": "v0", "kind": "source", "params": {}},
                  {"id": "s1", "kind": "not_a_real_step", "params": {}}],
        "edges": [["v0", "s1"]],
    }
    with pytest.raises(LabError):
        pipeline.validate_graph(graph)


def test_validate_graph_dataset_ref_with_incoming_edge_raises():
    graph = {
        "nodes": [{"id": "v0", "kind": "source", "params": {}},
                  {"id": "s1", "kind": "filter", "params": {}},
                  {"id": "dataset:orders", "kind": "dataset_ref", "dataset": "orders"}],
        "edges": [["v0", "s1"], ["s1", "dataset:orders"]],
    }
    with pytest.raises(LabError):
        pipeline.validate_graph(graph)


def test_validate_graph_unreachable_node_raises():
    graph = {
        "nodes": [{"id": "v0", "kind": "source", "params": {}},
                  {"id": "s1", "kind": "filter", "params": {}},
                  {"id": "orphan", "kind": "sort", "params": {}}],
        "edges": [["v0", "s1"]],
    }
    with pytest.raises(LabError):
        pipeline.validate_graph(graph)
