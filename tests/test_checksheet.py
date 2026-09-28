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
