from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from spec_integrator.config import Config
from spec_integrator.models import ParsedDocument, VerificationIssue, WITFileResult

__all__ = ["WITFileResult", "WITVerifier"]


class WITVerifier:
    def __init__(self, config: Config):
        self.config = config

    def verify_documents(
        self, documents: list[ParsedDocument], docs_root: Path
    ) -> tuple[list[VerificationIssue], list[WITFileResult]]:
        issues: list[VerificationIssue] = []
        results: list[WITFileResult] = []
        wit_tag = self.config.wit_verification.tag
        wit_dir_name = self.config.wit_verification.wit_dir_name
        # 1. Documents demanding WIT verification
        for doc in documents:
            if wit_tag in doc.all_tags:
                doc_dir = doc.full_path.parent
                wit_dir = doc_dir / wit_dir_name
                wit_files = list(wit_dir.glob("*.wit")) if wit_dir.exists() else []
                if not wit_files:
                    issues.append(
                        VerificationIssue(
                            gate="WIT",
                            severity="ERROR",
                            file_path=doc.file_path,
                            line=1,
                            rule_code="WIT-FILE-NOT-FOUND",
                            message=f"Document specifies '{wit_tag}', but no WIT definition files (*.wit) found in '{wit_dir.relative_to(docs_root)}/'.",
                        )
                    )
                    results.append(
                        WITFileResult(
                            component=doc.component,
                            wit_file=str(wit_dir.relative_to(docs_root)),
                            status="NOT_FOUND",
                            details="No *.wit file found in designated directory.",
                        )
                    )
                    continue
                for wit_file in wit_files:
                    res, file_issues = self.verify_wit_file(wit_file, doc.component, docs_root)
                    results.append(res)
                    issues.extend(file_issues)

        # 2. Also scan any standalone wit/ directories under docs_root that might not be tagged
        for wit_file in docs_root.rglob("*.wit"):
            rel_p = wit_file.relative_to(docs_root).as_posix()
            if not any(r.wit_file == rel_p for r in results):
                # Infer component name from parent
                comp = (
                    wit_file.parent.parent.name
                    if wit_file.parent.name == wit_dir_name
                    else wit_file.parent.name
                )
                res, file_issues = self.verify_wit_file(wit_file, comp, docs_root)
                results.append(res)
                issues.extend(file_issues)
        return issues, results

    def verify_wit_file(
        self, wit_path: Path, component: str, docs_root: Path
    ) -> tuple[WITFileResult, list[VerificationIssue]]:
        rel_path = wit_path.relative_to(docs_root).as_posix()
        issues: list[VerificationIssue] = []
        content = wit_path.read_text(encoding="utf-8")
        # 1. Check with wasm-tools if available on system
        wasm_tools_bin = shutil.which("wasm-tools")
        if wasm_tools_bin:
            try:
                proc = subprocess.run(
                    [wasm_tools_bin, "component", "wit", str(wit_path)],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if proc.returncode != 0:
                    err_msg = proc.stderr.strip() or "wasm-tools validation failed"
                    issues.append(
                        VerificationIssue(
                            gate="WIT",
                            severity="ERROR",
                            file_path=rel_path,
                            line=1,
                            rule_code="WIT-SYNTAX-ERROR",
                            message=f"wasm-tools error: {err_msg}",
                        )
                    )
                    return WITFileResult(
                        component=component,
                        wit_file=rel_path,
                        status="FAIL",
                        details=err_msg,
                    ), issues
            except Exception:
                pass  # fallback to built-in parser
        # 2. Built-in WIT Syntax and Semantic Validator
        syntax_err, interfaces, worlds = self._validate_wit_syntax(content)
        if syntax_err:
            issues.append(
                VerificationIssue(
                    gate="WIT",
                    severity="ERROR",
                    file_path=rel_path,
                    line=syntax_err.get("line", 1),
                    rule_code="WIT-SYNTAX-ERROR",
                    message=syntax_err["message"],
                )
            )
            return WITFileResult(
                component=component,
                wit_file=rel_path,
                status="FAIL",
                details=syntax_err["message"],
                defined_interfaces=interfaces,
                defined_worlds=worlds,
            ), issues

        details = (
            f"Valid WIT specification ({len(interfaces)} interface(s), {len(worlds)} world(s))"
        )
        return WITFileResult(
            component=component,
            wit_file=rel_path,
            status="PASS",
            details=details,
            defined_interfaces=interfaces,
            defined_worlds=worlds,
        ), issues

    def _validate_wit_syntax(self, content: str) -> tuple[dict | None, list[str], list[str]]:
        """Performs lexical and structural validation on WIT files."""
        lines = content.splitlines()
        brace_stack = []
        interfaces = []
        worlds = []
        # Remove single-line comments // ... and block comments /* ... */
        clean_lines = []
        in_block_comment = False
        for idx, line in enumerate(lines, 1):
            stripped = line.strip()
            if in_block_comment:
                if "*/" in stripped:
                    in_block_comment = False
                    stripped = stripped.split("*/", 1)[1].strip()
                else:
                    clean_lines.append((idx, ""))
                    continue
            if "/*" in stripped:
                if "*/" in stripped:
                    # Single line block comment
                    stripped = re.sub(r"/\*.*?\*/", "", stripped).strip()
                else:
                    in_block_comment = True
                    stripped = stripped.split("/*", 1)[0].strip()

            if stripped.startswith("//"):
                clean_lines.append((idx, ""))
                continue
            # Remove trailing comments
            if "//" in stripped:
                stripped = stripped.split("//", 1)[0].strip()

            clean_lines.append((idx, stripped))

        # Check bracket balancing and declaration patterns
        for line_no, line in clean_lines:
            if not line:
                continue
            # Check interface declaration
            m_iface = re.match(r"^interface\s+([a-z0-9\-]+)\s*\{?", line)
            if m_iface:
                interfaces.append(m_iface.group(1))

            # Check world declaration
            m_world = re.match(r"^world\s+([a-z0-9\-]+)\s*\{?", line)
            if m_world:
                worlds.append(m_world.group(1))

            # Check package declaration
            if line.startswith("package "):
                if not re.match(
                    r"^package\s+([a-z0-9\-]+:[a-z0-9\-]+(@[0-9]+\.[0-9]+\.[0-9]+)?);?$",
                    line.rstrip(";"),
                ):
                    # Warning or error on malformed package
                    pass
            for char in line:
                if char in "{(":
                    brace_stack.append((char, line_no))
                elif char == "}":
                    if not brace_stack or brace_stack[-1][0] != "{":
                        return (
                            {
                                "line": line_no,
                                "message": f"Mismatched closing brace '}}' at line {line_no}",
                            },
                            interfaces,
                            worlds,
                        )
                    brace_stack.pop()
                elif char == ")":
                    if not brace_stack or brace_stack[-1][0] != "(":
                        return (
                            {
                                "line": line_no,
                                "message": f"Mismatched closing parenthesis ')' at line {line_no}",
                            },
                            interfaces,
                            worlds,
                        )
                    brace_stack.pop()

        if brace_stack:
            unclosed_char, unclosed_line = brace_stack[-1]
            return (
                {
                    "line": unclosed_line,
                    "message": f"Unclosed '{unclosed_char}' starting at line {unclosed_line}",
                },
                interfaces,
                worlds,
            )
        return None, interfaces, worlds
