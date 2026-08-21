from __future__ import annotations

import os
import re
import json
import urllib3
import requests
from pathlib import Path
from dataclasses import dataclass, field, asdict
from spec_integrator.config import Config
from spec_integrator.parser import ParsedDocument, ParsedSection

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


ASSESS_PROMPT_TEMPLATE = """You are a Principal Embedded Systems Architect and Formal Verification Expert.
Your job is to analyze the CONTENT and ALGORITHMS of the following specification section, evaluating its complexity, design risks, and whether mathematical formal verification (via Python pyModelChecking: Kripke / CTL / LTL model checking) or semantic LLM auditing is required.

Section Title: {heading}
File: {file_path} (Tier: {tier})
Keywords/Tags: {keywords}

=== SECTION CONTENT ===
{content}

=== EVALUATION CRITERIA ===
1. Complexity (1-5): State space size, asynchronous/coroutine behavior, zero-copy ownership transfer, cache lifecycle, low-level hardware interaction.
2. Design Risk (1-5): Deadlock, race condition, memory corruption, starvation, unhandled failure modes, ambiguous/unspecified assumptions, missing error recovery.
3. Formal Verification Need: Does this section contain critical concurrent state transitions, invariant conditions, or deadlock-prone resource handoffs that REQUIRE mathematical proof via pyModelChecking (CTL/LTL)?
4. Verification Triage:
   - "pyModelChecking": For stateful, concurrent, invariant/liveness properties, deadlock-prone logic.
   - "LLM_Judge": For API contracts, semantic parameter alignment, cross-tier compliance.
   - "Static": For simple declarative tables, constants, or stateless interfaces.

=== OUTPUT FORMAT ===
Respond ONLY with a valid JSON object in English in the following format:
```json
{{
  "complexity_score": 1 to 5,
  "risk_score": 1 to 5,
  "formal_needed": true | false,
  "recommended_verification": "pyModelChecking" | "LLM_Judge" | "Static",
  "suggested_tags": ["{{VERIFY_FORMAL}}"] or ["{{VERIFY_LLM}}"] or [],
  "risk_factors": [
    "Brief description of specific risk factor 1 in English",
    "Brief description of specific risk factor 2 in English"
  ],
  "summary": "Concise summary of complexity and risk assessment in English"
}}
```
"""


@dataclass
class SectionRiskAssessment:
    section_id: str
    file_path: str
    heading: str
    tier: str | int
    complexity_score: int
    risk_score: int
    formal_needed: bool
    recommended_verification: str
    suggested_tags: list[str]
    risk_factors: list[str] = field(default_factory=list)
    summary: str = ""


@dataclass
class RiskAssessmentReport:
    assessments: list[SectionRiskAssessment] = field(default_factory=list)
    total_evaluated: int = 0
    formal_candidates_count: int = 0
    llm_candidates_count: int = 0

    def to_markdown(self) -> str:
        lines = [
            "# Fireball 設計複雑度 & リスク評価レポート (Risk Assessment Report)",
            "",
            f"- **評価セクション総数**: {self.total_evaluated}",
            f"- **形式検証 (pyModelChecking) 推奨セクション**: {self.formal_candidates_count}",
            f"- **LLM 意味監査 推奨セクション**: {self.llm_candidates_count}",
            "",
            "---",
            "",
            "## 1. 形式検証 (pyModelChecking) が推奨される重要セクション",
            "",
            "| ファイル | セクション | 複雑度 | リスク | 推奨検証 | 推奨タグ | 主なリスク要因 |",
            "| :--- | :--- | :---: | :---: | :--- | :--- | :--- |",
        ]

        formal_items = [a for a in self.assessments if a.formal_needed]
        formal_items.sort(key=lambda x: (x.risk_score, x.complexity_score), reverse=True)

        for a in formal_items:
            tags_str = " ".join(f"`{t}`" for t in a.suggested_tags) if a.suggested_tags else "`{VERIFY_FORMAL}`"
            factors_str = "<br>".join(a.risk_factors[:2]) if a.risk_factors else a.summary
            lines.append(
                f"| `{a.file_path}` | **{a.heading}** | {a.complexity_score}/5 | **{a.risk_score}/5** | `{a.recommended_verification}` | {tags_str} | {factors_str} |"
            )

        lines.extend([
            "",
            "---",
            "",
            "## 2. 全セクションの複雑度・リスク評価一覧 (降順)",
            "",
            "| ファイル | セクション | Tier | 複雑度 | リスク | 推奨手法 | 推奨タグ | 評価サマリー |",
            "| :--- | :--- | :---: | :---: | :---: | :--- | :--- | :--- |",
        ])

        all_sorted = sorted(self.assessments, key=lambda x: (x.risk_score + x.complexity_score), reverse=True)
        for a in all_sorted:
            tags_str = " ".join(f"`{t}`" for t in a.suggested_tags) if a.suggested_tags else "-"
            lines.append(
                f"| `{a.file_path}` | {a.heading} | {a.tier} | {a.complexity_score}/5 | {a.risk_score}/5 | `{a.recommended_verification}` | {tags_str} | {a.summary} |"
            )

        return "\n".join(lines)


