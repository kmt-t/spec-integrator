from __future__ import annotations

import json
import os
import re
import time

import requests
import urllib3

from spec_integrator.config import Config

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

RETRIES = 3
RETRY_SLEEP_SECONDS = 2


def call_sakura(config: Config, prompt: str, model: str | None) -> str:
    b_config = config.llm_judge.backends.get("sakura")
    api_key_env = b_config.api_key_env if b_config else "SAKURA_API_KEY"
    api_key = os.environ.get(api_key_env, "")
    if not api_key:
        raise ValueError(f"Sakura API key environment variable '{api_key_env}' is not set.")
    selected_model = model or (
        b_config.model if (b_config and b_config.model) else "preview/Qwen3.6-35B-A3B"
    )
    endpoint = (
        b_config.endpoint
        if (b_config and b_config.endpoint)
        else "https://api.ai.sakura.ad.jp/v1/chat/completions"
    )
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    return _call_chat_completion(
        endpoint, headers, selected_model, prompt, timeout=60, backend_name="Sakura"
    )


def call_sakura_embeddings(
    config: Config,
    texts: list[str],
    model: str | None = None,
    batch_size: int = 32,
) -> list[list[float]]:
    """Generates embedding vectors for texts using Sakura AI Embeddings API."""
    if not texts:
        return []
    b_config = config.llm_judge.backends.get("sakura")
    api_key_env = b_config.api_key_env if b_config else "SAKURA_API_KEY"
    api_key = os.environ.get(api_key_env, "")
    if not api_key:
        raise ValueError(f"Sakura API key environment variable '{api_key_env}' is not set.")

    selected_model = model or getattr(
        config.terminology, "embedding_model", "multilingual-e5-large"
    )
    endpoint = "https://api.ai.sakura.ad.jp/v1/embeddings"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    all_embeddings: list[list[float]] = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        payload = {
            "model": selected_model,
            "input": batch,
        }
        last_err: Exception | None = None
        batch_success = False
        for _attempt in range(RETRIES):
            try:
                resp = requests.post(
                    endpoint, json=payload, headers=headers, timeout=30, verify=False
                )
                if resp.status_code != 200:
                    raise RuntimeError(
                        f"Sakura Embeddings API returned status {resp.status_code}: {resp.text}"
                    )
                data = resp.json()
                sorted_items = sorted(data["data"], key=lambda x: x["index"])
                for item in sorted_items:
                    all_embeddings.append(item["embedding"])
                batch_success = True
                break
            except Exception as e:
                last_err = e
                time.sleep(RETRY_SLEEP_SECONDS)
        if not batch_success:
            raise RuntimeError(
                f"Failed to fetch Sakura embeddings after {RETRIES} attempts: {last_err}"
            )

    return all_embeddings


def call_openrouter(config: Config, prompt: str, model: str | None) -> str:
    b_config = config.llm_judge.backends.get("openrouter")
    api_key_env = b_config.api_key_env if b_config else "OPENROUTER_API_KEY"
    api_key = os.environ.get(api_key_env, "")
    if not api_key:
        raise ValueError(f"OpenRouter API key environment variable '{api_key_env}' is not set.")
    selected_model = model or (
        b_config.model if (b_config and b_config.model) else "qwen/qwen3.8-27b"
    )
    endpoint = (
        b_config.endpoint
        if (b_config and b_config.endpoint)
        else "https://openrouter.ai/api/v1/chat/completions"
    )
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": getattr(config.project, "url", "https://github.com/spec-integrator"),
        "X-Title": f"{config.project.name} Spec Integrator",
    }
    return _call_chat_completion(
        endpoint, headers, selected_model, prompt, timeout=90, backend_name="OpenRouter"
    )


def call_ollama(config: Config, prompt: str, model: str | None) -> str:
    b_config = config.llm_judge.backends.get("ollama")
    endpoint = (
        b_config.endpoint if (b_config and b_config.endpoint) else "http://localhost:11434"
    ) + "/api/generate"
    selected_model = model or (b_config.model if (b_config and b_config.model) else "llama3")
    payload = {
        "model": selected_model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
    }
    resp = requests.post(endpoint, json=payload, timeout=60)
    if resp.status_code != 200:
        raise RuntimeError(f"Ollama API returned status {resp.status_code}: {resp.text}")
    data = resp.json()
    return data.get("response", "")


def _call_chat_completion(
    endpoint: str,
    headers: dict,
    model: str,
    prompt: str,
    timeout: int,
    backend_name: str,
) -> str:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
    }
    last_err: Exception | None = None
    for _attempt in range(RETRIES):
        try:
            resp = requests.post(
                endpoint, json=payload, headers=headers, timeout=timeout, verify=False
            )
            if resp.status_code != 200:
                raise RuntimeError(
                    f"{backend_name} API returned status {resp.status_code}: {resp.text}"
                )
            data = resp.json()
            return data["choices"][0]["message"]["content"]
        except Exception as e:
            last_err = e
            time.sleep(RETRY_SLEEP_SECONDS)
    raise RuntimeError(f"Failed to call {backend_name} API after {RETRIES} attempts: {last_err}")


def extract_json(raw_text: str) -> dict:
    """Pulls the first JSON object out of a raw LLM response, fenced code block or not."""
    code_block = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", raw_text)
    if code_block:
        json_str = code_block.group(1)
    else:
        first_brace = raw_text.find("{")
        last_brace = raw_text.rfind("}")
        if first_brace != -1 and last_brace != -1:
            json_str = raw_text[first_brace : last_brace + 1]
        else:
            json_str = raw_text

    try:
        return json.loads(json_str, strict=False)
    except json.JSONDecodeError:
        # Clean invalid backslash escapes (e.g. \k, \O, \ ) by doubling or removing unescaped backslashes
        cleaned = re.sub(r'\\([^"\\/bfnrtu])', r"\1", json_str)
        return json.loads(cleaned, strict=False)
