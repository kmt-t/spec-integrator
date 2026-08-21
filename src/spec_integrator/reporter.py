from __future__ import annotations

import json
from pathlib import Path
from spec_integrator.config import Config
from spec_integrator.parser import ParsedDocument
from spec_integrator.graph import Graph
from spec_integrator.verifier.static import VerificationIssue
from spec_integrator.verifier.formal import FormalModelResult
from spec_integrator.verifier.wit import WITFileResult


class Reporter:
    def __init__(self, config: Config):
        self.config = config

    def generate_markdown_report(self, documents: list[ParsedDocument], graph: Graph,
                                 issues: list[VerificationIssue],
                                 formal_results: list[FormalModelResult],
                                 wit_results: list[WITFileResult],
                                 out_path: Path,
                                 obligation_summary=None,
                                 consistency_summary=None) -> str:
        lines = []

        total_docs = len(documents)
        total_sections = sum(len(d.sections) for d in documents)
        total_keywords = len([n for n in graph.nodes.values() if n.type == "item"])
        # Count *distinct* model scripts. A single model shared by several documents
        # must never be reported as several proofs.
        distinct_models = {r.model_file for r in formal_results}
        passing_models = {r.model_file for r in formal_results if r.status == "PASS"}
        total_wits = len(wit_results)

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
        lines.append(f"| Formal Models (distinct scripts) | {len(distinct_models)} |")
        lines.append(f"| Formal Models Passing Audit | {len(passing_models)} |")
        lines.append(f"| WIT Interface Files | {total_wits} |")
        if obligation_summary is not None:
            lines.append(f"| Verification Obligations Demanded | {obligation_summary.demanded} |")
            lines.append(f"| Verification Obligations Discharged | {obligation_summary.discharged} |")
        lines.append(f"| Errors | **{len(errors)}** |")
        lines.append(f"| Warnings | {len(warnings)} |\n")

        # 3. Gate Status Table
        gate_names = ["Format", "Traceability", "Hierarchy", "Formal", "WIT",
                      "Evidence", "Obligation", "Consistency"]
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
            lines.append("✨ No issues detected. All specification rules, hierarchy boundaries, formal models, and WIT interfaces are valid.\n")
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
            lines.append("| Component | Model Script | Backs | Status | Details |")
            lines.append("| :--- | :--- | :--- | :--- | :--- |")
            for r in formal_results:
                st = "🟢 PASS" if r.status == "PASS" else f"🔴 {r.status}"
                backs = "<br>".join(f"`{b}`" for b in r.backing_documents) or "*(none)*"
                lines.append(f"| `{r.component}` | `{r.model_file}` | {backs} | {st} | {r.details} |")
            lines.append("")

            audited = [r for r in formal_results if r.properties]
            if audited:
                lines.append("### 3.1 Property-level Audit\n")
                lines.append("| Model | Property | Kind | Result | Detail |")
                lines.append("| :--- | :--- | :--- | :--- | :--- |")
                for r in audited:
                    for p in r.properties:
                        icon = {"PASS": "🟢", "VACUOUS": "🟠", "FAIL": "🔴"}.get(p.status, "🔴")
                        lines.append(f"| `{r.model_file}` | {p.name} | {p.kind} | "
                                     f"{icon} {p.status} | {p.details} |")
                lines.append("")

        # 5b. Verification Obligations
        if obligation_summary is not None:
            lines.append("## 3.5 Verification Obligations (from Risk Assessment)\n")
            pct = int(round(obligation_summary.coverage * 100))
            lines.append(f"- Demanded: **{obligation_summary.demanded}** / "
                         f"Discharged: **{obligation_summary.discharged}** ({pct}%)")
            if obligation_summary.stale_documents:
                lines.append(f"- ⚠️ Stale assessments: {len(obligation_summary.stale_documents)} document(s)")
            if obligation_summary.unassessed_documents:
                lines.append(f"- ⚠️ Never assessed: {len(obligation_summary.unassessed_documents)} document(s)")
            lines.append("")
            if obligation_summary.skipped:
                lines.append("| File | Section | Risk | Missing Verification |")
                lines.append("| :--- | :--- | :---: | :--- |")
                for s in obligation_summary.skipped:
                    tags = ", ".join(f"`{t}`" for t in s["missing_tags"])
                    lines.append(f"| `{s['file_path']}` | {s['heading']} | "
                                 f"{s['risk_score']}/5 | {tags} |")
                lines.append("")

        # 5c. Consistency / propagation
        if consistency_summary is not None:
            lines.append("## 3.6 Change Propagation (Consistency)\n")
            lines.append(f"- Symbols tracked: **{consistency_summary.symbols_tracked}** / "
                         f"drifting: **{len(consistency_summary.drifting_symbols)}**")
            lines.append(f"- Co-change edges tracked: **{consistency_summary.cochange_tracked}** / "
                         f"stale: **{len(consistency_summary.cochange_stale)}**")
            if not consistency_summary.baseline_present:
                lines.append("- ⚠️ No baseline recorded — run `spec-integrator sync`.")
            lines.append("")

            if consistency_summary.drifting_symbols:
                lines.append("### 3.6.1 Symbols With Conflicting Values\n")
                lines.append("| Symbol | Value | Occurrences |")
                lines.append("| :--- | :--- | :--- |")
                for d in consistency_summary.drifting_symbols:
                    for value, locs in sorted(d.values.items()):
                        shown = ", ".join(f"`{l}`" for l in sorted(set(locs))[:4])
                        lines.append(f"| `{d.symbol}` | **{value}** | {shown} |")
                lines.append("")

            if consistency_summary.cochange_stale:
                lines.append("### 3.6.2 References Left Behind by an Edit\n")
                lines.append("| Keyword | Changed Definition | Not Updated |")
                lines.append("| :--- | :--- | :--- |")
                for s in consistency_summary.cochange_stale:
                    definer = s["definer"].replace("sec:", "")
                    lines.append(f"| `{{{s['keyword']}}}` | `{definer}` | "
                                 f"`{s['file_path']}` — {s['heading']} |")
                lines.append("")

        # 6. WIT Interface Verification Details
        if wit_results:
            lines.append("## 4. WIT Interface Verification Results\n")
            lines.append("| Component | WIT File | Interfaces / Worlds | Status | Details |")
            lines.append("| :--- | :--- | :--- | :--- | :--- |")
            for w in wit_results:
                st = "🟢 PASS" if w.status == "PASS" else f"🔴 {w.status}"
                ifaces = ", ".join(w.defined_interfaces) or "*(None)*"
                wrlds = ", ".join(w.defined_worlds) or ""
                summary = ifaces if not wrlds else f"{ifaces} (Worlds: {wrlds})"
                lines.append(f"| `{w.component}` | `{w.wit_file}` | `{summary}` | {st} | {w.details} |")
            lines.append("")

        # 7. Traceability Matrix
        lines.append("## 5. Traceability Matrix\n")
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

        # 8. DocGraph Mermaid Diagram
        lines.append("## 6. DocGraph Topology (Mermaid)\n")
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
