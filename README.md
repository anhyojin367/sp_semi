# SP 시험결과 자동검수 시스템

의약품 SP(Summary Protocol) PDF를 구조화하고, **Deterministic Rule · RAG · LLM**을 역할별로 결합해 시험 결과를 자동 검수하는 AI 시스템입니다.

단순히 PDF 내용을 LLM에 입력해 판정을 맡기는 방식이 아니라,

- **코드로 확정 가능한 기준은 Rule Engine으로 검증**
- **외부 근거가 필요한 경우 RAG로 관련 문서 검색**
- **정성적 의미 해석이 필요한 경우에만 LLM을 제한적으로 활용**
- **근거 부족·OCR 오류·정보 누락 시 추측하지 않고 Hold 처리**

하도록 설계했습니다.

---

## 1. System Overview

```text
SP PDF
  ↓
PDF / OCR Parsing
  ↓
Structured Test Records
  ↓
Deterministic Rule Validation
  ↓
RAG Evidence Retrieval
  ↓
Company / Product Domain Context
  ↓
LLM-assisted Semantic Judgment
  ↓
Permit Document Review (Hold cases)
  ↓
Pass / Hold / Fail
  ↓
Regression Log & Traceability
