from __future__ import annotations

from spec_integrator.anti_sabotage.base import AntiSabotageCheck, AntiSabotageContext
from spec_integrator.models import VerificationIssue


class HierarchyCheck(AntiSabotageCheck):
    """階層依存性検証: 上位 Tier から下位 Tier への具象直接参照（逆流依存）を検証する。"""

    rule_code = "HIERARCHY-REVERSE-DEPENDENCY"
    name = "階層依存の逆流・破綻"
    gate = "Hierarchy"
    severity = "ERROR"
    description = "上位 Tier から下位 Tier への具象直接リンクおよびキーワード直接参照を検出する。"

    def check(self, ctx: AntiSabotageContext) -> list[VerificationIssue]:
        issues: list[VerificationIssue] = []
        graph = ctx.graph
        if not graph:
            return issues

        for edge in graph.edges:
            src_node = graph.nodes.get(edge.source)
            tgt_node = graph.nodes.get(edge.target)
            if not src_node or not tgt_node:
                continue
            src_tier = src_node.tier
            if src_tier is None or src_tier == "meta":
                continue

            # Case A: Markdown link to lower tier
            if edge.relation == "links_to" and tgt_node.type in ("file", "section"):
                tgt_tier = tgt_node.tier
                if (
                    tgt_tier is not None
                    and tgt_tier != "meta"
                    and isinstance(src_tier, int)
                    and isinstance(tgt_tier, int)
                    and src_tier < tgt_tier
                ):
                    issues.append(
                        VerificationIssue(
                            gate="Hierarchy",
                            severity="ERROR",
                            file_path=src_node.file_path,
                            line=src_node.line,
                            rule_code="HIERARCHY-REVERSE-DEPENDENCY",
                            message=(
                                f"Encapsulation violation: Upper Tier {src_tier} directly links "
                                f"to Lower Tier {tgt_tier} ('{tgt_node.label}')."
                            ),
                        )
                    )

            # Case B: Keyword reference to lower tier local requirement
            elif edge.relation == "refers_to" and tgt_node.type == "item":
                kw_name = tgt_node.label.strip("{}")
                if kw_name.startswith("META_") or kw_name.startswith("GLOBAL_"):
                    continue
                def_tier = self._get_keyword_definition_tier(kw_name, ctx)
                if (
                    def_tier is not None
                    and def_tier != "meta"
                    and isinstance(src_tier, int)
                    and isinstance(def_tier, int)
                    and src_tier < def_tier
                ):
                    issues.append(
                        VerificationIssue(
                            gate="Hierarchy",
                            severity="ERROR",
                            file_path=src_node.file_path,
                            line=src_node.line,
                            rule_code="HIERARCHY-REVERSE-KEYWORD-REF",
                            message=(
                                f"Encapsulation violation: Upper Tier {src_tier} references "
                                f"Lower Tier {def_tier} keyword '{{{kw_name}}}'."
                            ),
                        )
                    )

        return issues

    def _get_keyword_definition_tier(
        self, keyword: str, ctx: AntiSabotageContext
    ) -> int | str | None:
        for doc in ctx.documents:
            if ctx.config.is_keyword_definition(keyword, doc.file_path):
                return doc.tier
        return None
