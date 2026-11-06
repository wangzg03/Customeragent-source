"""Ollama native structured-output client, bounded retries, no fabricated replies."""

import time
import hashlib
import json
import httpx
from backend.customer_agent.core.schemas import Action


class ModelError(RuntimeError):
    pass


class Ollama:
    def __init__(self, settings, model, seed=42):
        if model not in settings.models:
            raise ValueError("model not allowlisted")
        self.settings = settings
        self.model = model
        self.seed = seed
        self.calls = []
        self.allowed_tools = None
        self.end_reasons = None
        self.allowed_texts = None
        self.client = httpx.Client(
            base_url=settings.ollama_url,
            timeout=httpx.Timeout(settings.model_timeout, connect=5),
            trust_env=False,
        )

    def close(self):
        self.client.close()

    def generate(self, messages):
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "think": False,
            "format": Action.generation_schema(
                self.allowed_tools, self.end_reasons, self.allowed_texts
            ),
            "options": {
                "temperature": 0.2,
                "seed": self.seed,
                "num_ctx": 8192,
                "num_predict": 256,
            },
            "keep_alive": "10m",
        }
        attempts = []
        started = time.perf_counter()
        for attempt in range(2):
            try:
                response = self.client.post("/api/chat", json=payload)
                if response.status_code in {429, 502, 503, 504} and attempt == 0:
                    attempts.append({"error": f"http_{response.status_code}"})
                    time.sleep(0.5)
                    continue
                response.raise_for_status()
                data = response.json()
                raw = data.get("message", {}).get("content", "")
                record = {
                    "latency_ms": round((time.perf_counter() - started) * 1000, 3),
                    "prompt_tokens": data.get("prompt_eval_count", 0),
                    "completion_tokens": data.get("eval_count", 0),
                    "load_ms": data.get("load_duration", 0) / 1e6,
                    "raw": raw,
                    "attempts": attempts,
                    "done_reason": data.get("done_reason"),
                }
                self.calls.append(record)
                record["schema_sha256"] = hashlib.sha256(
                    json.dumps(
                        payload["format"], sort_keys=True, ensure_ascii=False
                    ).encode()
                ).hexdigest()
                record["approved_texts"] = (
                    list(self.allowed_texts) if self.allowed_texts is not None else None
                )
                if data.get("done_reason") == "length":
                    raise ModelError("generation_truncated")
                try:
                    chosen = Action.model_validate_json(raw).model_dump()
                    if self.allowed_texts is not None and (
                        chosen["tool"] != "speak"
                        or chosen["arguments"]["text"] not in self.allowed_texts
                    ):
                        raise ValueError("speech_text_outside_approved_choices")
                    return chosen
                except ValueError as exc:
                    record["error"] = "invalid_structured_output"
                    record["validation_error"] = str(exc)[:4000]
                    raise ModelError("invalid_structured_output") from exc
            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                attempts.append({"error": type(exc).__name__})
                if attempt == 0:
                    time.sleep(0.5)
                    continue
                self.calls.append(
                    {
                        "latency_ms": round((time.perf_counter() - started) * 1000, 3),
                        "error": "model_unavailable",
                        "attempts": attempts,
                        "raw": None,
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                    }
                )
                raise ModelError("model_unavailable") from exc
            except (httpx.HTTPStatusError, ValueError) as exc:
                self.calls.append(
                    {
                        "latency_ms": round((time.perf_counter() - started) * 1000, 3),
                        "error": "model_http_or_json_error",
                        "raw": None,
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                    }
                )
                raise ModelError("model_http_or_json_error") from exc
        raise ModelError("retry_exhausted")
