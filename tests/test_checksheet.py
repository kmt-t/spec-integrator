from unittest.mock import Mock, patch

from spec_integrator.config import Config, LLMBackendConfig, LLMCheckRule
from spec_integrator.judge.checksheet import Checksheet, submit_checksheet
from spec_integrator.judge.llm_backend import call_ollama_embeddings
from spec_integrator.judge.unified_reviewer import UnifiedReviewEngine
from spec_integrator.models import ParsedDocument, ParsedSection


def test_checksheet_uses_system_one_decisions_api(monkeypatch):
    config = Config()
    config.llm_judge.backends["jev"] = LLMBackendConfig(
        api_key_env="TEST_OPENROUTER_KEY",
        endpoint="https://openrouter.ai/api/alpha/decisions",
        model="typesafe/jev-1.13",
    )
    monkeypatch.setenv("TEST_OPENROUTER_KEY", "test-key")
    sheet = Checksheet(
        name="example",
        state={"text": "sample"},
        questions={
            "result": {
                "type": "choice",
                "instructions": "Classify the text.",
                "criteria": {"pass": "Acceptable", "fail": "Defective"},
            }
        },
    )
    response = Mock(status_code=200)
    response.json.return_value = {"answers": {"result": {"type": "choice", "choice": "pass"}}}

    with patch("spec_integrator.judge.llm_backend.requests.post", return_value=response) as post:
        result = submit_checksheet(config, sheet)

    assert result == response.json.return_value
    assert post.call_args.args == ("https://openrouter.ai/api/alpha/decisions",)
    assert post.call_args.kwargs["json"] == {
        "model": "typesafe/jev-1.13",
        "state": sheet.state,
        "questions": sheet.questions,
    }
    assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer test-key"


def test_clef_flash_uses_local_system_one_without_api_key():
    config = Config()
    config.llm_judge.backends["clef-flash"] = LLMBackendConfig(
        endpoint="http://localhost:11434/v1/systemone",
        model="clef-flash",
        requires_api_key=False,
    )
    sheet = Checksheet(
        name="example",
        state={"text": "sample"},
        questions={
            "result": {
                "type": "choice",
                "instructions": "Classify the text.",
                "criteria": {"pass": "Acceptable", "fail": "Defective"},
            }
        },
    )
    response = Mock(status_code=200)
    response.json.return_value = {
        "answers": {"result": {"type": "choice", "choice": "pass", "confidence": 0.8}}
    }

    with patch("spec_integrator.judge.llm_backend.requests.post", return_value=response) as post:
        result = submit_checksheet(config, sheet, backend="clef-flash")

    assert result == response.json.return_value
    assert post.call_args.args == ("http://localhost:11434/v1/systemone",)
    assert post.call_args.kwargs["json"] == {
        "model": "clef-flash",
        "state": sheet.state,
        "questions": sheet.questions,
    }
    assert "Authorization" not in post.call_args.kwargs["headers"]


def test_embeddings_use_ollama_batches():
    config = Config()
    config.embeddings.endpoint = "http://localhost:11434/"
    first = Mock(status_code=200)
    first.json.return_value = {"embeddings": [[1.0, 0.0], [0.0, 1.0]]}
    second = Mock(status_code=200)
    second.json.return_value = {"embeddings": [[0.5, 0.5]]}

    with patch(
        "spec_integrator.judge.llm_backend.requests.post", side_effect=[first, second]
    ) as post:
        vectors = call_ollama_embeddings(config, ["a", "b", "c"], "qwen3-embedding", 2)

    assert vectors == [[1.0, 0.0], [0.0, 1.0], [0.5, 0.5]]
    assert post.call_count == 2
    assert post.call_args_list[0].args == ("http://localhost:11434/api/embed",)
    assert post.call_args_list[0].kwargs["json"] == {
        "model": "qwen3-embedding",
        "input": ["a", "b"],
    }
    assert post.call_args_list[1].kwargs["json"]["input"] == ["c"]


def test_review_maps_checksheet_decision_to_failure():
    config = Config()
    config.llm_judge.checks = [
        LLMCheckRule(
            id="visible_defect", name="Visible defect", mode=["single"], prompt="Check it."
        )
    ]
    section = ParsedSection(
        section_id="sec:sample.md#Meaning",
        file_path="sample.md",
        heading="Meaning",
        level=2,
        line_start=2,
        line_end=3,
        body_text="A visible defect.",
    )
    doc = ParsedDocument(
        file_path="sample.md",
        full_path=None,
        tier=1,
        component="sample",
        content="## Meaning\nA visible defect.",
        content_hash="sample-hash",
        sections=[section],
    )
    answer = {
        "answers": {
            "visible_defect": {
                "type": "choice",
                "choice": "confirmed_violation",
                "confidence": 0.9,
            }
        }
    }

    with patch("spec_integrator.judge.checksheet.call_system_one", return_value=answer) as call:
        result = UnifiedReviewEngine(config).review_single_document(doc, backend="jev")

    assert result.status == "FAIL"
    assert result.evaluations[0].classification == "confirmed_violation"
    assert result.issues[0]["check_id"] == "visible_defect"
    assert "visible_defect" in call.call_args.args[2]


