from __future__ import annotations

import re
from bisect import bisect_right
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

from spec_integrator.anti_sabotage.base import AntiSabotageCheck, AntiSabotageContext
from spec_integrator.config import ProseReadabilityConfig
from spec_integrator.models import ParsedDocument, VerificationIssue

__all__ = ["ProseReadabilityCheck"]


@dataclass(frozen=True, slots=True)
class ProseBlock:
    text: str
    line: int


class _Token(Protocol):
    dep_: str


class _Sentence(Protocol):
    text: str
    start_char: int
    end_char: int

    def __iter__(self) -> Iterator[_Token]: ...


class _ParsedText(Protocol):
    @property
    def sents(self) -> Iterable[_Sentence]: ...


class _JapaneseNLP(Protocol):
    def __call__(self, text: str) -> _ParsedText: ...


_FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}(?:\s|$)")
_LIST_RE = re.compile(r"^(\s*)(?:[-+*]|\d+[.)])\s+(.*)$")
_TABLE_RE = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_RULE_RE = re.compile(r"^:?-{3,}:?$")
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`+[^`\n]*`+")
_LINK_RE = re.compile(r"!?\[([^\]]+)\]\([^)]*\)")
_HTML_TAG_RE = re.compile(r"</?[A-Za-z][^>]*>")
_FORMULA_RE = re.compile(r"\$[^$]+\$")
_JAPANESE_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")
_CLAUSE_DEPENDENCIES = frozenset(
    {"advcl", "acl", "ccomp", "xcomp", "conj", "parataxis"}
)
_SENTENCE_TERMINATORS = ("。", "！", "？", "!", "?", ".")


def _clean_inline_markdown(text: str) -> str:
    text = _HTML_COMMENT_RE.sub(" ", text)
    text = _INLINE_CODE_RE.sub(" ", text)
    text = _LINK_RE.sub(r"\1", text)
    text = _FORMULA_RE.sub(" ", text)
    text = _HTML_TAG_RE.sub(" ", text)
    text = re.sub(r"<!--.*$", " ", text)
    text = re.sub(r"`|\*\*?|__?", "", text)
    return text


def _extract_table_cells(line: str, line_number: int) -> list[ProseBlock]:
    cells = line.strip().strip("|").split("|")
    blocks: list[ProseBlock] = []
    for cell in cells:
        cleaned = _clean_inline_markdown(cell).strip()
        if cleaned and not _TABLE_RULE_RE.fullmatch(cleaned):
            blocks.append(ProseBlock(cleaned, line_number))
    return blocks


def _extract_prose_blocks(content: str) -> list[ProseBlock]:
    lines = content.splitlines()
    blocks: list[ProseBlock] = []
    current_lines: list[str] = []
    current_line = 1
    in_fence = False
    fence_character = ""
    fence_length = 0
    in_comment = False
    in_front_matter = bool(lines and lines[0].strip() == "---")
    front_matter_ended = not in_front_matter

    def flush() -> None:
        nonlocal current_lines
        if current_lines:
            text = "\n".join(current_lines).strip()
            if text:
                blocks.append(ProseBlock(text, current_line))
        current_lines = []

    for index, source_line in enumerate(lines):
        line_number = index + 1
        if not front_matter_ended:
            if line_number > 1 and source_line.strip() == "---":
                front_matter_ended = True
            continue

        fence_match = _FENCE_RE.match(source_line)
        if fence_match:
            marker = fence_match.group(1)
            if not in_fence:
                flush()
                in_fence = True
                fence_character = marker[0]
                fence_length = len(marker)
            elif marker[0] == fence_character and len(marker) >= fence_length:
                in_fence = False
            continue
        if in_fence:
            continue

        line = source_line
        if in_comment:
            if "-->" not in line:
                continue
            line = line.split("-->", 1)[1]
            in_comment = False
        if "<!--" in line:
            before, after = line.split("<!--", 1)
            if "-->" in after:
                line = before + after.split("-->", 1)[1]
            else:
                line = before
                in_comment = True

        if _HEADING_RE.match(line):
            flush()
            continue
        if _TABLE_RE.match(line):
            flush()
            blocks.extend(_extract_table_cells(line, line_number))
            continue
        if not line.strip() or re.fullmatch(r"\s{0,3}(?:-{3,}|\*{3,}|_{3,})\s*", line):
            flush()
            continue

        list_match = _LIST_RE.match(line)
        if list_match:
            flush()
            current_line = line_number
            cleaned = _clean_inline_markdown(list_match.group(2)).strip()
            if cleaned:
                current_lines.append(cleaned)
            continue

        cleaned = _clean_inline_markdown(re.sub(r"^\s{0,3}>\s?", "", line)).strip()
        if not cleaned:
            continue
        if not current_lines:
            current_line = line_number
        current_lines.append(cleaned)

    flush()
    return [block for block in blocks if _JAPANESE_RE.search(block.text)]


