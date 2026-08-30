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
                nodes=sorted(g["nodes"]),
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
        # 1. Inspect Mermaid graphs/flowcharts (Fail-closed: all flowcharts are checked by default)
        mermaid_blocks = re.finditer(r"```mermaid\s*\n(.*?)```", doc.content, re.DOTALL)
        for mb in mermaid_blocks:
            content = mb.group(1)
            line_no = doc.content[: mb.start()].count("\n") + 1
            lower_content = content.lower()
            # Skip non-flowchart diagram types (FSM, sequence diagrams, class diagrams, etc.)
            non_comment_lines = [
                ln.strip()
                for ln in content.strip().splitlines()
                if ln.strip() and not ln.strip().startswith("%%")
            ]
            first_cmd = non_comment_lines[0].lower() if non_comment_lines else ""
            if any(
                first_cmd.startswith(t)
                for t in [
                    "statediagram",
                    "sequencediagram",
                    "classdiagram",
                    "erdiagram",
                    "gitgraph",
                ]
            ):
                continue

            # Explicit opt-out for internal control flows, algorithm pipelines, or local state loops
            if (
                "%% not-a-topology" in lower_content
                or "%% not_a_topology" in lower_content
                or "%% not-topology" in lower_content
            ):
                continue

            # Check all graph / flowchart blocks
            if first_cmd.startswith("graph") or first_cmd.startswith("flowchart"):
                edges, nodes = self._parse_mermaid_edges(content)
                if edges:
                    graphs.append(
                        {
                            "name": f"Mermaid Flowchart Topology (line {line_no})",
                            "nodes": nodes,
                            "edges": edges,
                            "line": line_no,
                        }
                    )

        # 2. Check for IPC Router Role Matrix / Routing table definitions
        if "ipc_router.md" in doc.file_path:
            matrix_edges, matrix_nodes = self._extract_role_matrix_edges(doc)
            if matrix_edges:
                graphs.append(
                    {
                        "name": "IPC Router Service Dependency Topology",
                        "nodes": matrix_nodes,
                        "edges": matrix_edges,
                        "line": 1,
                    }
                )

        return graphs

    def _parse_mermaid_edges(self, content: str) -> tuple[list[tuple[str, str]], set[str]]:
        edges: list[tuple[str, str]] = []
        nodes: set[str] = set()
        edge_pattern = re.compile(
            r"([a-zA-Z0-9_]+)(?:\[.*?\]|\(.*?\)|\{.*?\})?\s*(?:-->|->|-\.->|==>)\s*(?:\|.*?\|\s*)?([a-zA-Z0-9_]+)(?:\[.*?\]|\(.*?\)|\{.*?\})?"
        )
        for line in content.splitlines():
            line = line.strip()
            if (
                line.startswith("%%")
                or line.startswith("graph")
                or line.startswith("flowchart")
                or line.startswith("subgraph")
                or line.startswith("end")
            ):
                continue
            for match in edge_pattern.finditer(line):
                src, dst = match.group(1).strip(), match.group(2).strip()
                if src and dst and src != dst:
                    edges.append((src, dst))
                    nodes.add(src)
                    nodes.add(dst)

        return edges, nodes

    def _extract_role_matrix_edges(
        self, doc: ParsedDocument
    ) -> tuple[list[tuple[str, str]], set[str]]:
        """
        Dynamically extracts directed communication dependency edges from Role/Access Matrices
        defined in Markdown tables or embedded design concepts.
        """
        edges: list[tuple[str, str]] = []
        nodes: set[str] = set()
        lines = doc.content.splitlines()
        in_matrix_table = False
        target_roles: list[str] = []
        for line in lines:
            line_str = line.strip()
            if any(
                kw in line_str.lower()
                for kw in [
                    "role_matrix",
                    "access_matrix",
                    "communication_matrix",
                    "rbac_matrix",
                    "role-based access control",
                ]
            ) or any(
                kw in line_str
                for kw in [
                    "ロール間通信許可マトリクス",
                    "アクセス制御マトリクス",
                    "通信許可マトリクス",
                ]
            ):
                in_matrix_table = True
                continue

            if in_matrix_table:
                if line_str.startswith("|") and (
                    "target" in line_str.lower()
                    or "送信先" in line_str
                    or "receiver" in line_str.lower()
                ):
                    # Header row
                    cols = [c.strip() for c in line_str.split("|")[1:-1]]
                    if len(cols) > 1:
                        target_roles = [re.sub(r"[*`]", "", c).strip() for c in cols[1:]]
                    continue
                elif line_str.startswith("|---") or line_str.startswith("|:--"):
                    continue
                elif line_str.startswith("|") and target_roles:
                    cols = [c.strip() for c in line_str.split("|")[1:-1]]
                    if len(cols) >= 1 + len(target_roles):
                        sender_role = re.sub(r"[*`]", "", cols[0]).strip()
                        for idx, target_role in enumerate(target_roles):
                            cell_val = cols[idx + 1].strip().upper()
                            if (
                                any(
                                    allow_kw in cell_val
                                    for allow_kw in [
                                        "ALLOW",
                                        "許可",
                                        "TRUE",
                                        "YES",
                                        "1",
                                    ]
                                )
                                and cell_val != "DENY"
                            ):
                                if sender_role != target_role:
                                    edges.append((sender_role, target_role))
                                    nodes.add(sender_role)
                                    nodes.add(target_role)
                elif not line_str.startswith("|") and line_str != "":
                    # End of table
                    in_matrix_table = False
                    target_roles = []

        # 2. Parse Python Dictionary format:
        # ("CLIENT_APP", "CORE_SERVICE"): True
        py_matrix_pattern = re.compile(
            r'\(\s*["\']([A-Za-z0-9_]+)["\']\s*,\s*["\']([A-Za-z0-9_]+)["\']\s*\)\s*:\s*(True|False|1|0)'
        )
        for match in py_matrix_pattern.finditer(doc.content):
            src = match.group(1).strip()
            dst = match.group(2).strip()
            allowed = match.group(3).strip() in ["True", "1"]
            if allowed and src != dst:
                edge = (src, dst)
                if edge not in edges:
                    edges.append(edge)
                nodes.add(src)
                nodes.add(dst)

        return edges, nodes

    def _detect_cycles(
        self, nodes: set[str] | list[str], edges: list[tuple[str, str]]
    ) -> tuple[bool, list[list[str]]]:
        """Tarjan / DFS cycle detection for directed graphs."""
        adj: dict[str, list[str]] = {n: [] for n in nodes}
        for u, v in edges:
            if u in adj:
                adj[u].append(v)
            else:
                adj[u] = [v]
            if v not in adj:
                adj[v] = []

        visited: dict[str, int] = dict.fromkeys(adj, 0)  # 0: unvisited, 1: visiting, 2: visited
        cycles: list[list[str]] = []
        path: list[str] = []

        def dfs(u: str):
            visited[u] = 1
            path.append(u)
            for v in adj.get(u, []):
                if visited.get(v, 0) == 1:
                    cycle_start_idx = path.index(v)
                    cycles.append([*path[cycle_start_idx:], v])
                elif visited.get(v, 0) == 0:
                    dfs(v)

            path.pop()
            visited[u] = 2

        for node in list(adj.keys()):
            if visited[node] == 0:
                dfs(node)

        return (len(cycles) > 0, cycles)
