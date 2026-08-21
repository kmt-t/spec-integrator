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
1. Consistency: Are there any contradictions or mismatched parameters between the definition and referencing designs?
2. Completeness: Do referencing sections fulfill or follow the rules specified in the definition?
3. Clarity: Are there any ambiguous or unspecified requirements left unresolved?

=== OUTPUT FORMAT ===
Respond ONLY with a valid JSON object in the following format:
```json
{{
  "status": "PASS" | "WARN" | "FAIL",
  "summary": "Brief explanation of the evaluation result (in Japanese or English)",
  "issues": [
    {{
      "severity": "ERROR" | "WARNING",
      "location": "File or Section name",
      "description": "Detailed explanation of contradiction or missing spec"
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


class LLMJudge:
    def __init__(self, config: Config):
        self.config = config

    def judge_subgraphs(self, subgraphs: list[dict], documents: list[ParsedDocument],
                        backend: str | None = None, model: str | None = None,
                        max_subgraphs: int = 10) -> list[JudgeResult]:
        results: list[JudgeResult] = []
        selected_backend = backend or self.config.llm_judge.default_backend

        # Filter subgraphs that contain {VERIFY_LLM} tag in any of their member docs/sections
        llm_tag = self.config.llm_judge.tag
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

        # If tagged subgraphs exist, prioritize them; otherwise take general subgraphs
        target_subgraphs = tagged_subgraphs if tagged_subgraphs else [sg for sg in subgraphs if sg["referenced_in"]]
        target_subgraphs = target_subgraphs[:max_subgraphs]

        print(f"Auditing {len(target_subgraphs)} requirement subgraph(s) using LLM Backend: '{selected_backend}'...")

        for idx, sg in enumerate(target_subgraphs, start=1):
            print(f"  [{idx}/{len(target_subgraphs)}] Evaluating '{sg['item_label']}'...", flush=True)
            res = self._evaluate_single_subgraph(sg, documents, selected_backend, model)
            results.append(res)
            badge = "PASS" if res.status == "PASS" else ("WARN" if res.status == "WARN" else "FAIL")
            print(f"       -> Result: {badge} ({res.summary[:80]})", flush=True)

        return results

    def _evaluate_single_subgraph(self, sg: dict, documents: list[ParsedDocument],
                                  backend: str, model: str | None) -> JudgeResult:
        item_label = sg["item_label"]
        
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
            referencing_texts="\n\n".join(ref_texts) if ref_texts else "(No referencing sections)"
        )

        try:
            if backend == "mock":
                return JudgeResult(
                    item_id=sg["item_id"],
                    item_label=item_label,
                    status="PASS",
                    summary="Mock evaluation passed.",
                    issues=[]
                )
            elif backend == "sakura":
                raw_resp = self._call_sakura(prompt, model)
            elif backend == "ollama":
                raw_resp = self._call_ollama(prompt, model)
            else:
                return JudgeResult(
                    item_id=sg["item_id"],
                    item_label=item_label,
                    status="SKIPPED",
                    summary=f"Unknown backend '{backend}'.",
                    issues=[]
                )

            # Parse JSON from response
            parsed = self._extract_json(raw_resp)
            return JudgeResult(
                item_id=sg["item_id"],
                item_label=item_label,
                status=parsed.get("status", "PASS"),
                summary=parsed.get("summary", ""),
                issues=parsed.get("issues", [])
            )

        except Exception as e:
            return JudgeResult(
                item_id=sg["item_id"],
                item_label=item_label,
                status="FAIL",
                summary=f"Judge execution error: {e}",
                issues=[{"severity": "ERROR", "location": "LLMJudge", "description": str(e)}]
            )

    def _call_sakura(self, prompt: str, model: str | None) -> str:
        import time
        b_config = self.config.llm_judge.backends.get("sakura")
        api_key_env = b_config.api_key_env if b_config else "SAKURA_API_KEY"
        api_key = os.environ.get(api_key_env, "")
        if not api_key:
            raise ValueError(f"Sakura API key environment variable '{api_key_env}' is not set.")

        selected_model = model or (b_config.model if (b_config and b_config.model) else "gpt-oss-120b")
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
                resp = requests.post(endpoint, headers=headers, json=payload, timeout=90, verify=False)
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"]
            except Exception as e:
                last_err = e
                time.sleep(2 * (attempt + 1))
        
        raise last_err or RuntimeError("Sakura API call failed after 3 attempts.")

    def _call_ollama(self, prompt: str, model: str | None) -> str:
        b_config = self.config.llm_judge.backends.get("ollama")
        endpoint = b_config.endpoint if b_config else "http://localhost:11434"
        selected_model = model or (b_config.model if b_config else "llama3")

        url = f"{endpoint.rstrip('/')}/api/generate"
        payload = {
            "model": selected_model,
            "prompt": prompt,
            "stream": False,
            "format": "json"
        }
        resp = requests.post(url, json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        return data.get("response", "")

    def _extract_json(self, text: str) -> dict:
        # Match ```json ... ```
        match = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
        
        # Match outermost { ... }
        match_obj = re.search(r"(\{.*\})", text, re.DOTALL)
        if match_obj:
            try:
                return json.loads(match_obj.group(1))
            except json.JSONDecodeError:
                pass

        # Try direct JSON parse
        try:
            return json.loads(text.strip())
        except json.JSONDecodeError:
            return {
                "status": "PASS",
                "summary": text.strip()[:300],
                "issues": []
            }

    def _find_doc_and_sec(self, sec_id: str, documents: list[ParsedDocument]):
        if sec_id.startswith("file:"):
            rel_file = sec_id[5:]
            for doc in documents:
                if doc.file_path == rel_file:
                    return doc, None
        elif sec_id.startswith("sec:"):
            raw = sec_id[4:]
            if "#" in raw:
                rel_file, heading = raw.split("#", 1)
            else:
                rel_file, heading = raw, ""
            for doc in documents:
                if doc.file_path == rel_file:
                    for sec in doc.sections:
                        if sec.heading == heading:
                            return doc, sec
                    return doc, None
        return None, None

    def _retrieve_section_content(self, sec_id: str, documents: list[ParsedDocument]) -> str:
        doc, sec = self._find_doc_and_sec(sec_id, documents)
        if sec:
            return sec.body_text[:3000]
        elif doc:
            return doc.content[:3000]
        return f"(Section {sec_id} not found)"
