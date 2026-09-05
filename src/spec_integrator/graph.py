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
class DocumentIsland:
    """A connected component (island) of documents and sections mutually linked or sharing keywords."""

    island_id: str
    name: str
    file_paths: list[str]
    section_ids: list[str]
    keywords: list[str]
    total_sections: int = 0
    total_docs: int = 0


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

    def extract_document_islands(
        self, min_size: int = 1, max_cluster_docs: int = 6
    ) -> list[DocumentIsland]:
        """Extracts connected clusters (islands) of mutually referencing documents and sections.

        Uses bidirectional links ('links_to') and shared keyword bindings ('defines', 'refers_to')
        to identify tightly coupled clusters.
        """
        # 1. Build adjacency list between file paths
        doc_files = [n.file_path for n in self.nodes.values() if n.type == "file" and n.file_path]
        file_adj: dict[str, set[str]] = {f: set() for f in doc_files}
        file_keywords: dict[str, set[str]] = {f: set() for f in doc_files}
        file_sections: dict[str, list[str]] = {f: [] for f in doc_files}

        # Index sections by file
        for n in self.nodes.values():
            if n.type == "section" and n.file_path in file_sections:
                file_sections[n.file_path].append(n.id)

        # Keyword to defining/referring files
        kw_files: dict[str, set[str]] = {}
        for e in self.edges:
            if e.relation in ("defines", "refers_to"):
                kw = e.target.removeprefix("item:")
                src_node = self.nodes.get(e.source)
                if src_node and src_node.file_path:
                    kw_files.setdefault(kw, set()).add(src_node.file_path)
                    if src_node.file_path in file_keywords:
                        file_keywords[src_node.file_path].add(kw)

            elif e.relation == "links_to":
                src_node = self.nodes.get(e.source)
                tgt_node = self.nodes.get(e.target)
                if (
                    src_node
                    and tgt_node
                    and src_node.file_path
                    and tgt_node.file_path
                    and src_node.file_path != tgt_node.file_path
                ):
                    if src_node.file_path in file_adj and tgt_node.file_path in file_adj:
                        file_adj[src_node.file_path].add(tgt_node.file_path)
                        file_adj[tgt_node.file_path].add(src_node.file_path)

        # Direct links ('links_to') are strong architectural connections
        # Keywords are only used to cluster if the keyword connects 2..max_cluster_docs files
        # Exclude ubiquitous top-level keywords defined in meta documents
        meta_docs = {
            "architecture/document_structure.md",
            "architecture/keyword_dictionary.md",
            "requires/requirement_list.md",
        }
        for _kw, files in kw_files.items():
            non_meta = {f for f in files if f not in meta_docs}
            if 2 <= len(non_meta) <= max_cluster_docs:
                flist = list(non_meta)
                for i in range(len(flist)):
                    for j in range(i + 1, len(flist)):
                        f1, f2 = flist[i], flist[j]
                        if f1 in file_adj and f2 in file_adj:
                            file_adj[f1].add(f2)
                            file_adj[f2].add(f1)

        # 2. Find connected components (BFS/DFS)
        visited: set[str] = set()
        islands: list[DocumentIsland] = []
        island_counter = 1

        # Sort files prioritizing component documents first, then others
        sorted_files = sorted(
            doc_files,
            key=lambda f: (
                0 if f.startswith("components/") else (1 if f.startswith("specs/") else 2),
                f,
            ),
        )

        for f in sorted_files:
            if f in visited:
                continue

            # Traverse cluster up to max_cluster_docs
            cluster: list[str] = []
            queue = [f]
            visited.add(f)

            while queue:
                curr = queue.pop(0)
                cluster.append(curr)
                if len(cluster) >= max_cluster_docs:
                    break

                # Prioritize neighbors in the same component directory
                curr_dir = Path(curr).parent
                neighbors = sorted(
                    file_adj.get(curr, set()),
                    key=lambda n: (0 if Path(n).parent == curr_dir else 1, n),
                )
                for neighbor in neighbors:
                    if neighbor not in visited and len(cluster) + len(queue) < max_cluster_docs:
                        visited.add(neighbor)
                        queue.append(neighbor)

            if len(cluster) < min_size:
                continue

            cluster_sorted = sorted(cluster)
            cluster_sections: list[str] = []
            cluster_kws: set[str] = set()

            for doc_path in cluster_sorted:
                cluster_sections.extend(file_sections.get(doc_path, []))
                cluster_kws.update(file_keywords.get(doc_path, set()))

            # Derive a meaningful island name
            base_names = [Path(p).stem for p in cluster_sorted]
            name = (
                base_names[0]
                if len(base_names) == 1
                else f"{base_names[0]} + {len(base_names) - 1} related"
            )

            islands.append(
                DocumentIsland(
                    island_id=f"island_{island_counter:02d}",
                    name=name,
                    file_paths=cluster_sorted,
                    section_ids=cluster_sections,
                    keywords=sorted(cluster_kws),
                    total_sections=len(cluster_sections),
                    total_docs=len(cluster_sorted),
                )
            )
            island_counter += 1

        return sorted(islands, key=lambda isl: isl.total_docs, reverse=True)

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
                    import os
                    norm_root = os.path.normpath(link.target_path).replace("\\", "/")
                    if f"file:{norm_root}" in graph.nodes:
                        target_file = norm_root
                    else:
                        src_dir = Path(doc.file_path).parent
                        resolved_target = (src_dir / link.target_path).as_posix()
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
