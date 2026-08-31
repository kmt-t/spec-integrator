from __future__ import annotations

import time
from typing import Any

from spec_integrator.config import Config
from spec_integrator.judge import llm_backend
from spec_integrator.models import ParsedDocument, ParsedSection


class BaseJudge:
    """Base class for LLM-based judges and risk assessors.

    Provides unified LLM communication, retry logic, prompt budgeting,
    JSON parsing, and document/subgraph lookup helpers.
    """

    def __init__(self, config: Config):
        self.config = config

    def _budgeted(self, text: str, max_chars: int | None = None) -> str:
        """Applies character budget, explicitly marking truncation to prevent vacuous verification."""
        limit = max_chars if max_chars is not None else self.config.llm_judge.section_char_budget
        if limit <= 0 or len(text) <= limit:
            return text
        omitted = len(text) - limit
        return (
            text[:limit] + f"\n\n[TRUNCATED: {omitted} further characters were not shown. "
            "Do not conclude consistency on the basis of the portion above; "
            "report the truncation as a limitation instead.]"
        )

    def _call_sakura(self, prompt: str, model: str | None) -> str:
        return llm_backend.call_sakura(self.config, prompt, model)

    def _call_openrouter(self, prompt: str, model: str | None) -> str:
        return llm_backend.call_openrouter(self.config, prompt, model)

    def _call_ollama(self, prompt: str, model: str | None) -> str:
        return llm_backend.call_ollama(self.config, prompt, model)

    def _call_llm_with_retry(
        self,
        prompt: str,
        backend: str | None = None,
        model: str | None = None,
        max_attempts: int = 3,
        retry_delay: float = 2.0,
    ) -> str:
        """Dispatches prompt to the specified backend with automatic retry."""
        selected_backend = backend or self.config.llm_judge.default_backend
        if selected_backend not in ("sakura", "openrouter", "ollama"):
            raise ValueError(f"Unsupported LLM backend: '{selected_backend}'")

        last_err: Exception | None = None
        for attempt in range(max_attempts):
            try:
                if selected_backend == "sakura":
                    return self._call_sakura(prompt, model)
                elif selected_backend == "openrouter":
                    return self._call_openrouter(prompt, model)
                elif selected_backend == "ollama":
                    return self._call_ollama(prompt, model)
            except Exception as e:
                last_err = e
                if attempt < max_attempts - 1:
                    time.sleep(retry_delay)

        raise RuntimeError(
            f"Failed to query LLM backend '{selected_backend}' after {max_attempts} attempts: {last_err}"
        )

    def _extract_json(self, raw_text: str) -> dict[str, Any]:
        """Extracts JSON object from LLM response."""
        return llm_backend.extract_json(raw_text)

    @staticmethod
    def _find_doc_and_sec(
        sec_id: str, documents: list[ParsedDocument]
    ) -> tuple[ParsedDocument | None, ParsedSection | None]:
        """Locates the document and/or section matching a section_id (or file_path)."""
        for doc in documents:
            if doc.file_path == sec_id:
                return doc, None
            for sec in doc.sections:
                if sec.section_id == sec_id or f"{doc.file_path}#{sec.heading}" == sec_id:
                    return doc, sec
        return None, None

    @classmethod
    def _covered_files(cls, sg: dict) -> list[str]:
        """Extracts unique file paths touched by a subgraph."""
        files: set[str] = set()
        for sec_id in list(sg.get("defined_in", [])) + list(sg.get("referenced_in", [])):
            path = str(sec_id).removeprefix("sec:")
            files.add(path.split("#", 1)[0])
        return sorted(files)

    def _retrieve_section_content(
        self, sec_id: str, documents: list[ParsedDocument], max_chars: int | None = None
    ) -> str:
        """Retrieves and budgets content for a given section ID."""
        doc, sec = self._find_doc_and_sec(sec_id, documents)
        if sec:
            return self._budgeted(sec.body_text, max_chars)
        elif doc:
            return self._budgeted(doc.content, max_chars)
        return ""

    def _representative(
        self, sg: dict, documents: list[ParsedDocument]
    ) -> tuple[ParsedDocument | None, ParsedSection | None]:
        """The keyword's defining (doc, section) if one exists, else its first reference."""
        for sec_id in list(sg.get("defined_in", [])) + list(sg.get("referenced_in", [])):
            doc, sec = self._find_doc_and_sec(sec_id, documents)
            if doc:
                return doc, sec
        return None, None
