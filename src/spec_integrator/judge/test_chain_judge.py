from __future__ import annotations

from pathlib import Path

from spec_integrator.config import Config
from spec_integrator.judge.base import BaseJudge
from spec_integrator.models import (
    TestChainReport,
    TestChainResult,
    TestChainTarget,
)

TEST_CHAIN_PROMPT_TEMPLATE = """You are a strict, formal Software Verification and Specification Auditor.
Your mission is to perform an exhaustive, end-to-end consistency audit across a 3-tier traceability chain:
1. DESIGN SPECIFICATION (Design Specs: Architecture, invariants, layouts, interfaces, error handling)
2. TEST SPECIFICATION (Test Specs: Test cases, preconditions, steps, expected results, requirement mappings)
3. TEST IMPLEMENTATION CODE (Test Code: Actual runnable test functions, assertions, mocks, harnesses)

Target Component / Module: {component_name}

=== 1. DESIGN SPECIFICATION ===
{design_spec_text}

=== 2. TEST SPECIFICATION ===
{test_spec_text}

=== 3. TEST IMPLEMENTATION CODE ===
{test_code_text}

=== EVALUATION CRITERIA ===
Perform your audit systematically against the following 4 core criteria:

1. Design Spec -> Test Spec Completeness (Specification Coverage):
   - Check whether all critical invariants, calling conventions, data structures, state machines, error handling policies, and safety constraints specified in the DESIGN SPECIFICATION are formally covered by test cases in the TEST SPECIFICATION.
   - Flag any crucial design requirement that lacks a corresponding test case as an ERROR or WARNING (Missing Test Specification).

2. Test Spec -> Test Code Fidelity (Implementation Faithfulness):
   - Check whether the test cases defined in the TEST SPECIFICATION are genuinely implemented in the TEST IMPLEMENTATION CODE.
   - Detect whether test code trivializes expected checks (e.g., using simplified mocks instead of testing actual invariants, missing boundary conditions, asserting trivial tautologies, or skipping failure paths required by the test spec).
   - Flag any test case in the test spec that is absent or diluted in the test code as an ERROR or WARNING (Unimplemented / Diluted Test Case).

3. Test Code -> Design Spec Semantic Consistency (No Hidden Divergence):
   - Verify that the actual constants, data layouts, calling signatures, boundary constraints, and execution models checked in the TEST CODE directly conform to the DESIGN SPECIFICATION.
   - Detect any contradictions where the test code enforces an outdated, conflicting, or non-compliant behavior not sanctioned by the design spec.

4. Clean Abstraction Separation:
   - Ensure the Design Specification and Test Specification maintain pure architectural/formal descriptions without leaking transient prototype implementation details.

=== AUDITOR RULES ===
- Literal & Rigorous Evaluation: Judge what the texts and code actually state and execute.
- Specific Citations: When reporting issues, always cite the specific section names in the design spec, test case IDs / table rows in the test spec, and function names / line numbers in the test code.
- No False Positives: If the 3-tier chain is consistent, well-covered, and faithfully verified, state so concisely as PASS.

=== OUTPUT FORMAT ===
Respond ONLY with a valid JSON object in English in the following format:
```json
{{
  "status": "PASS" | "WARN" | "FAIL",
  "summary": "Concise explanation of the 3-tier chain evaluation in English",
  "issues": [
    {{
      "severity": "ERROR" | "WARNING",
      "layer": "DESIGN_TO_TESTSPEC" | "TESTSPEC_TO_CODE" | "CODE_TO_DESIGN",
      "location": "Section / TestCaseID / FunctionName",
      "description": "Detailed explanation of gap, mismatch, or contradiction in English"
    }}
  ]
}}
```
"""


