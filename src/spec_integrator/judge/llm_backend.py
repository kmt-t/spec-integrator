from __future__ import annotations

import os
import time

import requests

from spec_integrator.config import Config

RETRIES = 3
RETRY_SLEEP_SECONDS = 2


class LLMBackendError(RuntimeError):
    """A backend request did not produce a usable checksheet response."""


def call_ollama_embeddings(
    config: Config,
    texts: list[str],
    model: str,
    batch_size: int = 32,
) -> list[list[float]]:
    """Generates embedding vectors through a local Ollama server."""
    if not texts:
        return []
    base_url = config.embeddings.endpoint
    endpoint = f"{base_url.rstrip('/')}/api/embed"

    all_embeddings: list[list[float]] = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        last_err: Exception | None = None
        embeddings: list[list[float]] | None = None
        for attempt in range(RETRIES):
            try:
                resp = requests.post(
                    endpoint,
                    json={"model": model, "input": batch},
                    timeout=60,
                )
                if resp.status_code != 200:
                    raise RuntimeError(
                        f"Ollama Embeddings API returned status {resp.status_code}: {resp.text}"
                    )
                data = resp.json()
                candidate = data.get("embeddings")
                if not isinstance(candidate, list) or len(candidate) != len(batch):
                    raise ValueError(
                        "Ollama Embeddings API response has an invalid 'embeddings' list"
                    )
                embeddings = candidate
                break
            except Exception as e:
                last_err = e
                if attempt < RETRIES - 1:
                    time.sleep(RETRY_SLEEP_SECONDS)
        if embeddings is None:
            raise RuntimeError(
                f"Failed to fetch Ollama embeddings after {RETRIES} attempts: {last_err}"
            )
        all_embeddings.extend(embeddings)

    return all_embeddings


def call_system_one(
    config: Config,
    state: str | dict,
    questions: dict[str, dict],
    model: str | None = None,
) -> dict:
    """Submit typed checksheet questions to Jev through OpenRouter."""
    b_config = config.llm_judge.backends.get("jev")
    api_key_env = b_config.api_key_env if b_config else "OPENROUTER_API_KEY"
    api_key = os.environ.get(api_key_env, "")
    if not api_key:
        raise ValueError(f"OpenRouter API key environment variable '{api_key_env}' is not set.")

    selected_model = model or (
        b_config.model if (b_config and b_config.model) else "typesafe/jev-1.13"
    )
    endpoint = (
        b_config.endpoint
        if (b_config and b_config.endpoint)
        else "https://openrouter.ai/api/alpha/decisions"
    )
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": getattr(config.project, "url", "https://github.com/spec-integrator"),
        "X-Title": f"{config.project.name} Spec Integrator",
    }
    payload = {"model": selected_model, "state": state, "questions": questions}

    last_err: Exception | None = None
    for attempt in range(RETRIES):
        try:
            resp = requests.post(endpoint, json=payload, headers=headers, timeout=90)
            if resp.status_code != 200:
                if 400 <= resp.status_code < 500 and resp.status_code != 429:
                    raise LLMBackendError(
                        f"OpenRouter Jev API returned status {resp.status_code}: {resp.text}"
                    )
                raise RuntimeError(
                    f"OpenRouter Jev API returned status {resp.status_code}: {resp.text}"
                )
            data = resp.json()
            if not isinstance(data, dict) or not isinstance(data.get("answers"), dict):
                raise ValueError("OpenRouter Jev API response has no 'answers' object")
            return data
        except LLMBackendError:
            raise
        except Exception as e:
            last_err = e
            if attempt < RETRIES - 1:
                time.sleep(RETRY_SLEEP_SECONDS)

    raise LLMBackendError(
        f"Failed to call OpenRouter Jev API after {RETRIES} attempts: {last_err}"
    ) from last_err
