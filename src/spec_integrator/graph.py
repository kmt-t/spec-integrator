from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

from spec_integrator.config import Config
from spec_integrator.parser import ParsedDocument


@dataclass
class Node:
    id: str
    label: str
    type: str  # 'file', 'section', 'item'
    file_path: str
    line: int = 0
    tier: str | int | None = None
    metadata: dict = field(default_factory=dict)


@dataclass
class Edge:
    source: str
    target: str
    relation: str  # 'contains', 'defines', 'refers_to', 'links_to'
    metadata: dict = field(default_factory=dict)


@dataclass
class Graph:
    nodes: dict[str, Node] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)

    def add_node(self, node: Node):
        if node.id not in self.nodes:
            self.nodes[node.id] = node

    def add_edge(self, edge: Edge):
        # Prevent duplicate edges
        for e in self.edges:
            if e.source == edge.source and e.target == edge.target and e.relation == edge.relation:
                return
        self.edges.append(edge)

    def extract_item_subgraphs(self) -> list[dict]:
        """Extracts subgraphs centered around Item/Keyword nodes for LLM Judge or analysis."""
        subgraphs = []
        item_nodes = [n for n in self.nodes.values() if n.type == "item"]
        for item in item_nodes:
            def_sources = [
                e.source for e in self.edges if e.target == item.id and e.relation == "defines"
            ]
            ref_sources = [
                e.source for e in self.edges if e.target == item.id and e.relation == "refers_to"
            ]
            subgraphs.append(
                {
                    "item_id": item.id,
                    "item_label": item.label,
                    "defined_in": def_sources,
                    "referenced_in": ref_sources,
                    "total_nodes": 1 + len(def_sources) + len(ref_sources),
                }
            )
        return sorted(subgraphs, key=lambda x: len(x["referenced_in"]), reverse=True)

    def to_dict(self) -> dict:
        return {
            "nodes": [asdict(n) for n in self.nodes.values()],
            "edges": [asdict(e) for e in self.edges],
        }

    def to_mermaid(self, max_nodes: int = 150) -> str:
        lines = ["graph TD"]
        lines.append(
            "    classDef fileNode fill:#2d3748,stroke:#4a5568,color:#fff,stroke-width:2px;"
        )
        lines.append("    classDef sectionNode fill:#2b6cb0,stroke:#3182ce,color:#fff;")
        lines.append(
            "    classDef itemNode fill:#d69e2e,stroke:#b7791f,color:#fff,stroke-width:2px;"
        )
        safe_id_map = {}
        for idx, nid in enumerate(self.nodes.keys()):
            safe_id_map[nid] = f"N{idx}"

        displayed_nodes = list(self.nodes.items())[:max_nodes]
        for nid, node in displayed_nodes:
            s_id = safe_id_map[nid]
            escaped_label = node.label.replace('"', '\\"').replace("[", "(").replace("]", ")")
            if node.type == "file":
                lines.append(f'    {s_id}["[Doc] {escaped_label}"]:::fileNode')
            elif node.type == "section":
                lines.append(f'    {s_id}["[Sec] {escaped_label}"]:::sectionNode')
            elif node.type == "item":
                lines.append(f'    {s_id}["[Item] {escaped_label}"]:::itemNode')

        relation_styles = {
            "contains": "-->",
            "defines": "==>",
            "refers_to": "-.->",
            "links_to": "-->",
        }
        for edge in self.edges:
            if edge.source in safe_id_map and edge.target in safe_id_map:
                if edge.source in dict(displayed_nodes) and edge.target in dict(displayed_nodes):
                    s_src = safe_id_map[edge.source]
                    s_tgt = safe_id_map[edge.target]
                    arrow = relation_styles.get(edge.relation, "-->")
                    label = (
                        f"|{edge.relation}|" if edge.relation not in ("contains", "defines") else ""
                    )
                    lines.append(f"    {s_src} {arrow}{label} {s_tgt}")
        return "\n".join(lines)


class DocGraphBuilder:
    def __init__(self, config: Config):
        self.config = config

    def build(self, documents: list[ParsedDocument], docs_root: Path) -> Graph:
        graph = Graph()
        file_map: dict[str, str] = {}  # rel_path -> file_node_id
        # 1. Add file nodes
        for doc in documents:
            file_node_id = f"file:{doc.file_path}"
            file_map[doc.file_path] = file_node_id
            graph.add_node(
                Node(
                    id=file_node_id,
                    label=doc.file_path,
                    type="file",
                    file_path=doc.file_path,
                    tier=doc.tier,
                )
            )

        # 2. Add section nodes and keyword definitions
        for doc in documents:
            file_node_id = f"file:{doc.file_path}"
            section_stack = [(0, file_node_id)]
            for sec in doc.sections:
                sec_node_id = sec.section_id
                graph.add_node(
                    Node(
                        id=sec_node_id,
                        label=sec.heading or "(Root)",
                        type="section",
                        file_path=doc.file_path,
                        line=sec.line_start,
                        tier=doc.tier,
                    )
                )
                # Section hierarchy (contains edge)
                while section_stack and section_stack[-1][0] >= sec.level:
                    section_stack.pop()

                parent_id = section_stack[-1][1] if section_stack else file_node_id
                graph.add_edge(Edge(source=parent_id, target=sec_node_id, relation="contains"))
                section_stack.append((sec.level, sec_node_id))
                # Check if this document/section is the definition source for any keyword
                for kw in sec.keywords:
                    item_id = f"item:{kw}"
                    is_definition = self.config.is_keyword_definition(kw, doc.file_path)
                    graph.add_node(
                        Node(
                            id=item_id,
                            label=f"{{{kw}}}",
                            type="item",
                            file_path=doc.file_path,
                            line=sec.line_start,
                        )
                    )
                    if is_definition:
                        graph.add_edge(Edge(source=sec_node_id, target=item_id, relation="defines"))
                    else:
                        graph.add_edge(
                            Edge(source=sec_node_id, target=item_id, relation="refers_to")
                        )

        # 3. Resolve Markdown links
        for doc in documents:
            for link in doc.all_links:
                source_sec_id = self._find_section_for_line(doc, link.source_line)
                # Resolve target file
                if not link.target_path:
                    # Same file anchor link
                    target_file = doc.file_path
                else:
                    # Relative path resolution
                    src_dir = Path(doc.file_path).parent
                    resolved_target = (src_dir / link.target_path).as_posix()
                    import os

                    target_file = os.path.normpath(resolved_target).replace("\\", "/")

                target_file_id = f"file:{target_file}"
                target_node_id = target_file_id
                if link.target_anchor:
                    target_sec_id = f"sec:{target_file}#{link.target_anchor}"
                    if target_sec_id in graph.nodes:
                        target_node_id = target_sec_id

                if target_file_id in graph.nodes:
                    graph.add_edge(
                        Edge(
                            source=source_sec_id,
                            target=target_node_id,
                            relation="links_to",
                            metadata={"line": link.source_line, "text": link.text},
                        )
                    )
        return graph

    def _is_keyword_definition(self, keyword: str, file_path: str) -> bool:
        # Check rule mapping in config
        for _k_type, rule in self.config.keywords.items():
            import re

            if re.match(rule.pattern, keyword):
                if rule.is_definition_file(file_path):
                    return True
        return False

    def _find_section_for_line(self, doc: ParsedDocument, line_num: int) -> str:
        for sec in doc.sections:
            if sec.line_start <= line_num <= sec.line_end:
                return sec.section_id
        return f"file:{doc.file_path}"
