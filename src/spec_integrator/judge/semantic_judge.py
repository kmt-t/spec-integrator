from __future__ import annotations

import os
import re
import json
import urllib3
import requests
from pathlib import Path
from dataclasses import dataclass, field
from spec_integrator.config import Config
from spec_integrator.graph import Graph
from spec_integrator.parser import ParsedDocument

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


JUDGE_PROMPT_TEMPLATE = """You are a strict System Specification Verification Judge.
Your job is to audit consistency, completeness, and contradictions between a Requirement/Definition section and its referencing Design sections.

Target Keyword/Requirement ID: {item_label}

=== DEFINITION SECTIONS ===
{definition_texts}

=== REFERENCING DESIGN SECTIONS ===
{referencing_texts}

=== EVALUATION CRITERIA ===
1. Vertical consistency: Are there contradictions, mismatched parameters, conflicting
   assumptions, or outdated invariants between the DEFINITION and each referencing design?
2. Horizontal consistency: Compare the referencing sections AGAINST EACH OTHER, pairwise.
   Two sections may each be compatible with the definition while contradicting one another,
   because the definition is usually more abstract than either. This is the most common way
   a specification rots: one document is updated and its counterpart is not. Report a
   contradiction here even when every section conforms to the definition. Look specifically
   for the same mechanism being described with different register assignments, different
   buffer or bank counts, different state names, different call signatures, different
   ordering guarantees, or one section claiming an operation is free while another describes
   work it performs.
3. Numeric agreement: Check the actual arithmetic. Where sections state sizes, counts,
   budgets or totals, verify that component values sum to the stated totals and that the
   same named quantity holds the same value everywhere. Do not treat a number as correct
   merely because the definition also states it — if a total does not equal the sum of its
   parts, that is an ERROR, and if two sections give different values for one quantity, say
   which sections disagree.
4. Completeness: Do referencing sections fulfill or follow the rules specified in the definition?
5. Clarity: Are there any ambiguous or unspecified requirements left unresolved?
6. Redundancy & Duplication Audit (Avoid Stale Duplication, Permit Layered Perspectives):
   Audit the texts for copy-pasted, redundant, or multi-location duplication of concrete specifications.
   - PERMITTED AND ENCOURAGED (Legitimate Layered / Multi-perspective Descriptions):
     Describing the same design subject from DIFFERENT architectural abstraction layers or complementary viewpoints
     is valid and must NOT be flagged as duplication. Examples of legitimate layering:
       * Tier 1 (Architecture): High-level system goals, 6 pillars summary, end-to-end rationale.
       * Tier 2 (Subsystem/Manager): Component lifecycle, state machines, API integration, fallback policies.
       * Tier 3 (Leaf Component): Exact byte/bitfield layouts, physical offsets, register assignments, stencils.
       * Formal/Verifier: Mathematical properties, invariants, proof assertions, empirical benchmarks.
   - FLAGGED AS REDUNDANT (WARNING / ERROR):
     * If two or more sections duplicate the SAME level of detailed technical specification (e.g., verbatim
       copy-pasted struct tables, duplicated binary layouts, identical step-by-step algorithm lists) instead
       of establishing a single Source of Truth and referencing it via link, flag as a WARNING (Redundant Specification Duplication).
     * If duplicated descriptions have drifted, mismatched parameters, or conflicting details between copies,
       flag as an ERROR (Drifted Duplicate / Inconsistency).
7. Claim-Evidence Substantiation & Unbacked Assertions:
   Identify all factual, safety, or empirical claims made in the prose (e.g., "formally verified",
   "proven deadlock-free", "zero overhead", "measured on Cortex-M7", specific benchmarks or cycles).
   - If a section claims a property is "proven", "verified", or "measured", check whether the text
     explicitly cites a concrete artifact (formal model file, concept test, WIT interface, or benchmark log).
   - An assertion of a verification without citing the backing artifact, or relying on a vacuous/unrelated
     model, is an ERROR (Unbacked Verification Claim).
   - A bare empirical measurement stated as an accomplished fact without an environment or artifact
     reference is a WARNING (Unsourced Metric / Measurement).
   - Valid design estimates, aspirations, or budgeted targets ("目標", "想定", "target", "budgeted") are
     acceptable and must not be flagged as errors.
Judge what the text actually says, not what it evidently intends. Restating a section's
claim back as confirmation is not an audit. If the sections agree, say so briefly; do not
manufacture issues. Cite the specific section names on both sides of any contradiction.

=== OUTPUT FORMAT ===
Respond ONLY with a valid JSON object in English in the following format:
```json
{{
  "status": "PASS" | "WARN" | "FAIL",
  "summary": "Concise explanation of the evaluation result in English",
  "issues": [
    {{
      "severity": "ERROR" | "WARNING",
      "location": "File or Section name",
      "description": "Detailed explanation of contradiction, duplicate, or missing spec in English"
    }}
  ]
}}
```
"""


