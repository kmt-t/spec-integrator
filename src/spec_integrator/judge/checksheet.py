from __future__ import annotations

from dataclasses import dataclass

from spec_integrator.config import Config
from spec_integrator.judge.llm_backend import call_system_one


@dataclass(frozen=True)
class Checksheet:
    """A named set of typed System One questions over one evidence unit."""

    name: str
    state: dict[str, object]
    questions: dict[str, dict[str, object]]

    def payload(self) -> dict[str, object]:
        return {"state": self.state, "questions": self.questions}


def submit_checksheet(config: Config, sheet: Checksheet, model: str | None = None) -> dict:
    """Submit one checksheet through the configured System One endpoint."""
    return call_system_one(config, sheet.state, sheet.questions, model)
