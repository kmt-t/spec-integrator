from __future__ import annotations

import os
import time

import requests

from spec_integrator.config import Config

RETRIES = 3
RETRY_SLEEP_SECONDS = 2
SYSTEM_ONE_BACKENDS = ("jev", "nimble")
BACKEND_LABELS = {"jev": "Jev", "nimble": "Nimble"}
BACKEND_DEFAULT_MODELS = {"jev": "typesafe/jev-1.13", "nimble": "nimble"}


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
    backend: str = "jev",
) -> dict:
    """Submit a typed checksheet through a configured System One-compatible backend."""
    if backend not in SYSTEM_ONE_BACKENDS:
        raise ValueError(f"Unsupported System One backend: '{backend}'")

    b_config = config.llm_judge.backends.get(backend)
    if b_config is None and backend != "jev":
        raise ValueError(f"System One backend '{backend}' is not configured.")

    api_key_env = (
        b_config.api_key_env if b_config and b_config.api_key_env else "OPENROUTER_API_KEY"
    )
    requires_api_key = b_config.requires_api_key if b_config else True
    api_key = os.environ.get(api_key_env, "") if requires_api_key else ""
    if requires_api_key and not api_key:
        raise ValueError(
            f"API key environment variable '{api_key_env}' is not set for '{backend}'."
        )

    selected_model = model or (
        b_config.model if (b_config and b_config.model) else BACKEND_DEFAULT_MODELS[backend]
    )
    default_endpoint = "https://openrouter.ai/api/alpha/decisions" if backend == "jev" else ""
    endpoint = b_config.endpoint if (b_config and b_config.endpoint) else default_endpoint
    if not endpoint:
        raise ValueError(f"System One endpoint is not configured for '{backend}'.")

    headers = {"Content-Type": "application/json"}
    if requires_api_key:
        headers["Authorization"] = f"Bearer {api_key}"
        headers["HTTP-Referer"] = getattr(
            config.project, "url", "https://github.com/spec-integrator"
        )
        headers["X-Title"] = f"{config.project.name} Spec Integrator"
    payload = {"model": selected_model, "state": state, "questions": questions}

    last_err: Exception | None = None
    for attempt in range(RETRIES):
        try:
            resp = requests.post(endpoint, json=payload, headers=headers, timeout=90)
            if resp.status_code != 200:
                if 400 <= resp.status_code < 500 and resp.status_code != 429:
                    raise LLMBackendError(
                        f"{BACKEND_LABELS[backend]} API returned status {resp.status_code}: {resp.text}"
                    )
                raise RuntimeError(
                    f"{BACKEND_LABELS[backend]} API returned status {resp.status_code}: {resp.text}"
                )
            data = resp.json()
            if not isinstance(data, dict) or not isinstance(data.get("answers"), dict):
                raise ValueError(f"{BACKEND_LABELS[backend]} API response has no 'answers' object")
            return data
        except LLMBackendError:
            raise
        except Exception as e:
            last_err = e
            if attempt < RETRIES - 1:
                time.sleep(RETRY_SLEEP_SECONDS)

    raise LLMBackendError(
        f"Failed to call {BACKEND_LABELS[backend]} API after {RETRIES} attempts: {last_err}"
    ) from last_err
