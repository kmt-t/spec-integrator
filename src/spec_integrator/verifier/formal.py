from __future__ import annotations

import sys
import io
import contextlib
import importlib.util
from pathlib import Path
from dataclasses import dataclass, field
from spec_integrator.config import Config
from spec_integrator.parser import ParsedDocument
from spec_integrator.verifier.static import VerificationIssue


@dataclass
class FormalModelResult:
    component: str
    model_file: str
    status: str  # "PASS", "FAIL", "ERROR", "NOT_FOUND"
    details: str = ""
    invariants: list[dict] = field(default_factory=list)


class FormalVerifier:
    def __init__(self, config: Config):
        self.config = config

    def verify_documents(self, documents: list[ParsedDocument], docs_root: Path) -> tuple[list[VerificationIssue], list[FormalModelResult]]:
        issues: list[VerificationIssue] = []
        results: list[FormalModelResult] = []

        formal_tag = self.config.formal_verification.tag
        model_dir_name = self.config.formal_verification.model_dir_name

        # Find documents that demand formal verification
        for doc in documents:
            if formal_tag in doc.all_tags:
                doc_dir = doc.full_path.parent
                formal_dir = doc_dir / model_dir_name

                # Look for formal models in formal/
                model_files = list(formal_dir.glob("*.py")) if formal_dir.exists() else []

                if not model_files:
                    issues.append(VerificationIssue(
                        gate="Formal",
                        severity="ERROR",
                        file_path=doc.file_path,
                        line=1,
                        rule_code="FORMAL-MODEL-NOT-FOUND",
                        message=f"Document specifies '{formal_tag}', but no formal model scripts found in '{formal_dir.relative_to(docs_root)}/'."
                    ))
                    results.append(FormalModelResult(
                        component=doc.component,
                        model_file=str(formal_dir.relative_to(docs_root)),
                        status="NOT_FOUND",
                        details="No formal model script (*.py) found."
                    ))
                    continue

                # Run each model script
                for model_file in model_files:
                    res = self._run_model_script(model_file, doc.component, docs_root)
                    results.append(res)
                    if res.status != "PASS":
                        issues.append(VerificationIssue(
                            gate="Formal",
                            severity="ERROR",
                            file_path=model_file.relative_to(docs_root).as_posix(),
                            line=1,
                            rule_code="FORMAL-VERIFICATION-FAILED",
                            message=f"Formal verification failed in model '{model_file.name}': {res.details}"
                        ))

        return issues, results

    def _run_model_script(self, script_path: Path, component: str, docs_root: Path) -> FormalModelResult:
        rel_path = script_path.relative_to(docs_root).as_posix()
        try:
            # Dynamic import and execution of verify() function
            spec = importlib.util.spec_from_file_location(f"formal_{script_path.stem}", str(script_path))
            if spec is None or spec.loader is None:
                return FormalModelResult(
                    component=component,
                    model_file=rel_path,
                    status="ERROR",
                    details=f"Could not load module spec from {script_path}"
                )

            module = importlib.util.module_from_spec(spec)
            
            # Capture stdout
            stdout_buf = io.StringIO()
            with contextlib.redirect_stdout(stdout_buf):
                spec.loader.exec_module(module)
                if hasattr(module, "verify"):
                    ret = module.verify()
                    is_pass = (ret == 0 or ret is None or (isinstance(ret, dict) and ret.get("status") == "PASS"))
                else:
                    is_pass = True

            output = stdout_buf.getvalue().strip()

            if is_pass:
                return FormalModelResult(
                    component=component,
                    model_file=rel_path,
                    status="PASS",
                    details=output or "Model checking succeeded."
                )
            else:
                return FormalModelResult(
                    component=component,
                    model_file=rel_path,
                    status="FAIL",
                    details=output or "Model checking failed (non-zero return code)."
                )

        except Exception as e:
            return FormalModelResult(
                component=component,
                model_file=rel_path,
                status="ERROR",
                details=f"Execution error: {e}"
            )
