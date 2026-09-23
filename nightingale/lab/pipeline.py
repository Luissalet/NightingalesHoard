"""Visual pipeline support for the UI's node editor: turn a dataset's recorded
step history (`services.recipe`) into a node/edge graph, and validate an
edited graph back into an ordered step list — the actual application still
goes through `workbench/steps.py`/`workbench/engine.py` (the one transform
engine the app has), never a second one.

Graph shape (JSON-friendly, matches what a node editor naturally produces):

    {
      "nodes": [
        {"id": "v0", "kind": "source", "params": {}},
        {"id": "step_1", "kind": "filter", "params": {"expr": "amount > 0"}},
        {"id": "dataset:orders", "kind": "dataset_ref", "dataset": "orders"},
        {"id": "step_2", "kind": "join", "params": {"other_dataset": "orders", "on": [...]}}
      ],
      "edges": [["v0", "step_1"], ["step_1", "step_2"], ["dataset:orders", "step_2"]]
    }

Exactly one node has `kind: "source"` (the dataset's raw ingest, v0 — its data
is never rebuilt from this graph, only steps after it). `dataset_ref` nodes
have no incoming edges and represent another dataset used by a `join`/`union`
step. Every other node is a single linear chain: at most one non-dataset_ref
predecessor, at most one successor.
"""

from __future__ import annotations

from typing import Any

from . import LabError
from ..workbench.steps import STEP_KINDS

__all__ = ["dataset_graph", "validate_graph"]


def dataset_graph(current_version: int, steps: list[dict]) -> dict:
    """Build the node/edge graph for a dataset's recorded steps (as returned
    by `services.recipe(dataset, "show")["steps"]`, filtered to
    `version <= current_version`)."""
    nodes = [{"id": "v0", "kind": "source", "params": {}, "version": 0}]
    edges: list[list[str]] = []
    prev_id = "v0"
    dataset_refs: dict[str, str] = {}
    for step in steps:
        v = step["version"]
        if v == 0:
            continue  # v0 is always the source/ingest step, already emitted
        node_id = f"step_{v}"
        params = step.get("params", {})
        nodes.append({"id": node_id, "kind": step["op"], "params": params, "version": v})
        other = params.get("other_dataset")
        if other and step["op"] in ("join", "union"):
            ref_id = f"dataset:{other}"
            if ref_id not in dataset_refs:
                nodes.append({"id": ref_id, "kind": "dataset_ref", "dataset": other})
                dataset_refs[ref_id] = other
            edges.append([ref_id, node_id])
        edges.append([prev_id, node_id])
        prev_id = node_id
    return {"nodes": nodes, "edges": edges, "tip": prev_id}


def validate_graph(graph: dict) -> list[dict]:
    """Validate an edited graph and return its ordered, applicable step list
    (excluding the source node): `[{"op": ..., "params": ...}, ...]`."""
    nodes = graph.get("nodes")
    edges = graph.get("edges")
    if not isinstance(nodes, list) or not nodes:
        raise LabError("graph.nodes must be a non-empty list")
    if not isinstance(edges, list):
        raise LabError("graph.edges must be a list")
    by_id = {}
    for n in nodes:
        if "id" not in n or "kind" not in n:
            raise LabError("every node needs an 'id' and a 'kind'")
        if n["id"] in by_id:
            raise LabError(f"duplicate node id: {n['id']}")
        by_id[n["id"]] = n

    sources = [n for n in nodes if n["kind"] == "source"]
    if len(sources) != 1:
        raise LabError(f"graph needs exactly one 'source' node, found {len(sources)}")
    source_id = sources[0]["id"]

    out_edges: dict[str, list[str]] = {n["id"]: [] for n in nodes}
    in_edges: dict[str, list[str]] = {n["id"]: [] for n in nodes}
    for e in edges:
        if not (isinstance(e, (list, tuple)) and len(e) == 2):
            raise LabError(f"malformed edge: {e!r}; expected [from_id, to_id]")
        a, b = e
        if a not in by_id or b not in by_id:
            raise LabError(f"edge references unknown node: {e!r}")
        out_edges[a].append(b)
        in_edges[b].append(a)

    for n in nodes:
        if n["kind"] == "dataset_ref":
            if in_edges[n["id"]]:
                raise LabError(f"dataset_ref node {n['id']!r} must have no incoming edges")
            if not n.get("dataset"):
                raise LabError(f"dataset_ref node {n['id']!r} needs a 'dataset' name")
            continue
        chain_predecessors = [p for p in in_edges[n["id"]] if by_id[p]["kind"] != "dataset_ref"]
        if n["kind"] == "source":
            if chain_predecessors:
                raise LabError("the source node must have no incoming chain edge")
        elif len(chain_predecessors) != 1:
            raise LabError(f"node {n['id']!r} must have exactly one incoming step/source edge "
                            f"(has {len(chain_predecessors)})")
        chain_successors = [s for s in out_edges[n["id"]] if by_id[s]["kind"] != "dataset_ref"]
        if len(chain_successors) > 1:
            raise LabError(f"node {n['id']!r} branches into {len(chain_successors)} steps; "
                            "the pipeline must stay a single linear chain")

    # walk the chain from source to its tip
    ordered = []
    current = source_id
    visited = {source_id}
    while True:
        successors = [s for s in out_edges[current] if by_id[s]["kind"] != "dataset_ref"]
        if not successors:
            break
        nxt = successors[0]
        if nxt in visited:
            raise LabError("graph contains a cycle")
        visited.add(nxt)
        node = by_id[nxt]
        if node["kind"] not in STEP_KINDS:
            raise LabError(f"unknown step kind: {node['kind']!r}; choose from {STEP_KINDS}")
        ordered.append({"op": node["kind"], "params": dict(node.get("params") or {})})
        current = nxt

    unreached = set(by_id) - visited - {n["id"] for n in nodes if n["kind"] == "dataset_ref"}
    if unreached:
        raise LabError(f"node(s) not reachable from the source: {sorted(unreached)}")
    return ordered
