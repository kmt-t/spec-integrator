"""Document-side application pipeline."""

from dataclasses import dataclass
from pathlib import Path

from spec_integrator.config import Config
from spec_integrator.document.gates import (
    ConsistencyVerifier,
    EvidenceVerifier,
    FormalVerifier,
    ObligationVerifier,
    SectionTopicVerifier,
    StaticVerifier,
    WITVerifier,
)
from spec_integrator.document.workspace import DocumentWorkspace, load_workspace
from spec_integrator.models import (
    ConsistencySummary,
    FormalModelResult,
    ObligationSummary,
    ParsedDocument,
    VerificationIssue,
    WITFileResult,
)
from spec_integrator.reporter import Reporter
from spec_integrator.terminology import TermExtractor, TermVarianceJudge


@dataclass
class DocumentCheckResult:
    issues: list[VerificationIssue]
    formal_results: list[FormalModelResult]
    wit_results: list[WITFileResult]
    obligation_summary: ObligationSummary
    consistency_summary: ConsistencySummary

    @property
    def errors(self) -> list[VerificationIssue]:
        return [issue for issue in self.issues if issue.severity == "ERROR"]


class DocumentPipeline:
    """Coordinates document parsing, quality gates, persistence, and reporting."""

    def __init__(self, config: Config):
        self.config = config

    def load_workspace(
        self,
        clean: bool = False,
        file_paths: list[str | Path] | None = None,
    ) -> DocumentWorkspace:
        return load_workspace(self.config, clean=clean, file_paths=file_paths)

    def check(
        self,
        workspace: DocumentWorkspace,
        report_path: Path,
    ) -> DocumentCheckResult:
        issues = StaticVerifier(self.config).verify(
            workspace.documents, workspace.graph, workspace.docs_root
        )
        formal_issues, formal_results = FormalVerifier(self.config).verify_documents(
            workspace.documents, workspace.docs_root
        )
        issues.extend(formal_issues)
        for result in formal_results:
            workspace.db.insert_formal_model(
                result.component,
                result.model_file,
                "pymodelchecking",
                result.status,
                result.details,
            )
        wit_issues, wit_results = WITVerifier(self.config).verify_documents(
            workspace.documents, workspace.docs_root
        )
        issues.extend(wit_issues)
        for result in wit_results:
            workspace.db.insert_wit_file(
                result.component,
                result.wit_file,
                result.status,
                result.details,
                result.defined_interfaces,
                result.defined_worlds,
            )
        issues.extend(
            EvidenceVerifier(self.config).verify(
                workspace.documents,
                workspace.docs_root,
                formal_results,
                wit_results,
            )
        )
        obligation_issues, obligation_summary = ObligationVerifier(self.config).verify(
            workspace.documents, workspace.graph, workspace.db
        )
        issues.extend(obligation_issues)
        consistency_issues, consistency_summary = ConsistencyVerifier(self.config).verify(
            workspace.documents, workspace.docs_root, db=workspace.db
        )
        issues.extend(consistency_issues)

        if getattr(self.config.terminology, "enabled", True):
            issues.extend(
                TermVarianceJudge(self.config).generate_verification_issues(
                    workspace.db,
                    min_confidence=self.config.terminology.confidence_threshold,
                )
            )
        if getattr(self.config.semantic_topic, "enabled", True):
            issues.extend(SectionTopicVerifier(self.config).verify(workspace.db))

        workspace.db.replace_verification_issues(issues)
        workspace.db.commit()
        Reporter(self.config).generate_markdown_report(
            workspace.documents,
            workspace.graph,
            issues,
            formal_results,
            wit_results,
            report_path,
            obligation_summary=obligation_summary,
            consistency_summary=consistency_summary,
            db=workspace.db,
        )
        return DocumentCheckResult(
            issues,
            formal_results,
            wit_results,
            obligation_summary,
            consistency_summary,
        )

    def build_terms(self, workspace: DocumentWorkspace) -> int:
        """Extract and persist document terminology for the build command."""
        return TermExtractor(self.config).extract_and_save(
            workspace.documents, workspace.db
        )

    def format(
        self,
        file_paths: list[str | Path] | None = None,
    ) -> tuple[int, int]:
        """Normalize Markdown whitespace and return changed and examined counts."""
        docs_root = self.config.get_docs_dir()
        if file_paths:
            target_files = [Path(path).resolve() for path in file_paths if Path(path).is_file()]
        else:
            target_files = [
                path
                for path in sorted(docs_root.rglob("*.md"))
                if not self.config.is_excluded(path, docs_root)
            ]

        changed = 0
        for path in target_files:
            content = path.read_text(encoding="utf-8")
            lines = [line.rstrip() for line in content.splitlines()]
            normalized = "\n".join(lines) + "\n" if lines else ""
            if normalized != content:
                path.write_text(normalized, encoding="utf-8")
                changed += 1
        return changed, len(target_files)
