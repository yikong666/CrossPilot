"""Bounded pyvis rendering for graph evidence returned by the API."""

import html
from collections.abc import Mapping
from typing import Any, cast

import streamlit.components.v1 as components
from pyvis.network import Network  # type: ignore[import-untyped]

MAX_GRAPH_NODES = 30
MAX_GRAPH_EDGES = 50


def trim_graph(graph_data: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Keep a small connected slice suitable for an in-page evidence panel."""
    raw_nodes = graph_data.get("nodes")
    raw_edges = graph_data.get("edges")
    nodes = (
        [dict(node) for node in raw_nodes if isinstance(node, Mapping)]
        if isinstance(raw_nodes, list)
        else []
    )
    edges = (
        [dict(edge) for edge in raw_edges if isinstance(edge, Mapping)]
        if isinstance(raw_edges, list)
        else []
    )
    selected_nodes = [node for node in nodes if node.get("id") is not None][:MAX_GRAPH_NODES]
    node_ids = {str(node["id"]) for node in selected_nodes}
    selected_edges = [
        edge
        for edge in edges
        if str(_edge_source(edge)) in node_ids and str(_edge_target(edge)) in node_ids
    ][:MAX_GRAPH_EDGES]
    return {"nodes": selected_nodes, "edges": selected_edges}


def build_graph_html(graph_data: Mapping[str, Any]) -> str:
    """Generate a self-contained pyvis visualization for a trimmed graph slice."""
    graph = trim_graph(graph_data)
    network = Network(
        height="420px", width="100%", directed=True, bgcolor="#ffffff", font_color="#1f2937"
    )
    network.barnes_hut(gravity=-2500, central_gravity=0.25, spring_length=135)
    for node in graph["nodes"]:
        node_id = str(node["id"])
        label = str(node.get("label") or node.get("name") or node_id)
        node_type = str(node.get("type") or node.get("labels") or "证据节点")
        properties = node.get("properties")
        details = properties if isinstance(properties, Mapping) else {}
        title = "<br>".join(
            [f"<b>{html.escape(node_type)}</b>"]
            + [
                f"{html.escape(str(key))}: {html.escape(str(value))}"
                for key, value in details.items()
            ]
        )
        network.add_node(node_id, label=label, title=title, group=node_type)
    for edge in graph["edges"]:
        source = str(_edge_source(edge))
        target = str(_edge_target(edge))
        label = str(edge.get("label") or edge.get("type") or "关联")
        network.add_edge(source, target, label=label, title=html.escape(label))
    return cast(str, network.generate_html(notebook=False))


def render_graph_evidence(graph_data: Mapping[str, Any]) -> None:
    """Show graph evidence only on demand, without exposing internal trace data."""
    graph = trim_graph(graph_data)
    if not graph["nodes"]:
        return
    components.html(build_graph_html(graph), height=430, scrolling=True)


def _edge_source(edge: Mapping[str, Any]) -> Any:
    return edge.get("source", edge.get("from"))


def _edge_target(edge: Mapping[str, Any]) -> Any:
    return edge.get("target", edge.get("to"))
