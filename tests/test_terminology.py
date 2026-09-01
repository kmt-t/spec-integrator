from __future__ import annotations

from pathlib import Path

import pytest
from spec_integrator.config import Config
from spec_integrator.db import DocAuditDB
from spec_integrator.models import ParsedDocument, ParsedSection
from spec_integrator.terminology import (
    TermExtractor,
    TermIndexer,
    TermVarianceJudge,
    cosine_similarity,
    extract_terms_from_text,
)


def test_extract_terms_from_text():
    text = """
    # ハイパーバイザ概要 {META_CORE}
    Fireball は高信頼なハイパーバイザーです。
    コンテキストスイッチおよびコンテキスト切替を高速化します。
    割り込み処理と割込ハンドラをサポートします。
    ```c
    void context_switch();
    ```
    詳細については [リンク](doc.md) を参照のこと。
    """
    terms = extract_terms_from_text(text)
    term_words = [t[0] for t in terms]

    # Check key terms
    assert "ハイパーバイザ" in term_words or "ハイパーバイザー" in term_words
    assert "META_CORE" in term_words
    assert "コンテキストスイッチ" in term_words
    assert "コンテキスト切替" in term_words
    assert "割り込み処理" in term_words or "割込ハンドラ" in term_words
    # Check custom stopwords
    custom_sw = {"ハイパーバイザー", "コンテキストスイッチ"}
    terms_filtered = extract_terms_from_text(text, stopwords=custom_sw)
    words_filtered = [t[0] for t in terms_filtered]
    assert "ハイパーバイザー" not in words_filtered
    assert "コンテキストスイッチ" not in words_filtered


def test_term_extractor_tfidf():
    config = Config()
    db = DocAuditDB(":memory:")

    doc1 = ParsedDocument(
        file_path="docs/doc1.md",
        full_path=Path("docs/doc1.md"),
        tier=1,
        component="core",
        content="",
        content_hash="hash1",
        sections=[
            ParsedSection(
                section_id="sec:doc1#1",
                file_path="docs/doc1.md",
                heading="コア設計",
                level=1,
                line_start=1,
                line_end=10,
                body_text="ハイパーバイザは仮想マシンを管理する。",
            )
        ],
    )

    doc2 = ParsedDocument(
        file_path="docs/doc2.md",
        full_path=Path("docs/doc2.md"),
        tier=2,
        component="runtime",
        content="",
        content_hash="hash2",
        sections=[
            ParsedSection(
                section_id="sec:doc2#1",
                file_path="docs/doc2.md",
                heading="ランタイム設計",
                level=1,
                line_start=1,
                line_end=10,
                body_text="ハイパーバイザーによる仮想マシンのスケジューリング。",
            )
        ],
    )

    extractor = TermExtractor(config)
    count = extractor.extract_and_save([doc1, doc2], db)
    assert count > 0

    all_terms = db.get_all_term_keywords()
    term_names = [r["term"] for r in all_terms]
    assert "ハイパーバイザ" in term_names or "ハイパーバイザー" in term_names


def test_cosine_similarity():
    v1 = [1.0, 0.0, 0.0]
    v2 = [1.0, 0.0, 0.0]
    assert cosine_similarity(v1, v2) == pytest.approx(1.0)

    v3 = [0.0, 1.0, 0.0]
    assert cosine_similarity(v1, v3) == pytest.approx(0.0)

    v4 = [1.0, 1.0, 0.0]
    assert cosine_similarity(v1, v4) == pytest.approx(0.7071, abs=1e-3)


def test_term_indexer_similarities():
    config = Config()
    config.terminology.similarity_threshold = 0.80
    db = DocAuditDB(":memory:")

    # Insert fake terms
    db.replace_term_keywords(
        [
            {
                "term": "ハイパーバイザ",
                "category": "katakana",
                "df": 1,
                "total_occurrences": 2,
                "tf_idf_score": 1.5,
                "occurrences": [
                    {
                        "file_path": "a.md",
                        "section_id": "s1",
                        "heading": "H1",
                        "line_start": 5,
                        "snippet": "...",
                    }
                ],
            },
            {
                "term": "ハイパーバイザー",
                "category": "katakana",
                "df": 1,
                "total_occurrences": 2,
                "tf_idf_score": 1.4,
                "occurrences": [
                    {
                        "file_path": "b.md",
                        "section_id": "s2",
                        "heading": "H2",
                        "line_start": 10,
                        "snippet": "...",
                    }
                ],
            },
            {
                "term": "全く無関係な語",
                "category": "kanji",
                "df": 1,
                "total_occurrences": 1,
                "tf_idf_score": 0.5,
                "occurrences": [],
            },
        ]
    )

    # Insert embeddings
    model = "test-model"
    db.insert_term_embeddings(
        [
            ("ハイパーバイザ", [0.9, 0.1, 0.0], model),
            ("ハイパーバイザー", [0.89, 0.11, 0.0], model),
            ("全く無関係な語", [0.0, 0.0, 1.0], model),
        ]
    )

    indexer = TermIndexer(config)
    sim_count = indexer.compute_and_save_similarities(db, model=model, min_similarity=0.80)
    assert sim_count == 1

    sims = db.get_term_similarities(0.80)
    assert len(sims) == 1
    assert sims[0]["term_a"] == "ハイパーバイザ"
    assert sims[0]["term_b"] == "ハイパーバイザー"
    assert sims[0]["similarity"] > 0.95


def test_term_variance_judge_and_issues():
    config = Config()
    config.llm_judge.default_backend = "mock"
    db = DocAuditDB(":memory:")

    db.replace_term_keywords(
        [
            {
                "term": "コンテキスト切替",
                "category": "mixed",
                "df": 1,
                "total_occurrences": 2,
                "tf_idf_score": 1.2,
                "occurrences": [
                    {
                        "file_path": "docs/core.md",
                        "section_id": "sec:core#ctx",
                        "heading": "コンテキスト処理",
                        "line_start": 15,
                        "snippet": "タスクのコンテキスト切替を高速に行う。",
                    }
                ],
            },
            {
                "term": "コンテキストスイッチ",
                "category": "katakana",
                "df": 1,
                "total_occurrences": 2,
                "tf_idf_score": 1.1,
                "occurrences": [
                    {
                        "file_path": "docs/sched.md",
                        "section_id": "sec:sched#ctx",
                        "heading": "スケジューラ",
                        "line_start": 30,
                        "snippet": "コンテキストスイッチのオーバーヘッドを削減する。",
                    }
                ],
            },
        ]
    )

    db.replace_term_similarities([("コンテキスト切替", "コンテキストスイッチ", 0.92)])

    judge = TermVarianceJudge(config)
    judged_count = judge.judge_similar_pairs(db, backend="mock", max_pairs=10)
    assert judged_count == 1

    # Verify judgment in DB
    judgments = db.get_all_term_variance_judgments()
    assert len(judgments) == 1
    assert judgments[0]["is_variance"] == 1
    assert judgments[0]["confidence"] == pytest.approx(0.95)

    # Verify issue generation
    issues = judge.generate_verification_issues(db, min_confidence=0.70)
    assert len(issues) == 1
    issue = issues[0]
    assert issue.gate == "Consistency"
    assert issue.severity == "WARNING"
    assert issue.rule_code == "TERM_VARIANCE"
    assert issue.file_path == "docs/core.md"
    assert issue.line == 15
    assert "コンテキスト切替" in issue.message
    assert "コンテキストスイッチ" in issue.message