@dataclass
class JudgeResult:
    item_id: str
    item_label: str
    status: str  # "PASS", "WARN", "FAIL", "SKIPPED"
    summary: str
    issues: list[dict] = field(default_factory=list)
    # Documents this verdict was formed over. Recorded explicitly because a
    # clean document contributes no issue text, so its path would otherwise
    # never appear in the report and would read as never audited.
    covered_files: list[str] = field(default_factory=list)


@dataclass
class JudgeReport:
    results: list[JudgeResult] = field(default_factory=list)
    total_evaluated: int = 0
    pass_count: int = 0
    warn_count: int = 0
    fail_count: int = 0

    def __iter__(self):
        return iter(self.results)

    def __len__(self):
        return len(self.results)

    def __getitem__(self, idx):
        return self.results[idx]

    def to_markdown(self) -> str:
        lines = [
            "# Fireball 仕様矛盾・セマンティック整合性監査レポート (LLM as a Judge)",
            "",
            f"- **監査サブグラフ総数**: {self.total_evaluated}",
            f"- **合格 (PASS)**: {self.pass_count}",
            f"- **警告 (WARN)**: {self.warn_count}",
            f"- **不合格 (FAIL)**: {self.fail_count}",
            "",
            "---",
            "",
            "## 1. 検出された矛盾・警告 (Issues Found)",
            "",
        ]

        issues_found = False
        for r in self.results:
            if r.status in ("WARN", "FAIL") or r.issues:
                issues_found = True
                badge = "🔴 FAIL" if r.status == "FAIL" else "🟡 WARN"
                lines.append(f"### {badge}: `{r.item_label}`")
                lines.append(f"- **サマリー**: {r.summary}")
                if r.issues:
                    lines.append("- **詳細項目**:")
                    for iss in r.issues:
                        sev = iss.get("severity", "WARNING")
                        loc = iss.get("location", "Unknown")
                        desc = iss.get("description", "")
                        lines.append(f"  - **[{sev}]** `{loc}`: {desc}")
                lines.append("")

        if not issues_found:
            lines.append("✔ 評価されたすべてのサブグラフにおいて、定義と参照設計間の重大な矛盾は検出されませんでした。\n")

        lines.extend([
            "---",
            "",
            "## 2. 全評価結果一覧",
            "",
            "| キーワード / 要求ID | 判定 | 評価サマリー | 検出Issue数 |",
            "| :--- | :---: | :--- | :---: |",
        ])

        for r in self.results:
            badge = "🟢 PASS" if r.status == "PASS" else ("🟡 WARN" if r.status == "WARN" else "🔴 FAIL")
            lines.append(f"| `{r.item_label}` | {badge} | {r.summary} | {len(r.issues)} |")

        return "\n".join(lines)


