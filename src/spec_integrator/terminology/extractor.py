from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from spec_integrator.config import Config
    from spec_integrator.db import DocAuditDB
from spec_integrator.config import DEFAULT_STOPWORDS

if TYPE_CHECKING:
    from spec_integrator.config import Config
    from spec_integrator.db import DocAuditDB
    from spec_integrator.models import ParsedDocument

# Regex patterns for terminology extraction
KATAKANA_PATTERN = re.compile(r"[\u30A1-\u30FA\u30FC]+(?:[・\u30FB][\u30A1-\u30FA\u30FC]+)*")
KANJI_PATTERN = re.compile(r"[\u4E00-\u9FFF]{2,}")
MIXED_PATTERN = re.compile(
    r"[\u30A1-\u30FA\u30FC]+[\u4E00-\u9FFF]+|[\u4E00-\u9FFF]+[\u30A1-\u30FA\u30FC]+"
)
ENGLISH_PATTERN = re.compile(r"\b[A-Za-z][A-Za-z0-9_]{2,}\b")
SPEC_KEYWORD_PATTERN = re.compile(r"\{([A-Za-z0-9_]+)\}")


def clean_markdown_text(text: str) -> str:
    """Removes code blocks, inline code, HTML comments, and markdown links."""
    # Remove fenced code blocks
    text = re.sub(r"```[\s\S]*?```", " ", text)
    # Remove HTML comments
    text = re.sub(r"<!--[\s\S]*?-->", " ", text)
    # Remove inline code
    text = re.sub(r"`[^`\n]+`", " ", text)
    # Remove markdown link URLs, keep link text: [text](url) -> text
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    # Remove image links: ![alt](url)
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", text)
    # Remove headings marker
    text = re.sub(r"^#+\s+", "", text, flags=re.MULTILINE)
    return text


def extract_terms_from_text(text: str, stopwords: set[str] | None = None) -> list[tuple[str, str]]:
    """Extracts candidate terms along with their categories."""
    sw = stopwords if stopwords is not None else set(DEFAULT_STOPWORDS)
    terms: list[tuple[str, str]] = []

    # Spec keywords
    for m in SPEC_KEYWORD_PATTERN.finditer(text):
        kw = m.group(1)
        if len(kw) >= 3:
            terms.append((kw, "spec_keyword"))

    cleaned = clean_markdown_text(text)

    # Katakana terms (e.g. ハイパーバイザ, ハイパーバイザー, コンテキストスイッチ)
    for m in KATAKANA_PATTERN.finditer(cleaned):
        word = m.group(0)
        if len(word) >= 2 and word not in sw:
            terms.append((word, "katakana"))

    # Mixed Katakana + Kanji terms (e.g. 割込ハンドラ, メモリ管理)
    for m in MIXED_PATTERN.finditer(cleaned):
        word = m.group(0)
        if len(word) >= 3 and word not in sw:
            terms.append((word, "mixed"))

    # Kanji compound terms (e.g. 割込, 割り込み, 排他制御, 仮想化)
    for m in KANJI_PATTERN.finditer(cleaned):
        word = m.group(0)
        if len(word) >= 2 and word not in sw:
            terms.append((word, "kanji"))

    # English identifiers and technical words
    for m in ENGLISH_PATTERN.finditer(cleaned):
        word = m.group(0)
        lower_word = word.lower()
        if len(word) >= 3 and lower_word not in sw:
            terms.append((word, "english"))

    return terms


class TermExtractor:
    """Extracts keywords and calculates TF-IDF across specification documents."""

    def __init__(self, config: Config):
        self.config = config
        self.stopwords = set(getattr(config.terminology, "stopwords", None) or DEFAULT_STOPWORDS)

    def extract_and_save(self, documents: list[ParsedDocument], db: DocAuditDB) -> int:
        """Extracts candidate terms using TF-IDF across all documents and persists to DB."""
        if not documents:
            return 0

        # doc_id -> list of terms
        doc_term_counts: dict[str, Counter[str]] = {}
        term_categories: dict[str, str] = {}
        term_occurrences: dict[str, list[dict]] = defaultdict(list)

        for doc in documents:
            doc_terms: list[str] = []
            for sec in doc.sections:
                sec_terms = extract_terms_from_text(sec.body_text, self.stopwords)
                for term, cat in sec_terms:
                    if len(term) < 2 or len(term) > 35:
                        continue
                    term_categories[term] = cat
                    doc_terms.append(term)
                    # Record occurrence (limit occurrences per term per section to 1 to avoid explosion)
                    occurrences = term_occurrences[term]
                    if not any(
                        occ["file_path"] == doc.file_path and occ["section_id"] == sec.section_id
                        for occ in occurrences
                    ):
                        # Extract a snippet around the term
                        idx = sec.body_text.find(term)
                        start = max(0, idx - 40)
                        end = min(len(sec.body_text), idx + len(term) + 40)
                        snippet = sec.body_text[start:end].replace("\n", " ").strip()
                        occurrences.append(
                            {
                                "file_path": doc.file_path,
                                "section_id": sec.section_id,
                                "heading": sec.heading,
                                "line_start": sec.line_start,
                                "snippet": snippet,
                            }
                        )

            doc_term_counts[doc.file_path] = Counter(doc_terms)

        n_docs = len(documents)
        df: Counter[str] = Counter()
        for counter in doc_term_counts.values():
            for term in counter.keys():
                df[term] += 1

        # Calculate TF-IDF
        term_scores: dict[str, float] = {}
        total_occurrences: Counter[str] = Counter()

        for _doc_path, counter in doc_term_counts.items():
            total_words = sum(counter.values()) or 1
            for term, count in counter.items():
                total_occurrences[term] += count
                tf = count / total_words
                idf = math.log((n_docs + 1) / (df[term] + 1)) + 1.0
                score = tf * idf
                term_scores[term] = max(term_scores.get(term, 0.0), score)

        # Filter and rank terms
        # Keep terms that appear in at least 1 document and have total occurrences >= 1
        filtered_terms = [
            term for term, count in total_occurrences.items() if count >= 1 and len(term) >= 2
        ]

        max_terms = getattr(self.config.terminology, "max_terms", 500)
        # Sort by total occurrences and TF-IDF score
        filtered_terms.sort(
            key=lambda t: (total_occurrences[t], term_scores.get(t, 0.0)), reverse=True
        )
        selected_terms = filtered_terms[:max_terms]

        term_records = [
            {
                "term": t,
                "category": term_categories.get(t, "general"),
                "df": df[t],
                "total_occurrences": total_occurrences[t],
                "tf_idf_score": term_scores.get(t, 0.0),
                "occurrences": term_occurrences[t],
            }
            for t in selected_terms
        ]

        db.replace_term_keywords(term_records)
        db.commit()
        return len(term_records)