def test_clef_flash_review_labels_its_native_confidence_metric():
    config = Config()
    config.llm_judge.backends["clef-flash"] = LLMBackendConfig(
        endpoint="http://localhost:11434/v1/systemone",
        model="clef-flash",
        requires_api_key=False,
    )
    config.llm_judge.checks = [
        LLMCheckRule(id="visible_defect", name="Visible defect", mode=["single"])
    ]
    section = ParsedSection(
        section_id="sec:sample.md#Meaning",
        file_path="sample.md",
        heading="Meaning",
        level=2,
        line_start=2,
        line_end=3,
        body_text="A visible defect.",
    )
    doc = ParsedDocument(
        file_path="sample.md",
        full_path=None,
        tier=1,
        component="sample",
        content="## Meaning\nA visible defect.",
        content_hash="sample-hash",
        sections=[section],
    )
    answer = {
        "answers": {
            "visible_defect": {
                "type": "choice",
                "choice": "possible_violation",
                "confidence": 0.82,
            }
        }
    }

    with patch("spec_integrator.judge.checksheet.call_system_one", return_value=answer) as call:
        result = UnifiedReviewEngine(config).review_single_document(doc, backend="clef-flash")

    assert result.status == "WARN"
    assert "Clef Flash classified" in result.issues[0]["description"]
    assert "probability concentration 82%" in result.issues[0]["description"]
    assert "Jev" not in result.issues[0]["description"]
    assert call.call_args.args[4] == "clef-flash"


def test_clef_flash_review_evidence_budget_fits_configured_context_window():
    config = Config()
    config.llm_judge.backends["clef-flash"] = LLMBackendConfig(
        endpoint="http://localhost:11434/v1/systemone",
        model="clef-flash",
        requires_api_key=False,
        context_window_tokens=8192,
    )
    reviewer = UnifiedReviewEngine(config)

    assert reviewer._section_content_budget("clef-flash", 1) == 3000
    assert reviewer._section_content_budget("clef-flash", 2) == 1500
    assert reviewer._section_content_budget("jev", 1) is None
    assert reviewer._checks_per_request("clef-flash", 5) == 2
    assert reviewer._checks_per_request("jev", 5) == 5
    limited = reviewer._budgeted("evidence " * 1000, 3000)
    assert len(limited) > 3000
    assert "[TRUNCATED:" in limited

    config.llm_judge.section_char_budget = 0
    config.llm_judge.backends["clef-flash"].context_window_tokens = 16384
    assert reviewer._section_content_budget("clef-flash", 1) == 6000
    assert reviewer._checks_per_request("clef-flash", 5) == 5


def test_clef_flash_review_splits_checks_into_context_bounded_requests():
    config = Config()
    config.llm_judge.checks = [
        LLMCheckRule(id=f"check_{index}", name=f"Check {index}", mode=["single"])
        for index in range(5)
    ]
    section = ParsedSection(
        section_id="sec:sample.md#Meaning",
        file_path="sample.md",
        heading="Meaning",
        level=2,
        line_start=2,
        line_end=3,
        body_text="Evidence is present.",
    )
    document = ParsedDocument(
        file_path="sample.md",
        full_path=None,
        tier=1,
        component="sample",
        content="## Meaning\nEvidence is present.",
        content_hash="sample-hash",
        sections=[section],
    )
    reviewer = UnifiedReviewEngine(config)
    no_issue = {"type": "choice", "choice": "no_issue", "confidence": 0.9}
    responses = [
        {"answers": {"check_0": no_issue, "check_1": no_issue}},
        {"answers": {"check_2": no_issue, "check_3": no_issue}},
        {"answers": {"check_4": no_issue}},
    ]

    with patch.object(reviewer, "_submit_checksheet", side_effect=responses) as submit:
        result = reviewer.review_single_document(document, backend="clef-flash")

    assert result.status == "PASS"
    assert len(result.evaluations) == 5
    submitted_questions = [list(call.args[0].questions) for call in submit.call_args_list]
    assert [len(batch) for batch in submitted_questions] == [2, 2, 1]
    assert [check_id for batch in submitted_questions for check_id in batch] == [
        f"check_{index}" for index in range(5)
    ]
