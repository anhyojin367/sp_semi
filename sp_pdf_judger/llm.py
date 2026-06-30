from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from pydantic import BaseModel, Field

from .config import BASE_DIR, DEFAULT_GEMINI_MODEL, GEMINI_API_KEY, FAIL_LABEL, HOLD_LABEL, PASS_LABEL
from .utils import clean_text

try:
    from google import genai
except Exception:
    genai = None


class JudgeResponse(BaseModel):
    status: str = Field(description="적합, 부적합, 또는 보류")
    reason: str = Field(description="검수합격, 검수불합격, 또는 검수보류라는 표현을 포함한 한글 1문장")
    normalized_criteria: str | None = None
    normalized_result: str | None = None


class GeminiJudgeClient:
    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None) -> None:
        # Streamlit이 이미 떠 있는 상태에서 .env를 바꿔도 새 판정 시점에는 다시 읽는다.
        load_dotenv(Path(BASE_DIR) / ".env", override=True)
        load_dotenv(override=True)

        env_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or GEMINI_API_KEY
        env_model = os.getenv("GEMINI_MODEL") or DEFAULT_GEMINI_MODEL

        self.api_key = (api_key or env_key or "").strip()
        self.model = (model or env_model or "gemini-2.5-flash").strip()
        if self.api_key in {"여기에_제미나이_API키", "YOUR_GEMINI_API_KEY", "YOUR_API_KEY"}:
            self.api_key = ""
        self.enabled = bool(self.api_key and genai is not None)
        self.client = None
        self.call_count = 0
        self.success_count = 0
        self.last_error = ""
        if self.enabled:
            try:
                self.client = genai.Client(api_key=self.api_key)
            except Exception as exc:
                self.enabled = False
                self.last_error = f"Gemini client init failed: {exc}"

    def explain(
        self,
        *,
        test_name: str,
        criteria: str | None,
        result: str | None,
        rag_contexts: list[str],
        forced_status: str | None = None,
        deterministic_reason: str | None = None,
    ) -> JudgeResponse | None:
        if not self.enabled or self.client is None:
            return None

        rag_text = "\n".join(f"- {clean_text(x)}" for x in rag_contexts if clean_text(x)) or "- 없음"

        if forced_status:
            task = (
                f"이미 최종 판정은 '{forced_status}'로 확정되었다. "
                f"status는 반드시 '{forced_status}'를 유지하고, 그에 맞는 이유만 작성하라."
            )
        else:
            task = (
                f"기준과 결과를 비교하여 '{PASS_LABEL}', '{FAIL_LABEL}', '{HOLD_LABEL}' 중 하나를 판단하라. "
                f"시험기준과 시험결과만으로 의미 비교가 가능하면 반드시 '{PASS_LABEL}' 또는 '{FAIL_LABEL}'을 선택하라. "
                f"'{HOLD_LABEL}'는 시험기준/시험결과가 비어 있거나, OCR이 심하게 깨졌거나, 외부 문서 없이는 기준 자체를 알 수 없는 경우에만 선택하라."
            )

        prompt = f"""
당신은 백신/바이오의약품 시험성적서 판정 보조 모델이다.
{task}

판정 원칙:
- 숫자와 실제 단위를 우선 비교한다.
- 숫자 뒤에 붙은 설명어(예: 백색도성상, 용출률확인, 삼투압, 성상, 확인, 시험명 일부)는 비교 단위가 아니라 부가 설명일 수 있다.
- 따라서 "100.0%용출률확인"은 "100.0%"와 같은 값으로 볼 수 있다.
- "280mOsm/kg삼투압"은 "280 mOsm/kg"와 같은 값으로 볼 수 있다.
- 실제 단위가 다를 때만 단위 불일치라고 판단한다.
- 비교 가능하면 단위 불일치라고 쓰지 말고 숫자 비교 결과를 써라.
- 범위 기준이면 시험결과가 범위 안에 포함되는지 판단한다.
- 이상/이하/미만/초과 표현을 정확히 해석한다.
- 시험방법, 시험기간, 시험일자 같은 정보는 이유에 넣지 않는다.
- 시험기준과 시험결과만으로 명확히 의미 비교가 가능하면 판정한다.
- 정성 문장도 적극적으로 의미 비교한다. 예: "세포가 뭉치지 않고 구형이어야 함"과 "뭉치지 않음, 구형임"은 두 조건을 모두 만족하므로 '{PASS_LABEL}'이다.
- "확인되지 않아야 함", "검출되지 않아야 함", "관찰되지 않아야 함" 같은 부재 조건은 결과의 "확인되지 않음", "미검출", "불검출", "음성"과 의미상 일치하면 '{PASS_LABEL}'이다.
- "일치해야 함", "동일해야 함", "부합해야 함" 같은 기준은 결과의 "일치함", "동일함", "부합함", "적합"과 의미상 일치하면 '{PASS_LABEL}'이다. 단 "불일치", "상이", "다름"은 '{FAIL_LABEL}'이다.
- 기준이 "실측치", "실측값", "측정값"처럼 실제 확인/측정 결과를 요구하고 결과가 "확인됨", "측정됨", "관찰됨"이면 '{PASS_LABEL}'로 판단할 수 있다. 단 미확인, 측정불가, 부적합이면 '{FAIL_LABEL}'이다.
- 표에서 시험결과 열이 여러 행이면 각 행의 시험결과를 같은 기준에 독립적으로 비교한다. 행/열이 OCR로 심하게 밀려 시험명·시험기간·시험결과를 신뢰할 수 없을 때만 '{HOLD_LABEL}'로 둔다.
- 기준에 여러 조건이 있으면 각 조건을 분해하여 모두 만족하는지 판단한다. 하나라도 명확히 어기면 '{FAIL_LABEL}'이다.
- 허가서가 아직 없어도 SP 문서 안의 시험기준과 시험결과가 직접 비교 가능하면 허가서 확인 대상으로 미루지 말고 판정한다.
- 기준이 "허가서 기준에 따름", "별도 기준에 따름", "제품별 허용기준 확인 필요"처럼 실제 기준값이나 정성 조건을 제공하지 않는 경우에만 '{HOLD_LABEL}'로 판단한다.

출력 조건:
- 응답은 JSON만 출력
- status는 반드시 "{PASS_LABEL}", "{FAIL_LABEL}", "{HOLD_LABEL}" 중 하나
- reason은 반드시 한 문장
- reason에는 반드시 "검수합격", "검수불합격", "검수보류" 중 하나의 표현을 쓴다
- "부적합이 아닌 검수불합격" 같은 표현은 절대 쓰지 않는다
- 비교가 가능하면 reason은 아래 형태를 따른다
  - 시험결과가 시험기준과 같아 검수합격으로 판단했습니다.
  - 시험결과가 시험기준 범위 안에 포함되어 검수합격으로 판단했습니다.
  - 시험결과가 시험기준을 초과해 검수불합격으로 판단했습니다.
  - 시험결과와 시험기준의 단위가 일치하지 않아 검수불합격으로 판단했습니다.
  - 시험기준과 시험결과만으로 실제 기준을 확인할 수 없어 검수보류 처리했습니다.
- normalized_criteria, normalized_result는 비교에 사용한 정규화 표현만 적는다

시험명: {clean_text(test_name)}
시험기준: {clean_text(criteria)}
시험결과: {clean_text(result)}

결정적 판정 근거 / 단계 지시:
{clean_text(deterministic_reason)}

참고 근거:
{rag_text}
""".strip()

        try:
            self.call_count += 1
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config={
                    "response_mime_type": "application/json",
                    "response_schema": JudgeResponse,
                },
            )
            parsed = getattr(response, "parsed", None)
            if parsed:
                self.success_count += 1
                if isinstance(parsed, JudgeResponse):
                    return parsed
                if isinstance(parsed, dict):
                    return JudgeResponse(**parsed)

            text = clean_text(getattr(response, "text", ""))
            if text:
                self.success_count += 1
                return JudgeResponse(**json.loads(text))
        except Exception as exc:
            self.last_error = str(exc)
            print(f"[LLM_JUDGE_ERROR] {exc}")

            # 일부 google-genai 버전/모델 조합에서는 response_schema가 실패할 수 있어
            # 같은 프롬프트를 JSON mime만으로 한 번 더 시도한다.
            try:
                self.call_count += 1
                response = self.client.models.generate_content(
                    model=self.model,
                    contents=prompt,
                    config={"response_mime_type": "application/json"},
                )
                text = clean_text(getattr(response, "text", ""))
                if text:
                    match = re.search(r"\{.*\}", text, flags=re.S)
                    payload = match.group(0) if match else text
                    self.success_count += 1
                    self.last_error = ""
                    return JudgeResponse(**json.loads(payload))
            except Exception as retry_exc:
                self.last_error = str(retry_exc)
                print(f"[LLM_JUDGE_RETRY_ERROR] {retry_exc}")
                return None

        return None
