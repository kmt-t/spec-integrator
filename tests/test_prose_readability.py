from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from spec_integrator.anti_sabotage.base import AntiSabotageContext
from spec_integrator.anti_sabotage.checks.prose_readability import (
    ProseReadabilityCheck,
    _extract_prose_blocks,
)
from spec_integrator.config import Config, ProseReadabilityConfig
from spec_integrator.models import ParsedDocument


@dataclass(frozen=True, slots=True)
class FakeToken:
    dep_: str


@dataclass(frozen=True, slots=True)
class FakeSentence:
    text: str
    start_char: int
    dependencies: tuple[str, ...]

    @property
    def end_char(self) -> int:
        return self.start_char + len(self.text)

    def __iter__(self) -> Iterator[FakeToken]:
        return iter(tuple(FakeToken(dependency) for dependency in self.dependencies))


@dataclass(frozen=True, slots=True)
class FakeDoc:
    sents: tuple[FakeSentence, ...]


class FakePipeline:
    def __init__(self, sentences: tuple[FakeSentence, ...]) -> None:
        self.sentences = sentences

    def __call__(self, _text: str) -> FakeDoc:
        return FakeDoc(self.sentences)


def _context(content: str) -> AntiSabotageContext:
    document = ParsedDocument(
        file_path="components/tier3_executer/interpreter.md",
        full_path=Path("interpreter.md"),
        tier=2,
        component="interpreter",
        content=content,
        content_hash="test-hash",
    )
    return AntiSabotageContext(
        documents=[document], graph=None, docs_root=Path("docs"), config=Config()
    )


def test_extract_prose_skips_code_comments_and_headings_but_keeps_table_cells():
    content = """---
title: test
---
# 見出し
通常の説明文である。

<!-- 隠すべきコメントである。 -->
```python
これは解析対象外である。
```
| 項目 | 表の説明文である。 |
| :--- | :--- |
"""

    blocks = _extract_prose_blocks(content)

    assert [block.text for block in blocks] == ["通常の説明文である。", "項目", "表の説明文である。"]
    assert [block.line for block in blocks] == [5, 11, 11]


def test_long_sentence_is_reported_as_warning_with_source_line():
    first = "短い説明である。"
    second = "あ" * 101 + "。"
    content = f"{first}\n\n{second}"
    separator_length = len(first) + 2
    pipeline = FakePipeline(
        (
            FakeSentence(first, 0, ()),
            FakeSentence(second, separator_length, ()),
        )
    )

    issues = ProseReadabilityCheck(pipeline).check(_context(content))

    assert len(issues) == 1
    assert issues[0].severity == "WARNING"
    assert issues[0].rule_code == "PROSE-REVIEW-CANDIDATE"
    assert issues[0].line == 3
    assert "60〜80文字を目安" in issues[0].message
    assert "最大100文字以内" in issues[0].message
    assert "102文字" not in issues[0].message


def test_clause_chain_is_reported_but_short_simple_sentence_is_not():
    sentence = "あ" * 80 + "。"
    content = f"{sentence}\n\n短い説明である。"
    pipeline = FakePipeline(
        (
            FakeSentence(sentence, 0, ("advcl", "conj", "ccomp")),
            FakeSentence("短い説明である。", len(sentence) + 2, ()),
        )
    )

    context = _context(content)
    context.config.prose_readability = ProseReadabilityConfig(
        complex_sentence_min_characters=70,
        min_clause_links=3,
    )

    issues = ProseReadabilityCheck(pipeline).check(context)

    assert len(issues) == 1
    assert issues[0].severity == "WARNING"
    assert issues[0].line == 1
    assert "接続助詞で節を数珠つなぎにせず" in issues[0].message
    assert "前提・動機・不変条件・例外・検証エビデンス" in issues[0].message
    assert "情報を削らずに分割する" in issues[0].message


def test_configured_thresholds_control_warning_detection():
    sentence = "あ" * 51 + "。"
    content = sentence
    pipeline = FakePipeline((FakeSentence(sentence, 0, ()),))
    context = _context(content)
    context.config.prose_readability = ProseReadabilityConfig(max_sentence_characters=50)

    issues = ProseReadabilityCheck(pipeline).check(context)

    assert len(issues) == 1
    assert "60〜80文字を目安" in issues[0].message
