from __future__ import annotations

import json
import re
from typing import Any, TypeVar

from pydantic import BaseModel

try:
    from openai import OpenAI
except Exception:  # pragma: no cover - optional until runtime dependencies are installed
    OpenAI = None


ResponseModelT = TypeVar("ResponseModelT", bound=BaseModel)


def create_clova_client(api_key: str, base_url: str) -> Any | None:
    if not api_key or OpenAI is None:
        return None
    return OpenAI(api_key=api_key, base_url=base_url)


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
    response = client.chat.completions.create(**request)
    choices = getattr(response, "choices", None) or []
    if not choices:
        raise ValueError("CLOVA response did not contain any choices")
    text = _message_text(getattr(choices[0].message, "content", ""))
    if not text:
        raise ValueError("CLOVA response content was empty")
    return _parse_model_json(text, response_model)
