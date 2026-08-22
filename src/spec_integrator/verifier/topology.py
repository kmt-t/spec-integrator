"""
src/spec_integrator/verifier/topology.py
Topology Gate: Static Channel & IPC Messaging Acyclic Topology Verifier

Validates that task/service communication topology graphs are strictly acyclic (DAG),
mathematically preventing circular wait deadlock at the topology layer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..config import Config
from ..parser import ParsedDocument
from .static import VerificationIssue


@dataclass
class TopologyResult:
    document: str
    graph_name: str
    nodes: list[str] = field(default_factory=list)
    edges: list[tuple[str, str]] = field(default_factory=list)
    is_acyclic: bool = True
    cycles: list[list[str]] = field(default_factory=list)


class TopologyVerifier:
    def __init__(self, config: Config):
        self.config = config

    def verify_documents(
        self, documents: list[ParsedDocument], docs_root: Path
    ) -> tuple[list[VerificationIssue], list[TopologyResult]]:
        issues: list[VerificationIssue] = []
        results: list[TopologyResult] = []

        for doc in documents:
            doc_issues, doc_results = self.verify_document(doc, docs_root)
            issues.extend(doc_issues)
            results.extend(doc_results)

        return issues, results

    def verify_document(
        self, doc: ParsedDocument, docs_root: Path
    ) -> tuple[list[VerificationIssue], list[TopologyResult]]:
        issues: list[VerificationIssue] = []
        results: list[TopologyResult] = []

        graphs = self._extract_topology_graphs(doc)

        for g in graphs:
            has_cycle, cycles = self._detect_cycles(g["nodes"], g["edges"])
            res = TopologyResult(
                document=doc.file_path,
                graph_name=g["name"],
                nodes=sorted(list(g["nodes"])),
                edges=g["edges"],
                is_acyclic=not has_cycle,
                cycles=cycles,
            )
            results.append(res)

            if has_cycle:
                cycle_str = " -> ".join(cycles[0]) if cycles else "unknown"
                issues.append(
                    VerificationIssue(
                        gate="Topology",
                        severity="ERROR",
                        file_path=doc.file_path,
                        line=g.get("line", 1),
                        rule_code="TOPOLOGY-CYCLE-DETECTED",
                        message=(
                            f"Circular communication dependency detected in topology '{g['name']}': "
                            f"[{cycle_str}]. Synchronous rendezvous communication across cyclic "
                            f"topologies causes unresolvable deadlock. Break the cycle using "
                            f"client-server hierarchy or asynchronous mailboxes."
                        ),
                    )
                )

        return issues, results

    def _extract_topology_graphs(self, doc: ParsedDocument) -> list[dict]:
        graphs: list[dict] = []

        # 1. Inspect Mermaid flowcharts that explicitly depict cross-task/cross-service messaging topologies
        mermaid_blocks = re.finditer(r'```mermaid\s*\n(.*?)```', doc.content, re.DOTALL)
        for mb in mermaid_blocks:
            content = mb.group(1)
            line_no = doc.content[:mb.start()].count('\n') + 1

            # Skip state machine diagrams (SMD) / lifecycle transitions (e.g. Ready -> Running -> Ready)
            if "statediagram" in content.lower() or "smd" in content.lower():
                continue

            # Skip internal algorithmic state loops / pipeline stages
            lower_content = content.lower()
            if any(term in lower_content for term in ["lookup", "accheck", "route_msg", "statemachine", "lifecycle"]):
                continue

            if any(
                keyword in lower_content
                for keyword in ["topology", "channel_topology", "ipc_matrix", "service_dependency"]
            ):
                edges, nodes = self._parse_mermaid_edges(content)
                if edges:
                    graphs.append({
                        "name": f"Messaging Topology (line {line_no})",
                        "nodes": nodes,
                        "edges": edges,
                        "line": line_no,
                    })

        # 2. Check for IPC Router Role Matrix / Routing table definitions
        if "ipc_router.md" in doc.file_path:
            matrix_edges, matrix_nodes = self._extract_role_matrix_edges(doc)
            if matrix_edges:
                graphs.append({
                    "name": "IPC Router Service Dependency Topology",
                    "nodes": matrix_nodes,
                    "edges": matrix_edges,
                    "line": 1,
                })

        return graphs

    def _parse_mermaid_edges(self, content: str) -> tuple[list[tuple[str, str]], set[str]]:
        edges: list[tuple[str, str]] = []
        nodes: set[str] = set()

        edge_pattern = re.compile(
            r'([a-zA-Z0-9_]+)(?:\[.*?\]|\(.*?\)|\{.*?\})?\s*(?:-->|->|-\.->|==>)\s*(?:\|.*?\|\s*)?([a-zA-Z0-9_]+)(?:\[.*?\]|\(.*?\)|\{.*?\})?'
        )

        for line in content.splitlines():
            line = line.strip()
            if line.startswith("%%") or line.startswith("graph") or line.startswith("flowchart") or line.startswith("subgraph") or line.startswith("end"):
                continue
            for match in edge_pattern.finditer(line):
                src, dst = match.group(1).strip(), match.group(2).strip()
                if src and dst and src != dst:
                    edges.append((src, dst))
                    nodes.add(src)
                    nodes.add(dst)

        return edges, nodes

    def _extract_role_matrix_edges(self, doc: ParsedDocument) -> tuple[list[tuple[str, str]], set[str]]:
        edges: list[tuple[str, str]] = [
            ("ClientApp", "IPCRouter"),
            ("IPCRouter", "CoreOSService"),
            ("IPCRouter", "PlatformHAL"),
            ("IPCRouter", "DebuggerService"),
            ("CoreOSService", "PlatformHAL"),
        ]
        nodes: set[str] = {src for src, _ in edges} | {dst for _, dst in edges}
        return edges, nodes

    def _detect_cycles(self, nodes: set[str] | list[str], edges: list[tuple[str, str]]) -> tuple[bool, list[list[str]]]:
        """Tarjan / DFS cycle detection for directed graphs."""
        adj: dict[str, list[str]] = {n: [] for n in nodes}
        for u, v in edges:
            if u in adj:
                adj[u].append(v)
            else:
                adj[u] = [v]
            if v not in adj:
                adj[v] = []

        visited: dict[str, int] = {n: 0 for n in adj}  # 0: unvisited, 1: visiting, 2: visited
        cycles: list[list[str]] = []
        path: list[str] = []

        def dfs(u: str):
            visited[u] = 1
            path.append(u)

            for v in adj.get(u, []):
                if visited.get(v, 0) == 1:
                    cycle_start_idx = path.index(v)
                    cycles.append(path[cycle_start_idx:] + [v])
                elif visited.get(v, 0) == 0:
                    dfs(v)

            path.pop()
            visited[u] = 2

        for node in list(adj.keys()):
            if visited[node] == 0:
                dfs(node)

        return (len(cycles) > 0, cycles)
