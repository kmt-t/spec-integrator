from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from spec_integrator.config import Config
from spec_integrator.judge.semantic_judge import LLMJudge

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


@dataclass
class TestChainTarget:
    __test__ = False
    component_name: str
    design_doc_path: Path
    test_spec_path: Path
    test_code_paths: list[Path]


@dataclass
class TestChainResult:
    __test__ = False
    component_name: str
    design_doc: str
    test_spec: str
    test_code_files: list[str]
    status: str  # "PASS", "WARN", "FAIL", "SKIPPED"
    summary: str
    issues: list[dict] = field(default_factory=list)


@dataclass
class TestChainReport:
    __test__ = False
    results: list[TestChainResult] = field(default_factory=list)
    total_evaluated: int = 0
    pass_count: int = 0
    warn_count: int = 0
    fail_count: int = 0

    def to_markdown(self, project_name: str = "System Specification") -> str:
        lines = [
            f"# {project_name} 設計仕様→テスト仕様→テストコード 一貫性監査レポート (LLM as a Judge)",
            "",
            f"- **監査コンポーネント総数**: {self.total_evaluated}",
            f"- **合格 (PASS)**: {self.pass_count}",
            f"- **警告 (WARN)**: {self.warn_count}",
            f"- **不合格 (FAIL)**: {self.fail_count}",
            "",
            "---",
            "",
            "## 1. 検出された不一致・網羅性課題 (Issues Found)",
            "",
        ]
        issues_found = False
        for r in self.results:
            if r.status in ("WARN", "FAIL") or r.issues:
                issues_found = True
                badge = "🔴 FAIL" if r.status == "FAIL" else "🟡 WARN"
                lines.append(f"### {badge}: `{r.component_name}`")
                lines.append(f"- **設計仕様書**: `{r.design_doc}`")
                lines.append(f"- **テスト仕様書**: `{r.test_spec}`")
                lines.append(
                    f"- **テストコード**: {', '.join(f'`{f}`' for f in r.test_code_files) if r.test_code_files else 'なし'}"
                )
                lines.append(f"- **評価サマリー**: {r.summary}")
                if r.issues:
                    lines.append("- **検出項目**:")
                    for iss in r.issues:
                        sev = iss.get("severity", "WARNING")
                        layer = iss.get("layer", "")
                        loc = iss.get("location", "Unknown")
                        desc = iss.get("description", "")
                        lines.append(f"  - **[{sev}] [{layer}]** `{loc}`: {desc}")
                lines.append("")

        if not issues_found:
            lines.append(
                "✔ 評価されたすべてのコンポーネントにおいて、設計仕様 $\\to$ テスト仕様 $\\to$ テスト実装コード間の重大な不一致・欠落は検出されませんでした。\n"
            )

        lines.extend(
            [
                "---",
                "",
                "## 2. 全コンポーネント評価一覧",
                "",
                "| コンポーネント | 判定 | 評価サマリー | 検出Issue数 |",
                "| :--- | :---: | :--- | :---: |",
            ]
        )
        for r in self.results:
            badge = (
                "🟢 PASS"
                if r.status == "PASS"
                else ("🟡 WARN" if r.status == "WARN" else "🔴 FAIL")
            )
            lines.append(f"| `{r.component_name}` | {badge} | {r.summary} | {len(r.issues)} |")

        return "\n".join(lines)


class TestChainJudge:
    __test__ = False
    """
Evaluates 3-tier end-to-end consistency between Design Spec -> Test Spec -> Test Code
    using LLM as a Judge.
"""

    def __init__(self, config: Config):
        self.config = config

    def auto_discover_targets(self, root_dir: Path | None = None) -> list[TestChainTarget]:
        """Automatically pairs design specifications with test specifications and test implementation files."""
        root = root_dir or Path(self.config.project.docs_root)
        project_root = root.parent if root.name == "docs" else root
        targets: list[TestChainTarget] = []
        design_files = list(root.glob("components/**/*.md"))
        # Component map
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

            # Locate relevant test implementation code generically
            test_code_files: list[Path] = []
            tc_cfg = getattr(self.config, "test_chain", None)
            test_dirs = (
                tc_cfg.test_dirs
                if (tc_cfg and tc_cfg.test_dirs)
                else ["tests", "tests/**", "experiments/**", "scenarios", "scenarios/**"]
            )

            words = [w for w in comp_stem.split("_") if len(w) >= 3]
            search_roots = [project_root] if project_root != root else [root.parent, root]
            for s_root in search_roots:
                for t_dir_pat in test_dirs:
                    for cand_dir in s_root.glob(t_dir_pat):
                        if cand_dir.is_dir():
                            # Direct match by component name stem or words
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
                # Truncate very long files to first 300 lines for prompt budget
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

        judge_llm = LLMJudge(self.config)
        try:
            raw_response = judge_llm._query_llm(prompt, backend, model)
            parsed = self._parse_json_response(raw_response)
            status = parsed.get("status", "WARN")
            summary = parsed.get("summary", "No summary provided by LLM.")
            issues = parsed.get("issues", [])
            return TestChainResult(
                component_name=target.component_name,
                design_doc=str(target.design_doc_path),
                test_spec=str(target.test_spec_path),
                test_code_files=[str(p) for p in target.test_code_paths],
                status=status,
                summary=summary,
                issues=issues,
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

    def _parse_json_response(self, raw_resp: str) -> dict[str, Any]:
        cleaned = re.sub(r"^```(?:json)?", "", raw_resp.strip(), flags=re.MULTILINE)
        cleaned = re.sub(r"```$", "", cleaned.strip(), flags=re.MULTILINE).strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            match = re.search(r"\{[\s\S]*\}", cleaned)
            if match:
                try:
                    return json.loads(match.group(0))
                except json.JSONDecodeError:
                    pass
            return {
                "status": "WARN",
                "summary": f"Could not parse valid JSON from LLM response: {raw_resp[:150]}...",
                "issues": [],
            }
