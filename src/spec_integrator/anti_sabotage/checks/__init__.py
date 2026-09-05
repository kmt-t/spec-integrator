from __future__ import annotations

from spec_integrator.anti_sabotage.base import AntiSabotageCheck
from spec_integrator.anti_sabotage.checks.consist_duplicate_definition import (
    DuplicateDefinitionCheck,
)
from spec_integrator.anti_sabotage.checks.consist_stale_value import StaleValueCheck
from spec_integrator.anti_sabotage.checks.consist_symbol_drift import SymbolDriftCheck
from spec_integrator.anti_sabotage.checks.evid_benchmark_missing import (
    BenchmarkScriptMissingCheck,
)
from spec_integrator.anti_sabotage.checks.evid_dangling_artifact_ref import (
    DanglingArtifactRefCheck,
)
from spec_integrator.anti_sabotage.checks.evid_declared_file_missing import (
    DeclaredEvidenceFileMissingCheck,
)
from spec_integrator.anti_sabotage.checks.evid_tag_undeclared import (
    TagToEvidenceMismatchCheck,
)
from spec_integrator.anti_sabotage.checks.fmt_broken_anchor import BrokenAnchorCheck
from spec_integrator.anti_sabotage.checks.fmt_broken_link import BrokenLinkCheck
from spec_integrator.anti_sabotage.checks.fmt_file_link import FileLinkFormatCheck
from spec_integrator.anti_sabotage.checks.fmt_hierarchy import HierarchyCheck
from spec_integrator.anti_sabotage.checks.fmt_invalid_mermaid import MermaidSyntaxCheck
from spec_integrator.anti_sabotage.checks.fmt_traceability import TraceabilityCheck
from spec_integrator.anti_sabotage.checks.fmt_typo import LevenshteinTypoCheck
from spec_integrator.anti_sabotage.checks.formal_backing_ambiguous import (
    FormalBackingAmbiguousCheck,
)
from spec_integrator.anti_sabotage.checks.formal_contract_missing import (
    FormalContractMissingCheck,
)
from spec_integrator.anti_sabotage.checks.formal_model_missing import (
    FormalModelMissingCheck,
)
from spec_integrator.anti_sabotage.checks.formal_property_invalid import (
    FormalPropertyInvalidCheck,
)
from spec_integrator.anti_sabotage.checks.formal_soundness import (
    FormalModelSoundnessCheck,
)
from spec_integrator.anti_sabotage.checks.formal_vacuous import (
    FormalPropertyVacuousCheck,
)
from spec_integrator.anti_sabotage.checks.oblig_assessment_independence import (
    AssessmentIndependenceCheck,
)
from spec_integrator.anti_sabotage.checks.oblig_assessment_missing import (
    AssessmentMissingCheck,
)
from spec_integrator.anti_sabotage.checks.oblig_doc_judge_coverage import (
    DocumentJudgeCoverageCheck,
)
from spec_integrator.anti_sabotage.checks.oblig_judge_coverage import JudgeCoverageCheck
from spec_integrator.anti_sabotage.checks.oblig_verification_skipped import (
    VerificationTagSkippedCheck,
)

ALL_CHECKS: list[type[AntiSabotageCheck]] = [
    # Format
    BrokenLinkCheck,
    BrokenAnchorCheck,
    FileLinkFormatCheck,
    MermaidSyntaxCheck,
    TraceabilityCheck,
    HierarchyCheck,
    LevenshteinTypoCheck,
    # Formal
    FormalModelMissingCheck,
    FormalContractMissingCheck,
    FormalModelSoundnessCheck,
    FormalPropertyVacuousCheck,
    FormalPropertyInvalidCheck,
    FormalBackingAmbiguousCheck,
    # Evidence
    DeclaredEvidenceFileMissingCheck,
    TagToEvidenceMismatchCheck,
    BenchmarkScriptMissingCheck,
    DanglingArtifactRefCheck,
    # Obligation
    AssessmentMissingCheck,
    AssessmentIndependenceCheck,
    VerificationTagSkippedCheck,
    JudgeCoverageCheck,
    DocumentJudgeCoverageCheck,
    # Consistency
    DuplicateDefinitionCheck,
    SymbolDriftCheck,
    StaleValueCheck,
]

__all__ = [
    "ALL_CHECKS",
    "AssessmentIndependenceCheck",
    "AssessmentMissingCheck",
    "BenchmarkScriptMissingCheck",
    "BrokenAnchorCheck",
    "BrokenLinkCheck",
    "DanglingArtifactRefCheck",
    "DeclaredEvidenceFileMissingCheck",
    "DocumentJudgeCoverageCheck",
    "DuplicateDefinitionCheck",
    "FileLinkFormatCheck",
    "FormalBackingAmbiguousCheck",
    "FormalContractMissingCheck",
    "FormalModelMissingCheck",
    "FormalModelSoundnessCheck",
    "FormalPropertyInvalidCheck",
    "FormalPropertyVacuousCheck",
    "HierarchyCheck",
    "JudgeCoverageCheck",
    "LevenshteinTypoCheck",
    "MermaidSyntaxCheck",
    "StaleValueCheck",
    "SymbolDriftCheck",
    "TagToEvidenceMismatchCheck",
    "TraceabilityCheck",
    "VerificationTagSkippedCheck",
]