@lru_cache(maxsize=1)
def _load_ginza() -> _JapaneseNLP:
    import spacy

    return spacy.load("ja_ginza")


class ProseReadabilityCheck(AntiSabotageCheck):
    """GiNZAで長文・複雑な節連結の候補を警告する。"""

    rule_code = "PROSE-REVIEW-CANDIDATE"
    name = "可読性レビュー候補"
    gate = "Readability"
    severity = "WARNING"
    description = "長文または節連結が多い日本語文をレビュー候補として報告する。"

    def __init__(self, pipeline: _JapaneseNLP | None = None) -> None:
        self._pipeline = pipeline

    def check(self, ctx: AntiSabotageContext) -> list[VerificationIssue]:
        document_blocks = [
            (document, _extract_prose_blocks(document.content))
            for document in ctx.documents
        ]
        document_blocks = [(doc, blocks) for doc, blocks in document_blocks if blocks]
        if not document_blocks:
            return []

        try:
            pipeline = self._pipeline or _load_ginza()
        except (ImportError, OSError) as error:
            first_document = document_blocks[0][0]
            return [
                VerificationIssue(
                    gate=self.gate,
                    severity=self.severity,
                    file_path=first_document.file_path,
                    line=1,
                    rule_code="PROSE-GINZA-UNAVAILABLE",
                    message=(
                        "GiNZAの解析モデルを読み込めないため文章検査を実行できない。"
                        f"開発用のprose依存を確認する。詳細: {error}"
                    ),
                )
            ]

        issues: list[VerificationIssue] = []
        for document, blocks in document_blocks:
            issues.extend(
                self._check_document(document, blocks, pipeline, ctx.config.prose_readability)
            )
        return issues

    def _check_document(
        self,
        document: ParsedDocument,
        blocks: list[ProseBlock],
        pipeline: _JapaneseNLP,
        thresholds: ProseReadabilityConfig,
    ) -> list[VerificationIssue]:
        parts: list[str] = []
        ranges: list[tuple[int, int, ProseBlock]] = []
        starts: list[int] = []
        offset = 0
        for block in blocks:
            text = block.text.strip()
            if not text:
                continue
            starts.append(offset)
            end = offset + len(text)
            ranges.append((offset, end, block))
            needs_terminator = not text.endswith(_SENTENCE_TERMINATORS)
            parts.append(text + ("。" if needs_terminator else ""))
            offset = end + int(needs_terminator) + 2

        combined_text = "\n\n".join(parts)
        parsed = pipeline(combined_text)
        issues: list[VerificationIssue] = []
        for sentence in parsed.sents:
            range_index = bisect_right(starts, sentence.start_char) - 1
            if range_index < 0:
                continue
            start, end, block = ranges[range_index]
            if sentence.start_char >= end:
                continue
            text = combined_text[sentence.start_char : min(sentence.end_char, end)].strip()
            character_count = sum(not character.isspace() for character in text)
            clause_links = sum(
                token.dep_.split(":", 1)[0] in _CLAUSE_DEPENDENCIES
                for token in sentence
            )
            guidance: list[str] = []
            if character_count > thresholds.max_sentence_characters:
                guidance.append(
                    "一文一義を基本とし、文長は60〜80文字を目安に、"
                    "最大100文字以内で句点を打つ。"
                )
            if (
                character_count >= thresholds.complex_sentence_min_characters
                and clause_links >= thresholds.min_clause_links
            ):
                guidance.append(
                    "接続助詞で節を数珠つなぎにせず、1文の命題を原則1つにする。"
                    "前提・動機・不変条件・例外・検証エビデンスは、"
                    "後続文または箇条書きへ分ける。"
                )
            if not guidance:
                continue
            guidance.append("情報を削らずに分割する。")

            relative_offset = max(0, sentence.start_char - start)
            source_line = block.line + block.text[:relative_offset].count("\n")
            issues.append(
                VerificationIssue(
                    gate=self.gate,
                    severity=self.severity,
                    file_path=document.file_path,
                    line=source_line,
                    rule_code=self.rule_code,
                    message=f"文章の見直し候補。{' '.join(guidance)}",
                )
            )
        return issues