class RiskAssessor:
    def __init__(self, config: Config):
        self.config = config

    def assess_documents(self, documents: list[ParsedDocument],
                         backend: str | None = None, model: str | None = None,
                         max_sections: int = 15) -> RiskAssessmentReport:
        report = RiskAssessmentReport()
        selected_backend = backend or self.config.llm_judge.default_backend

        # Select target candidate sections (ignore root/format, focus on core logic, algorithms, state machines)
        candidates: list[tuple[ParsedDocument, ParsedSection]] = []
        for doc in documents:
            if doc.tier == 0 or doc.tier == "meta":
                continue
            for sec in doc.sections:
                if sec.level in (2, 3) and len(sec.body_text.strip()) > 100:
                    # Filter out purely structural headings
                    if any(kw in sec.heading.lower() for kw in ["アーキテクチャ分類", "静的モデル", "ディレクトリ", "目次"]):
                        continue
                    candidates.append((doc, sec))

        # Sort candidates to prioritize algorithmic, protocol, stateful, or verification sections
        def candidate_priority(item: tuple[ParsedDocument, ParsedSection]) -> int:
            _, s = item
            score = 0
            text = (s.heading + " " + s.body_text).lower()
            if any(k in text for k in ["algorithm", "アルゴリズム", "state", "状態遷移", "handoff", "channel", "lock", "queue", "interrupt", "割り込み", "cache", "jit"]):
                score += 10
            if s.keywords:
                score += 5
            return score

        candidates.sort(key=candidate_priority, reverse=True)
        target_candidates = candidates[:max_sections]

        print(f"Assessing complexity & design risk for {len(target_candidates)} candidate section(s) using Backend: '{selected_backend}'...")

        for idx, (doc, sec) in enumerate(target_candidates, start=1):
            print(f"  [{idx}/{len(target_candidates)}] Assessing '{doc.file_path} -> {sec.heading}'...", flush=True)
            assessment = self._assess_single_section(doc, sec, backend=selected_backend, model=model)
            report.assessments.append(assessment)
            if assessment.formal_needed:
                report.formal_candidates_count += 1
            if assessment.recommended_verification == "LLM_Judge":
                report.llm_candidates_count += 1

        report.total_evaluated = len(report.assessments)
        return report

    def _assess_single_section(self, doc: ParsedDocument, sec: ParsedSection,
                               backend: str, model: str | None = None) -> SectionRiskAssessment:
        prompt = ASSESS_PROMPT_TEMPLATE.format(
            heading=sec.heading,
            file_path=doc.file_path,
            tier=doc.tier,
            keywords=" ".join(f"{{{k}}}" for k in sec.keywords),
            content=sec.body_text[:2500]
        )

        try:
            if backend == "sakura":
                raw_resp = self._call_sakura(prompt, model)
            elif backend == "ollama":
                raw_resp = self._call_ollama(prompt, model)
            else:
                raw_resp = self._call_sakura(prompt, model)

            parsed = self._extract_json(raw_resp)
            return SectionRiskAssessment(
                section_id=sec.section_id,
                file_path=doc.file_path,
                heading=sec.heading,
                tier=doc.tier,
                complexity_score=int(parsed.get("complexity_score", 3)),
                risk_score=int(parsed.get("risk_score", 3)),
                formal_needed=bool(parsed.get("formal_needed", False)),
                recommended_verification=str(parsed.get("recommended_verification", "LLM_Judge")),
                suggested_tags=list(parsed.get("suggested_tags", [])),
                risk_factors=list(parsed.get("risk_factors", [])),
                summary=str(parsed.get("summary", ""))
            )
        except Exception as e:
            return SectionRiskAssessment(
                section_id=sec.section_id,
                file_path=doc.file_path,
                heading=sec.heading,
                tier=doc.tier,
                complexity_score=3,
                risk_score=3,
                formal_needed=False,
                recommended_verification="LLM_Judge",
                suggested_tags=[],
                risk_factors=[f"Assessment error: {e}"],
                summary=f"Assessment error: {e}"
            )

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
