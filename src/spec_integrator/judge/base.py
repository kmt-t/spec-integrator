from __future__ import annotations

from spec_integrator.config import Config
from spec_integrator.judge.checksheet import Checksheet, submit_checksheet
from spec_integrator.models import ParsedDocument, ParsedSection


class BaseJudge:
    """Base class for LLM-based judges and risk assessors.

    Provides checksheet submission, content budgeting, and document lookup helpers.
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

    def _submit_checksheet(
        self, sheet: Checksheet, model: str | None = None, backend: str = "jev"
    ) -> dict:
        return submit_checksheet(self.config, sheet, model, backend)

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
