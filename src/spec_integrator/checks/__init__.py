from __future__ import annotations

from spec_integrator.checks.consistency.duplicate_definition import DuplicateDefinitionCheck
from spec_integrator.checks.consistency.stale_value import StaleValueCheck
from spec_integrator.checks.consistency.symbol_drift import SymbolDriftCheck
from spec_integrator.checks.evidence.benchmark_missing import BenchmarkScriptMissingCheck
from spec_integrator.checks.evidence.dangling_artifact_ref import DanglingArtifactRefCheck
from spec_integrator.checks.evidence.declared_file_missing import DeclaredEvidenceFileMissingCheck
from spec_integrator.checks.evidence.tag_undeclared import TagToEvidenceMismatchCheck
from spec_integrator.checks.formal.backing_ambiguous import FormalBackingAmbiguousCheck
from spec_integrator.checks.formal.contract_missing import FormalContractMissingCheck
from spec_integrator.checks.formal.model_missing import FormalModelMissingCheck
from spec_integrator.checks.formal.property_invalid import FormalPropertyInvalidCheck
from spec_integrator.checks.formal.soundness import FormalModelSoundnessCheck
from spec_integrator.checks.formal.vacuous import FormalPropertyVacuousCheck
from spec_integrator.checks.format.broken_anchor import BrokenAnchorCheck
from spec_integrator.checks.format.broken_link import BrokenLinkCheck
from spec_integrator.checks.format.file_link import FileLinkFormatCheck
from spec_integrator.checks.format.invalid_mermaid import MermaidSyntaxCheck
from spec_integrator.checks.format.typo import LevenshteinTypoCheck
from spec_integrator.checks.hierarchy.hierarchy import HierarchyCheck
from spec_integrator.checks.obligation.assessment_independence import AssessmentIndependenceCheck
from spec_integrator.checks.obligation.assessment_missing import AssessmentMissingCheck
from spec_integrator.checks.obligation.doc_judge_coverage import DocumentJudgeCoverageCheck
from spec_integrator.checks.obligation.judge_coverage import JudgeCoverageCheck
from spec_integrator.checks.obligation.verification_skipped import VerificationTagSkippedCheck
from spec_integrator.checks.readability.prose_readability import ProseReadabilityCheck
from spec_integrator.checks.traceability.traceability import TraceabilityCheck
from spec_integrator.gates.base import GateCheck

ALL_CHECKS: list[type[GateCheck]] = [
    BrokenLinkCheck,
    BrokenAnchorCheck,
    FileLinkFormatCheck,
    MermaidSyntaxCheck,
    LevenshteinTypoCheck,
    TraceabilityCheck,
    HierarchyCheck,
    ProseReadabilityCheck,
    FormalModelMissingCheck,
    FormalContractMissingCheck,
    FormalModelSoundnessCheck,
    FormalPropertyVacuousCheck,
    FormalPropertyInvalidCheck,
    FormalBackingAmbiguousCheck,
    DeclaredEvidenceFileMissingCheck,
    TagToEvidenceMismatchCheck,
    DanglingArtifactRefCheck,
    BenchmarkScriptMissingCheck,
    AssessmentMissingCheck,
    AssessmentIndependenceCheck,
    VerificationTagSkippedCheck,
    JudgeCoverageCheck,
    DocumentJudgeCoverageCheck,
    DuplicateDefinitionCheck,
    SymbolDriftCheck,
    StaleValueCheck,
]

__all__ = ["ALL_CHECKS", "GateCheck"]