class SemanticJudge:
    """Evaluates semantic consistency, completeness, and contradictions

    between specification definitions and their referencing design sections
    using an LLM as a Judge.
    """
    def __init__(self, config: Config):
        self.config = config

    def judge_subgraphs(self, subgraphs: list[dict], documents: list[ParsedDocument],
                         backend: str | None = None, model: str | None = None,
                         max_subgraphs: int = 10,
                         exhaustive: bool = False,
                         min_references: int = 1,
                         changed_sections: set[str] | None = None) -> JudgeReport:
        report = JudgeReport()
        selected_backend = backend or self.config.llm_judge.default_backend

        llm_tag = self.config.llm_judge.tag

        # Filter candidates based on mode
        if exhaustive:
            # Exhaustive mode: check ALL subgraphs with references (or all subgraphs if min_references == 0)
            target_subgraphs = [sg for sg in subgraphs if len(sg.get("referenced_in", [])) >= min_references]
        else:
            # Tagged priority mode
            tagged_subgraphs = []
            for sg in subgraphs:
                has_tag = False
                for sec_id in sg["defined_in"] + sg["referenced_in"]:
                    doc, sec = self._find_doc_and_sec(sec_id, documents)
                    if doc and (llm_tag in doc.all_tags or (sec and llm_tag in sec.tags)):
                        has_tag = True
                        break
                if has_tag:
                    tagged_subgraphs.append(sg)

            if tagged_subgraphs:
                target_subgraphs = tagged_subgraphs
            else:
                target_subgraphs = [sg for sg in subgraphs if len(sg.get("referenced_in", [])) >= min_references]

        # A definition and its referencing sections are audited together as one
        # subgraph regardless of which side actually moved -- there is no need
        # to work out whether it was the definition or a reference that changed
        # (or classify the edit as significant) before deciding to re-check.
        # Whether the change matters is exactly the question the LLM answers;
        # a section that changed in a way that doesn't matter just comes back
        # PASS. This also covers definitions with no {VERIFY_LLM} tag of their
        # own, since selection here is driven by what changed, not by tags.
        if changed_sections is not None:
            target_subgraphs = [
                sg for sg in target_subgraphs
                if (set(sg.get("defined_in", [])) | set(sg.get("referenced_in", []))) & changed_sections
            ]

        # Apply max_subgraphs limit if > 0
        if max_subgraphs > 0:
            target_candidates = target_subgraphs[:max_subgraphs]
        else:
            target_candidates = target_subgraphs

        print(f"Auditing {len(target_candidates)} requirement subgraph(s) using LLM Backend: '{selected_backend}'...")
        if exhaustive:
            print(f"  (Exhaustive mode: checking all subgraphs with >= {min_references} reference(s))")

        for idx, sg in enumerate(target_candidates, start=1):
            ref_count = len(sg.get("referenced_in", []))
            print(f"  [{idx}/{len(target_candidates)}] Evaluating '{sg['item_label']}' ({ref_count} reference(s))...", flush=True)
            res = self._evaluate_single_subgraph(sg, documents, selected_backend, model)
            report.results.append(res)
            if res.status == "PASS":
                report.pass_count += 1
            elif res.status == "WARN":
                report.warn_count += 1
            elif res.status == "FAIL":
                report.fail_count += 1

            badge = "PASS" if res.status == "PASS" else ("WARN" if res.status == "WARN" else "FAIL")
            print(f"       -> Result: {badge} ({res.summary[:80]})", flush=True)

        report.total_evaluated = len(report.results)
        return report

    @staticmethod
    def _covered_files(sg: dict) -> list[str]:
        """File paths the subgraph draws its sections from."""
        files: set[str] = set()
        for sec_id in list(sg.get("defined_in", [])) + list(sg.get("referenced_in", [])):
            path = str(sec_id)
            if path.startswith("sec:"):
                path = path[4:]
            files.add(path.split("#", 1)[0])
        return sorted(files)

    def _evaluate_single_subgraph(self, sg: dict, documents: list[ParsedDocument],
                                  backend: str, model: str | None) -> JudgeResult:
        item_label = sg["item_label"]
        covered = self._covered_files(sg)
        
        def_texts = []
        for sec_id in sg["defined_in"]:
            content = self._retrieve_section_content(sec_id, documents)
            def_texts.append(f"--- Section: {sec_id} ---\n{content}")

        ref_texts = []
        for sec_id in sg["referenced_in"]:
            content = self._retrieve_section_content(sec_id, documents)
            ref_texts.append(f"--- Section: {sec_id} ---\n{content}")

        prompt = JUDGE_PROMPT_TEMPLATE.format(
            item_label=item_label,
            definition_texts="\n\n".join(def_texts) if def_texts else "(No explicit definition section)",
            referencing_texts="\n\n".join(ref_texts) if ref_texts else "(No referencing sections)",
        )

        if backend == "mock":
            return JudgeResult(
                item_id=sg["item_id"],
                item_label=item_label,
                status="PASS",
                summary="Mock evaluation passed.",
                issues=[],
                covered_files=covered,
            )
        if backend not in ("sakura", "ollama", "openrouter"):
            return JudgeResult(
                item_id=sg["item_id"],
                item_label=item_label,
                status="SKIPPED",
                summary=f"Unknown backend '{backend}'.",
                issues=[],
                covered_files=covered,
            )

        # _call_sakura / _call_openrouter already retries transport-level failures
        # (non-200, timeout, connection errors) internally.
        import time
        last_err: Exception | None = None
        parsed: dict | None = None
        for attempt in range(3):
            try:
                if backend == "sakura":
                    raw_resp = self._call_sakura(prompt, model)
                elif backend == "openrouter":
                    raw_resp = self._call_openrouter(prompt, model)
                else:
                    raw_resp = self._call_ollama(prompt, model)
                candidate = self._extract_json(raw_resp)
                if not candidate.get("status"):
                    raise ValueError("response JSON has no 'status' field")
                parsed = candidate
                break
            except Exception as e:
                last_err = e
                if attempt < 2:
                    time.sleep(2)

        if parsed is None:
            return JudgeResult(
                item_id=sg["item_id"],
                item_label=item_label,
                status="FAIL",
                summary=f"Judge error after 3 attempts: {last_err}",
                issues=[{"severity": "ERROR", "location": item_label,
                        "description": f"No usable verdict after 3 attempts: {last_err}"}],
                covered_files=covered,
            )

        issues = parsed.get("issues", []) or []
        status = parsed["status"]
        # An audit that lists blocking issues has not passed, whatever it says.
        if status == "PASS" and any(
                str(i.get("severity", "")).upper() == "ERROR"
                for i in issues if isinstance(i, dict)):
            status = "FAIL"

        return JudgeResult(
            item_id=sg["item_id"],
            item_label=item_label,
            status=status,
            summary=parsed.get("summary", ""),
            issues=issues,
            covered_files=covered,
        )

    def _retrieve_section_content(self, sec_id: str, documents: list[ParsedDocument]) -> str:
        doc, sec = self._find_doc_and_sec(sec_id, documents)
        if sec:
            return self._budgeted(sec.body_text)
        elif doc:
            return self._budgeted(doc.raw_content)
        return ""

    def _budgeted(self, text: str) -> str:
        """Applies the per-section character budget, marking any cut explicitly.

        Silent truncation lets the judge report 'no contradiction found' about
        text it was never shown, which reads identically to a real pass.
        """
        limit = self.config.llm_judge.section_char_budget
        if limit <= 0 or len(text) <= limit:
            return text
        omitted = len(text) - limit
        return (text[:limit] +
                f"\n\n[TRUNCATED: {omitted} further characters of this section were not shown. "
                "Do not conclude that this section is consistent with the others on the basis "
                "of the portion above; report the truncation as a limitation instead.]")

    def _find_doc_and_sec(self, sec_id: str, documents: list[ParsedDocument]
                          ) -> tuple[ParsedDocument | None, any]:
        for doc in documents:
            if doc.file_path == sec_id:
                return doc, None
            for sec in doc.sections:
                if sec.section_id == sec_id or f"{doc.file_path}#{sec.heading}" == sec_id:
                    return doc, sec
        return None, None

    def _call_sakura(self, prompt: str, model: str | None) -> str:
        import time
        b_config = self.config.llm_judge.backends.get("sakura")
        api_key_env = b_config.api_key_env if b_config else "SAKURA_API_KEY"
        api_key = os.environ.get(api_key_env, "")
        if not api_key:
            raise ValueError(f"Sakura API key environment variable '{api_key_env}' is not set.")

        selected_model = model or (b_config.model if (b_config and b_config.model) else "preview/gemma-4-31B-it")
        endpoint = (b_config.endpoint if (b_config and b_config.endpoint) else "https://api.ai.sakura.ad.jp/v1/chat/completions")

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": selected_model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0
        }

        last_err = None
        for attempt in range(3):
            try:
                resp = requests.post(endpoint, json=payload, headers=headers, timeout=60, verify=False)
                if resp.status_code != 200:
                    raise RuntimeError(f"Sakura API returned status {resp.status_code}: {resp.text}")
                data = resp.json()
                return data["choices"][0]["message"]["content"]
            except Exception as e:
                last_err = e
                time.sleep(2)
        raise RuntimeError(f"Failed to call Sakura API after 3 attempts: {last_err}")

    def _call_openrouter(self, prompt: str, model: str | None) -> str:
        import time
        b_config = self.config.llm_judge.backends.get("openrouter")
        api_key_env = b_config.api_key_env if b_config else "OPENROUTER_API_KEY"
        api_key = os.environ.get(api_key_env, "")
        if not api_key:
            raise ValueError(f"OpenRouter API key environment variable '{api_key_env}' is not set.")

        selected_model = model or (b_config.model if (b_config and b_config.model) else "qwen/qwen3.8-27b")
        endpoint = (b_config.endpoint if (b_config and b_config.endpoint) else "https://openrouter.ai/api/v1/chat/completions")

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/kmt-t/fireball",
            "X-Title": "Fireball Spec Integrator"
        }
        payload = {
            "model": selected_model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0
        }

        last_err = None
        for attempt in range(3):
            try:
                resp = requests.post(endpoint, json=payload, headers=headers, timeout=90, verify=False)
                if resp.status_code != 200:
                    raise RuntimeError(f"OpenRouter API returned status {resp.status_code}: {resp.text}")
                data = resp.json()
                return data["choices"][0]["message"]["content"]
            except Exception as e:
                last_err = e
                time.sleep(2)
        raise RuntimeError(f"Failed to call OpenRouter API after 3 attempts: {last_err}")

    def _call_ollama(self, prompt: str, model: str | None) -> str:
        b_config = self.config.llm_judge.backends.get("ollama")
        endpoint = (b_config.endpoint if (b_config and b_config.endpoint) else "http://localhost:11434") + "/api/generate"
        selected_model = model or (b_config.model if (b_config and b_config.model) else "llama3")

        payload = {
            "model": selected_model,
            "prompt": prompt,
            "stream": False,
            "format": "json"
        }
        resp = requests.post(endpoint, json=payload, timeout=60)
        if resp.status_code != 200:
            raise RuntimeError(f"Ollama API returned status {resp.status_code}: {resp.text}")
        data = resp.json()
        return data.get("response", "")

    def _extract_json(self, raw_text: str) -> dict:
        code_block = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", raw_text)
        if code_block:
            json_str = code_block.group(1)
        else:
            first_brace = raw_text.find("{")
            last_brace = raw_text.rfind("}")
            if first_brace != -1 and last_brace != -1:
                json_str = raw_text[first_brace:last_brace + 1]
            else:
                json_str = raw_text

        return json.loads(json_str)


# Alias for backward compatibility
LLMJudge = SemanticJudge
