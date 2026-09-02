from __future__ import annotations

import json
from types import SimpleNamespace

from pydantic import BaseModel

from sp_pdf_judger.clova_client import request_structured_response


class DemoResponse(BaseModel):
    status: str
    reason: str


class FakeCompletions:
    def __init__(self) -> None:
        self.request: dict[str, object] = {}

    def create(self, **kwargs):
        self.request = kwargs
        content = json.dumps({"status": "pass", "reason": "matched"})
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )


def test_clova_structured_request_uses_openai_compatible_chat_api() -> None:
    completions = FakeCompletions()
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))

    response = request_structured_response(
        client=client,
        model="HCX-007",
        prompt="judge this",
        response_model=DemoResponse,
        max_completion_tokens=1234,
    )

    assert response.status == "pass"
    assert completions.request["model"] == "HCX-007"
    assert completions.request["messages"] == [
        {"role": "user", "content": "judge this"}
    ]
    assert completions.request["max_completion_tokens"] == 1234
    assert completions.request["reasoning_effort"] == "none"
    assert completions.request["response_format"]["type"] == "json_schema"
