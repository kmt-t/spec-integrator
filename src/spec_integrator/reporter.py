from __future__ import annotations

import json
from pathlib import Path
from spec_integrator.config import Config
from spec_integrator.parser import ParsedDocument
from spec_integrator.graph import Graph
from spec_integrator.verifier.static import VerificationIssue
from spec_integrator.verifier.formal import FormalModelResult


class Reporter:
    def __init__(self, config: Config):
        self.config = config

    def generate_markdown_report(self, documents: list[ParsedDocument], graph: Graph,
                                 issues: list[VerificationIssue],
                                 formal_results: list[FormalModelResult],
                                 out_path: Path) -> str:
        lines = []

        total_docs = len(documents)
        total_sections = sum(len(d.sections) for d in documents)
        total_keywords = len([n for n in graph.nodes.values() if n.type == "item"])
        total_models = len(formal_results)
        
        errors = [i for i in issues if i.severity == "ERROR"]
        warnings = [i for i in issues if i.severity == "WARNING"]
        is_passed = (len(errors) == 0)

        # 1. Header & Overall Status
        lines.append(f"# Spec Verification Report: {self.config.project.name}\n")
        status_badge = "✅ **ALL GATES PASSED**" if is_passed else "❌ **VERIFICATION FAILED**"
        lines.append(f"**Overall Status**: {status_badge}\n")

        # 2. Executive Summary Table
        lines.append("## 1. Executive Summary\n")
        lines.append("| Metric | Value |")
        lines.append("| :--- | :--- |")
        lines.append(f"| Total Documents | {total_docs} |")
        lines.append(f"| Total Sections | {total_sections} |")
        lines.append(f"| Total Keywords / Entities | {total_keywords} |")
        lines.append(f"| Formal Verification Models | {total_models} |")
        lines.append(f"| Errors | **{len(errors)}** |")
        lines.append(f"| Warnings | {len(warnings)} |\n")

        # 3. Gate Status Table
        gate_names = ["Format", "Traceability", "Hierarchy", "Formal"]
        lines.append("### Quality Gate Status\n")
        lines.append("| Gate | Status | Issues |")
        lines.append("| :--- | :--- | :--- |")
        for g in gate_names:
            g_issues = [i for i in issues if i.gate == g and i.severity == "ERROR"]
            status_str = "🟢 PASS" if len(g_issues) == 0 else f"🔴 FAIL ({len(g_issues)} errors)"
            lines.append(f"| **{g} Gate** | {status_str} | {len(g_issues)} |")
        lines.append("")

        # 4. Violations / Issues Section
        lines.append("## 2. Issues & Violations\n")
        if not issues:
            lines.append("✨ No issues detected. All specification rules, hierarchy boundaries, and formal models are valid.\n")
        else:
            lines.append("| Severity | Gate | Location | Rule | Message |")
            lines.append("| :--- | :--- | :--- | :--- | :--- |")
            for issue in issues:
                loc = f"`{issue.file_path}:{issue.line}`"
                lines.append(f"| **{issue.severity}** | {issue.gate} | {loc} | `{issue.rule_code}` | {issue.message} |")
            lines.append("")

        # 5. Formal Verification Details
        if formal_results:
            lines.append("## 3. Formal Verification Results (pyModelChecking)\n")
            lines.append("| Component | Model Script | Status | Details |")
            lines.append("| :--- | :--- | :--- | :--- |")
            for r in formal_results:
                st = "🟢 PASS" if r.status == "PASS" else f"🔴 {r.status}"
                lines.append(f"| `{r.component}` | `{r.model_file}` | {st} | {r.details} |")
            lines.append("")

        # 6. Traceability Matrix
        lines.append("## 4. Traceability Matrix\n")
        subgraphs = graph.extract_item_subgraphs()
        lines.append("| Item / Requirement | Defined In | Referenced In (Design Specs) | Status |")
        lines.append("| :--- | :--- | :--- | :--- |")
        for sg in subgraphs:
            item_lbl = f"`{sg['item_label']}`"
            def_str = "<br>".join(f"`{d.replace('sec:', '')}`" for d in sg["defined_in"]) or "*(None)*"
            ref_str = "<br>".join(f"`{r.replace('sec:', '')}`" for r in sg["referenced_in"]) or "*(Unreferenced)*"
            status = "🟢 Satisfied" if (sg["defined_in"] and sg["referenced_in"]) else ("🔴 Unreferenced" if not sg["referenced_in"] else "🔴 Undefined")
            lines.append(f"| {item_lbl} | {def_str} | {ref_str} | {status} |")
        lines.append("")

        # 7. DocGraph Mermaid Diagram
        lines.append("## 5. DocGraph Topology (Mermaid)\n")
        lines.append("```mermaid")
        lines.append(graph.to_mermaid(max_nodes=120))
        lines.append("```\n")

        report_content = "\n".join(lines)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(report_content, encoding="utf-8")
        return report_content

    def export_graph_json(self, graph: Graph, out_path: Path):
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(graph.to_dict(), f, indent=2, ensure_ascii=False)
