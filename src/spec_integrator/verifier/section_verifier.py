from __future__ import annotations

from typing import TYPE_CHECKING

from spec_integrator.db import VerificationIssue

if TYPE_CHECKING:
    from spec_integrator.config import Config
    from spec_integrator.db import DocAuditDB


class SectionTopicVerifier:
    """Verifies that semantically linked sections across specifications

    share appropriate traceability keywords and do not contain duplicated content.
    """

    def __init__(self, config: Config):
        self.config = config

    def verify(self, db: DocAuditDB) -> list[VerificationIssue]:
        """Inspects section topic similarities and yields warnings for unlinked or duplicate sections."""
        st_config = getattr(self.config, "semantic_topic", None)
        if not st_config or not getattr(st_config, "enabled", True):
            return []

        min_sim = getattr(st_config, "similarity_threshold", 0.80)
        unlinked_threshold = getattr(st_config, "unlinked_warning_threshold", 0.82)
        duplicate_threshold = getattr(st_config, "duplicate_warning_threshold", 0.90)
        model = getattr(st_config, "embedding_model", "multilingual-e5-large")

        pairs = db.get_section_similarities(model=model, min_similarity=min_sim)
        if not pairs:
            return []

        keywords_map = db.get_section_keywords_map()
        issues: list[VerificationIssue] = []
        seen_pairs: set[tuple[str, str]] = set()

        for p in pairs:
            sec_a_id = p["section_a"]
            sec_b_id = p["section_b"]
            file_a = p["file_a"]
            file_b = p["file_b"]
            sim = p["similarity"]

            # Avoid symmetric duplicate warnings
            pair_key = (min(sec_a_id, sec_b_id), max(sec_a_id, sec_b_id))
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)

            info_a = db.get_section_info(sec_a_id)
            info_b = db.get_section_info(sec_b_id)
            if not info_a or not info_b:
                continue

            heading_a = info_a["heading"]
            line_a = info_a["line_start"]
            heading_b = info_b["heading"]

            kws_a = keywords_map.get(sec_a_id, set())
            kws_b = keywords_map.get(sec_b_id, set())
            common_kws = kws_a & kws_b

            # 1. Duplicate / Redundant content warning
            if sim >= duplicate_threshold:
                issues.append(
                    VerificationIssue(
                        gate="SemanticTopic",
                        severity="WARNING",
                        file_path=file_a,
                        line=line_a,
                        rule_code="SEM-TOPIC-DUPLICATE",
                        message=(
                            f"Section '{heading_a}' in '{file_a}' and '{heading_b}' in '{file_b}' "
                            f"exhibit potentially duplicated content (similarity: {sim:.2f}). "
                            "Consider consolidating or cross-referencing."
                        ),
                    )
                )

            # 2. Missing keyword linkage warning
            elif sim >= unlinked_threshold and not common_kws:
                issues.append(
                    VerificationIssue(
                        gate="SemanticTopic",
                        severity="WARNING",
                        file_path=file_a,
                        line=line_a,
                        rule_code="SEM-TOPIC-UNLINKED",
                        message=(
                            f"Section '{heading_a}' in '{file_a}' and '{heading_b}' in '{file_b}' "
                            f"discuss the same topic (similarity: {sim:.2f}) but share no common "
                            "traceability keyword (e.g. {Keyword}). Consider linking them."
                        ),
                    )
                )

        return issues
