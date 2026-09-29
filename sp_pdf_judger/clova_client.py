from __future__ import annotations

import json
import re
import time
import threading
import os
import hashlib
import uuid
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

try:
    from openai import OpenAI
except Exception:  # pragma: no cover - optional until runtime dependencies are installed
    OpenAI = None


ResponseModelT = TypeVar("ResponseModelT", bound=BaseModel)
_REQUEST_LOCK = threading.Lock()
_LAST_REQUEST = 0.0


def _completion_with_backoff(client, request):
    """Respect provider throttling; a rate limit is never a judgement result."""
    global _LAST_REQUEST
    for attempt in range(5):
        with _REQUEST_LOCK:
            delay = max(0.0, 2.0 - (time.monotonic() - _LAST_REQUEST))
            if delay:
                time.sleep(delay)
            _LAST_REQUEST = time.monotonic()
        try:
            return client.chat.completions.create(**request)
        except Exception as exc:
            if getattr(exc, "status_code", None) != 429 or attempt == 4:
                raise
            headers = getattr(getattr(exc, "response", None), "headers", {}) or {}
            try:
                retry_after = float(headers.get("retry-after", "0"))
            except (TypeError, ValueError):
                retry_after = 0
            delay = max(retry_after, min(60, 10 * 2 ** attempt))
            print(f"[CLOVA_RATE_LIMIT] retry {attempt + 1}/4 after {delay:.0f}s", flush=True)
            time.sleep(delay)


def create_clova_client(api_key: str, base_url: str) -> Any | None:
    if not api_key or OpenAI is None:
        return None
    return OpenAI(api_key=api_key, base_url=base_url, max_retries=0, timeout=60.0)


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
            elif isinstance(getattr(item, "text", None), str):
                parts.append(item.text)
        return "\n".join(parts).strip()
    return str(content or "").strip()


def _parse_model_json(text: str, response_model: type[ResponseModelT]) -> ResponseModelT:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return response_model.model_validate_json(cleaned)
    except Exception:
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if not match:
            raise ValueError("CLOVA response did not contain a JSON object")
        return response_model.model_validate(json.loads(match.group(0)))


def request_structured_response(
    *,
    client: Any,
    model: str,
    prompt: str,
    response_model: type[ResponseModelT],
    max_completion_tokens: int = 4096,
    use_schema: bool = True,
) -> ResponseModelT:
    request: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "reasoning_effort": "none",
        "max_completion_tokens": max_completion_tokens,
    }
    if use_schema:
        request["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": response_model.__name__,
                "strict": True,
                "schema": response_model.model_json_schema(),
            },
        }
    cache_path = None
    cache_root = os.getenv("CLOVA_RESPONSE_CACHE_DIR")
    if cache_root:
        from .policy_engine import policy_fingerprint
        signature = json.dumps({"request": request, "schema": response_model.model_json_schema(),
            "endpoint": str(getattr(client, "base_url", "")), "rules": policy_fingerprint()}, ensure_ascii=False, sort_keys=True)
        cache_path = Path(cache_root) / (hashlib.sha256(signature.encode()).hexdigest() + ".json")
        if cache_path.is_file():
            try:
                return response_model.model_validate_json(cache_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
    response = _completion_with_backoff(client, request)
    choices = getattr(response, "choices", None) or []
    if not choices:
        raise ValueError("CLOVA response did not contain any choices")
    text = _message_text(getattr(choices[0].message, "content", ""))
    if not text:
        raise ValueError("CLOVA response content was empty")
    parsed = _parse_model_json(text, response_model)
    if cache_path is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = cache_path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(parsed.model_dump_json(), encoding="utf-8")
            os.replace(temporary, cache_path)
        finally:
            temporary.unlink(missing_ok=True)
    return parsed
