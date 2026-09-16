"""Source verification result models."""

from dataclasses import dataclass, field


@dataclass
class SourceIssue:
    file_path: str
    line: int
    rule: str
    severity: str
    message: str
    group: str = ""


@dataclass
class SourceVerificationResult:
    group: str
    files_evaluated: int = 0
    issues: list[SourceIssue] = field(default_factory=list)

    @property
    def status(self) -> str:
        return "FAIL" if any(issue.severity == "ERROR" for issue in self.issues) else "PASS"


__all__ = ["SourceIssue", "SourceVerificationResult"]