class TestChainJudge(BaseJudge):
    __test__ = False
    """Evaluates 3-tier end-to-end consistency between Design Spec -> Test Spec -> Test Code
    using LLM as a Judge.
    """

    def __init__(self, config: Config):
        super().__init__(config)

    def auto_discover_targets(self, root_dir: Path | None = None) -> list[TestChainTarget]:
        """Automatically pairs design specifications with test specifications and test implementation files."""
        root = root_dir or Path(self.config.project.docs_root)
        project_root = root.parent if root.name == "docs" else root
        targets: list[TestChainTarget] = []
        design_files = list(root.glob("components/**/*.md"))

        for df in sorted(design_files):
            if "tests" in df.parts or df.name.endswith("_test_spec.md") or df.name == "FORMAT.md":
                continue
            comp_stem = df.stem

            # Locate matching test specification
            test_spec_file = None
            for p in [df.parent / "tests", df.parent.parent / "tests"]:
                if p.exists():
                    cand = p / f"{comp_stem}_test_spec.md"
                    if cand.exists():
                        test_spec_file = cand
                        break
                    generic_cands = list(p.glob(f"*{comp_stem}*.md"))
                    if generic_cands:
                        test_spec_file = generic_cands[0]
                        break
            if test_spec_file is None or not test_spec_file.exists():
                global_cands = list(root.glob(f"components/**/tests/*{comp_stem}*.md"))
                if global_cands:
                    test_spec_file = global_cands[0]
                else:
                    continue

            # Locate relevant test implementation code
            test_code_files: list[Path] = []
            test_dirs = self.config.test_chain.test_dirs
            words = [w for w in comp_stem.split("_") if len(w) >= 3]
            search_roots = [project_root] if project_root != root else [root.parent, root]
            for s_root in search_roots:
                for t_dir_pat in test_dirs:
                    for cand_dir in s_root.glob(t_dir_pat):
                        if cand_dir.is_dir():
                            test_code_files.extend(list(cand_dir.glob(f"test*{comp_stem}*.py")))
                            test_code_files.extend(list(cand_dir.glob(f"*{comp_stem}*test*.py")))
                            test_code_files.extend(list(cand_dir.glob(f"*{comp_stem}*.py")))
                            for w in words:
                                test_code_files.extend(list(cand_dir.glob(f"test*{w}*.py")))
                                test_code_files.extend(list(cand_dir.glob(f"*{w}*test*.py")))

            # Deduplicate test code paths
            unique_code = []
            seen = set()
            for tf in test_code_files:
                if tf.resolve() not in seen and tf.exists() and tf.is_file():
                    seen.add(tf.resolve())
                    unique_code.append(tf)

            targets.append(
                TestChainTarget(
                    component_name=comp_stem,
                    design_doc_path=df,
                    test_spec_path=test_spec_file,
                    test_code_paths=unique_code,
                )
            )
        return targets

    def judge_targets(
        self,
        targets: list[TestChainTarget],
        backend: str | None = None,
        model: str | None = None,
        max_targets: int = 10,
    ) -> TestChainReport:
        report = TestChainReport()
        selected_backend = backend or self.config.llm_judge.default_backend
        candidates = targets[:max_targets] if max_targets > 0 else targets
        print(
            f"Auditing {len(candidates)} component 3-tier test chain(s) using LLM Backend: '{selected_backend}'..."
        )
        for idx, target in enumerate(candidates, start=1):
            print(
                f"  [{idx}/{len(candidates)}] Auditing '{target.component_name}' (Spec -> TestSpec -> TestCode)...",
                flush=True,
            )
            res = self._evaluate_single_chain(target, selected_backend, model)
            report.results.append(res)
            report.total_evaluated += 1
            if res.status == "PASS":
                report.pass_count += 1
            elif res.status == "WARN":
                report.warn_count += 1
            else:
                report.fail_count += 1
        return report

    def _evaluate_single_chain(
        self, target: TestChainTarget, backend: str, model: str | None
    ) -> TestChainResult:
        try:
            design_text = target.design_doc_path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return TestChainResult(
                component_name=target.component_name,
                design_doc=str(target.design_doc_path),
                test_spec=str(target.test_spec_path),
                test_code_files=[str(p) for p in target.test_code_paths],
                status="FAIL",
                summary=f"Failed to read design doc: {e}",
            )

        try:
            test_spec_text = target.test_spec_path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return TestChainResult(
                component_name=target.component_name,
                design_doc=str(target.design_doc_path),
                test_spec=str(target.test_spec_path),
                test_code_files=[str(p) for p in target.test_code_paths],
                status="FAIL",
                summary=f"Failed to read test spec: {e}",
            )

        test_code_chunks = []
        for code_path in target.test_code_paths:
            try:
                code_text = code_path.read_text(encoding="utf-8", errors="replace")
                lines = code_text.splitlines()
                if len(lines) > 300:
                    code_text = (
                        "\n".join(lines[:300]) + f"\n... [truncated {len(lines) - 300} lines]"
                    )
                test_code_chunks.append(f"--- File: {code_path.name} ---\n{code_text}")
            except Exception:
                pass
        test_code_text = (
            "\n\n".join(test_code_chunks)
            if test_code_chunks
            else "(No test code implementation files found)"
        )
        prompt = TEST_CHAIN_PROMPT_TEMPLATE.format(
            component_name=target.component_name,
            design_spec_text=design_text[:8000],
            test_spec_text=test_spec_text[:8000],
            test_code_text=test_code_text[:8000],
        )

        if backend == "mock":
            return TestChainResult(
                component_name=target.component_name,
                design_doc=str(target.design_doc_path),
                test_spec=str(target.test_spec_path),
                test_code_files=[str(p) for p in target.test_code_paths],
                status="PASS",
                summary=f"[MOCK] 3-tier chain (Design -> TestSpec -> TestCode) for '{target.component_name}' is fully verified and consistent.",
            )

        if backend not in ("sakura", "openrouter", "ollama"):
            return TestChainResult(
                component_name=target.component_name,
                design_doc=str(target.design_doc_path),
                test_spec=str(target.test_spec_path),
                test_code_files=[str(p) for p in target.test_code_paths],
                status="SKIPPED",
                summary=f"Unknown backend '{backend}'.",
            )

        try:
            if backend == "sakura":
                raw_response = self._call_sakura(prompt, model)
            elif backend == "openrouter":
                raw_response = self._call_openrouter(prompt, model)
            else:
                raw_response = self._call_ollama(prompt, model)
            parsed = self._parse_json_response(raw_response)
            return TestChainResult(
                component_name=target.component_name,
                design_doc=str(target.design_doc_path),
                test_spec=str(target.test_spec_path),
                test_code_files=[str(p) for p in target.test_code_paths],
                status=parsed.get("status", "WARN"),
                summary=parsed.get("summary", "No summary provided by LLM."),
                issues=parsed.get("issues", []),
            )
        except Exception as e:
            return TestChainResult(
                component_name=target.component_name,
                design_doc=str(target.design_doc_path),
                test_spec=str(target.test_spec_path),
                test_code_files=[str(p) for p in target.test_code_paths],
                status="FAIL",
                summary=f"LLM Judge execution error: {e}",
                issues=[
                    {
                        "severity": "ERROR",
                        "location": "LLM Backend",
                        "description": str(e),
                    }
                ],
            )

    def _parse_json_response(self, raw_resp: str) -> dict:
        """Never raises: an unparsable response is a WARN result, not a crash."""
        try:
            return self._extract_json(raw_resp)
        except (ValueError, LookupError):
            return {
                "status": "WARN",
                "summary": f"Could not parse valid JSON from LLM response: {raw_resp[:150]}...",
                "issues": [],
            }


__all__ = ["TestChainJudge", "TestChainReport", "TestChainResult", "TestChainTarget"]
