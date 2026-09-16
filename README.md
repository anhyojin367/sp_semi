# SP 시험결과 자동검수 시스템

> **Rule · RAG · LLM을 역할별로 분리한 의약품 SP 문서 자동검수 시스템**

의약품 SP(Summary Protocol) PDF에서 제조 및 시험 정보를 구조화하고,  
**Deterministic Rule · RAG · LLM**을 역할별로 결합하여 시험 결과를 자동 검수하는 AI 시스템입니다.

단순히 PDF 전체를 LLM에 입력해 판정을 맡기는 방식이 아니라,

- **코드로 확정 가능한 조건은 Deterministic Rule로 검증**
- **추가 근거가 필요한 항목은 RAG로 관련 문서 검색**
- **정성적 의미 해석이 필요한 항목에만 LLM을 제한적으로 활용**
- **OCR 오류·정보 누락·근거 부족 시 추측하지 않고 Hold 처리**
- **규칙·RAG·도메인 Context 변경에 대한 Regression Test 및 Traceability 관리**

를 핵심 설계 원칙으로 합니다.

---

## System Overview

```text
SP PDF
  │
  ▼
PDF / OCR Parsing
  │
  ▼
Structured Test & Manufacturing Records
  │
  ▼
Deterministic Rule Validation
  │
  ├───────────────┐
  │               │
  ▼               ▼
확정 가능        추가 근거 필요
  │               │
  │               ▼
  │        RAG Evidence Retrieval
  │               │
  │               ▼
  │       Domain-specific Context
  │        Overall / Company / Product
  │               │
  │               ▼
  │          LLM Assist
  │               │
  └───────┬───────┘
          ▼
    Pass / Hold / Fail
          │
          ▼
Regression Log & Traceability
```

### Core Principle

> **모든 판단을 LLM에 맡기지 않는다.**

수치 비교, 날짜 관계, 단위, 제조번호, 공정 순서와 같이  
코드로 명확하게 검증할 수 있는 항목은 Deterministic Rule이 우선합니다.

LLM은 규칙만으로 확정하기 어려운 의미 비교를 보조하며,  
충분한 근거가 없는 경우에는 임의의 결론 대신 **Hold** 상태를 반환합니다.

---

# 1. Problem

SP 문서는 단순 텍스트 문서가 아닙니다.

실제 검수 과정에서는 다음과 같은 문제가 동시에 발생합니다.

- 회사·제품마다 문서 구조와 시험 항목이 다름
- PDF 표 구조 및 OCR 결과가 일정하지 않음
- 동일한 단위가 여러 표기 방식으로 등장
- 수치 기준과 정성적 기준이 혼재
- 제품별 허가사항 및 예외 조건 존재
- 제조번호·제조일자·공정 순서 등 문서 간 관계 검증 필요
- AI가 근거 없이 추측할 경우 잘못된 판정으로 이어질 수 있음

따라서 본 프로젝트에서는 **추출 → 규칙 검증 → 근거 검색 → 의미 해석 → 보류 및 검증**의 역할을 분리했습니다.

---

# 2. Core Features

## 2.1 PDF / OCR 기반 정보 구조화

SP PDF에서 시험 및 제조 정보를 추출하여 이후 검증에 사용할 수 있는 구조화 데이터로 변환합니다.

주요 추출 대상은 다음과 같습니다.

- 회사명
- 제품명
- 문서 제목
- 제조번호
- 제조일자
- 제조량
- 시험명
- 시험방법
- 시험기준
- 시험결과
- 제조단계
- 공정 순서 및 날짜 정보

PDF 텍스트 레이어와 OCR 결과를 함께 활용하며,  
추출된 값은 판정 단계에서 다시 정규화됩니다.

---

## 2.2 Deterministic Rule Validation

명확한 조건으로 검증할 수 있는 항목은 LLM을 사용하지 않고 Python 코드에서 직접 판정합니다.

예:

- 이상 / 이하 / 초과 / 미만
- 수치 범위 비교
- 단위 정규화
- 복수 시험기준 AND 조건
- 제조번호 일치 여부
- 제조일자 비교
- 시험일 허용 범위
- 제조 공정 간 날짜 선후관계
- 제조량 비교
- 공정 순서 검증

```text
Structured Record
      │
      ▼
Condition Parsing
      │
      ▼
Unit / Date Normalization
      │
      ▼
Deterministic Validator
      │
      ▼
Pass / Fail / Hold
```

결정적 규칙으로 확정된 판정은 RAG나 LLM이 임의로 뒤집지 않도록 역할을 분리했습니다.

---

## 2.3 RAG Evidence Retrieval

규칙만으로 충분한 근거를 확보하기 어려운 경우 관련 문서를 검색하여 LLM Context에 제공합니다.

현재 검색 계층은 외부 Vector DB가 아닌 **로컬 TF-IDF 기반 Retrieval**로 구성되어 있습니다.

### Retrieval

```text
시험명 + 시험기준 + 시험결과
            │
            ▼
      Query Normalization
            │
      ┌─────┴─────┐
      ▼           ▼
 Word TF-IDF   Character TF-IDF
   1~2 gram       2~5 gram
     65%            35%
      └─────┬─────┘
            ▼
     Cosine Similarity
            │
            ▼
     Evidence Ranking
```

문자 단위 TF-IDF를 함께 사용하여 다음과 같은 표기 차이에 대한 검색 누락을 완화합니다.

- OCR 오탈자
- 한글 조사 차이
- 단위 기호 차이
- `µ`, `μ`, `u` 등 문자 차이
- `㎎`, `mg` 등 표기 방식 차이

### Retrieval Sources

검색에 사용되는 데이터 예:

- 대한민국약전
- 생물학적제제 기준 및 시험방법
- 단위·기호 자료
- UCUM 관련 데이터
- 승인된 참고 문서

검색 결과에는 자료명, 페이지, 청크 내용 등의 Source 정보가 함께 유지됩니다.

---

## 2.4 Company / Product-specific Domain Context

SP 검수 기준은 회사와 제품에 따라 달라질 수 있습니다.

이를 하나의 거대한 Prompt에 모두 넣는 대신,  
문서에서 인식한 회사와 제품에 맞춰 필요한 Context만 선택적으로 병합합니다.

```text
overall.md
    +
company-specific context
    +
product-specific context
```

예:

```text
GC녹십자
+
녹십자-알부민주20%

        ↓

overall.md
+
companies/gc_biopharma.md
+
products/albumin.md
```

각 Context에는 다음과 같은 정보가 포함될 수 있습니다.

- 회사별 용어
- 제품별 시험 항목
- 자주 누락되는 검수 포인트
- 필드 간 비교 관계
- 사람 확인이 필요한 예외 조건
- Hold가 필요한 상황

Domain Context는 새로운 Python Rule을 자동 생성하는 용도가 아니라,  
**이미 추출된 정보를 LLM이 올바르게 해석하도록 보조하는 지식 계층**으로 사용합니다.

---

## 2.5 LLM-assisted Semantic Judgment

LLM은 전체 시험을 직접 판정하지 않습니다.

문자열은 다르지만 의미상 같은 표현인지 판단해야 하는 경우와 같이  
정성적 해석이 필요한 항목에서 제한적으로 사용합니다.

예:

```text
시험기준
"검출되지 않아야 한다"

시험결과
"미검출"
```

LLM 호출 시에는 단순히 두 문장만 전달하지 않고 다음 Context를 함께 제공합니다.

```text
시험명
+
시험방법
+
시험기준
+
시험결과
+
Deterministic Rule 결과
+
RAG Evidence
+
Overall Context
+
Company Context
+
Product Context
```

이를 통해 LLM 판단이 가능한 한 제공된 근거 안에서 이루어지도록 설계했습니다.

---

## 2.6 Hold-first Design

규제 문서 검수에서는 잘못된 자동 Pass보다 **판단을 보류하는 것**이 더 중요할 수 있습니다.

따라서 충분한 근거를 확보하지 못한 경우 임의의 판정을 생성하지 않고 Hold 상태로 처리합니다.

Hold 예:

- OCR 결과가 불완전한 경우
- 시험기준이 누락된 경우
- 시험결과가 누락된 경우
- 제품별 예외가 불명확한 경우
- 관련 근거 문서를 찾지 못한 경우
- 추가 허가서 확인이 필요한 경우

```text
Evidence Sufficient
      │
      ├──→ Pass
      │
      └──→ Fail


Evidence Insufficient
      │
      └──→ Hold
               │
               ▼
        Human / Permit Review
```

---

## 2.7 Permit Document Review

공통 RAG 문서와 제품별 허가서는 분리하여 관리합니다.

제품별 허가서는 모든 제품에 대한 전역 검색 대상으로 사용하지 않고,  
현재 SP 문서와 연결된 허가서만 추가 검토에 사용합니다.

```text
Initial Judgment
      │
      ▼
     Hold
      │
      ▼
Connected Permit Document
      │
      ▼
Additional Evidence Review
```

이를 통해 다른 제품의 허가 정보가 잘못 검색되어 판정에 영향을 주는 위험을 줄입니다.

---

# 3. Reliability & Traceability

AI의 최종 출력만 저장하는 것이 아니라  
**어떤 데이터와 근거가 해당 판정에 사용되었는지** 추적할 수 있도록 Metadata를 관리합니다.

주요 Metadata:

- `rag_doc_count`
- `rag_sources`
- `domain_detail_sources`
- `domain_detail_matched_company`
- `domain_detail_matched_product`
- `domain_detail_fingerprint`
- `document_company`
- `document_product`
- `llm_model`
- `llm_call_count`
- `llm_success_count`
- `llm_last_error`

이를 통해 특정 결과가 어떤 RAG 자료, Domain Context, LLM 설정을 사용하여 생성됐는지 확인할 수 있습니다.

---

# 4. Regression Testing

도메인 규칙이나 RAG 자료를 추가할 때 기존 판정 결과가 의도하지 않게 바뀔 수 있습니다.

이를 확인하기 위해 PDF별 Regression Log를 관리합니다.

```text
Input PDF
   +
Permit Document
   +
Rule Version
   +
RAG Dataset
   +
Domain Context
   +
LLM Configuration
          │
          ▼
      Evaluation
          │
          ▼
  Regression History
```

### Regression Log

PDF별 Excel 파일에는 다음 정보가 기록됩니다.

- 정답지
- 실행별 판정 결과
- 시험·제조 정보별 세부 판정
- 규칙별 구현 상태
- 사용한 RAG 자료
- 사용한 Domain Context
- LLM 실행 정보
- 실행 환경 Fingerprint

예:

```text
OUTPUT/
└── <PDF명>__<PDF_HASH>__rule_regression.xlsx
```

동일한 PDF와 동일한 코드·RAG·Context·모델 조합은 중복 기록하지 않으며,  
구성 요소 중 하나라도 변경되면 새로운 실행으로 기록합니다.

---

# 5. Rule / RAG / LLM Responsibility

본 시스템에서는 각 계층의 역할을 명확히 분리합니다.

| Component | Responsibility |
|---|---|
| Extractor | PDF/OCR에서 필요한 정보를 구조화 |
| Deterministic Rule | 명확하게 계산·비교 가능한 기준 판정 |
| RAG | 관련 기준 및 참고 근거 검색 |
| Domain Context | 회사·제품별 검수 관점 및 예외 제공 |
| LLM | 정성적 의미 해석 및 보조 판정 |
| Permit Review | Hold 항목에 대한 제품별 추가 근거 확인 |
| Regression Logger | 판정 변화 및 사용 근거 추적 |

### Important

```text
Rule ≠ RAG
RAG ≠ Validator
Domain Context ≠ Python Rule
LLM ≠ Final Authority
```

각 계층이 담당해야 할 역할을 분리하여  
시스템의 재현성과 유지보수성을 높이는 것을 목표로 합니다.

---

# 6. Architecture

```text
┌──────────────────────────────────────────────┐
│                    SP PDF                    │
└──────────────────────┬───────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────┐
│              PDF / OCR Parsing               │
│  Test · Manufacturing · Metadata Extraction  │
└──────────────────────┬───────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────┐
│              Structured Records              │
└──────────────────────┬───────────────────────┘
                       │
              ┌────────┴────────┐
              │                 │
              ▼                 ▼
┌─────────────────────┐   ┌─────────────────────┐
│ Deterministic Rules │   │    RAG Retrieval    │
│                     │   │                     │
│ Numeric             │   │ Pharmacopeia       │
│ Unit                │   │ Standards           │
│ Date                │   │ Unit / Symbol Data  │
│ Manufacturing       │   │ Reference Docs      │
└──────────┬──────────┘   └──────────┬──────────┘
           │                         │
           └────────────┬────────────┘
                        ▼
┌──────────────────────────────────────────────┐
│               Domain Context                 │
│        Overall + Company + Product            │
└──────────────────────┬───────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────┐
│                  LLM Assist                  │
│          Semantic / Qualitative Judge        │
└──────────────────────┬───────────────────────┘
                       │
             ┌─────────┴─────────┐
             ▼                   ▼
      ┌────────────┐       ┌────────────┐
      │ Pass / Fail│       │    Hold    │
      └──────┬─────┘       └──────┬─────┘
             │                    │
             │                    ▼
             │           ┌──────────────────┐
             │           │ Permit / Human   │
             │           │      Review      │
             │           └────────┬─────────┘
             │                    │
             └──────────┬─────────┘
                        ▼
┌──────────────────────────────────────────────┐
│       Regression Log & Traceability          │
└──────────────────────────────────────────────┘
```

---

# 7. Project Structure

```text
sp_semi/
│
├── sp_app.py
│   └── Streamlit UI 및 사용자 인터랙션
│
├── sp_gmail_ingest.py
│   └── Gmail PDF 첨부파일 수집
│
├── sp_document_metadata.py
│   └── 문서 Metadata 추출
│
├── sp_pdf_viewer.py
│   └── PDF 및 제조요약도 렌더링
│
├── sp_pdf_judger/
│   │
│   ├── extractor.py
│   │   └── 시험 정보 추출
│   │
│   ├── criteria_parser.py
│   │   └── 시험기준 Parsing
│   │
│   ├── date_normalizer.py
│   │   └── 날짜 정보 정규화
│   │
│   ├── manufacturing_info_validator.py
│   │   └── 제조정보 결정적 검증
│   │
│   ├── judgement.py
│   │   └── 시험 결과 판정 및 RAG 연계
│   │
│   ├── rag.py
│   │   └── TF-IDF 기반 Evidence Retrieval
│   │
│   ├── llm.py
│   │   └── LLM 보조 판정
│   │
│   ├── permit_pdf_store.py
│   │   └── 연결 허가서 관리
│   │
│   ├── domain_details.py
│   │   └── 회사 / 제품 Context 선택 및 병합
│   │
│   ├── domain_details/
│   │   ├── overall.md
│   │   ├── companies/
│   │   └── products/
│   │
│   └── pipeline.py
│       └── 전체 검수 Pipeline 조립
│
├── rag_data/
│   └── 공통 RAG 참고 자료
│
├── docs/
│   ├── RAG_ARCHITECTURE_AND_DATA_GUIDE.md
│   ├── DOMAIN_DETAIL_AUTHORING_GUIDE.md
│   └── RULE_REGRESSION_AND_RAG_OPERATIONS.md
│
├── tests/
│   └── Rule / Domain Context 관련 테스트
│
├── OUTPUT/
│   └── 판정 결과 및 Regression Log
│
└── requirements.txt
```

---

# 8. Tech Stack

## Language / Framework

- Python
- Streamlit
- Pydantic
- PyYAML

## Document Processing

- PyMuPDF
- pdfplumber
- pypdf
- pypdfium2
- Tesseract OCR

## Retrieval / AI

- RAG
- TF-IDF
- Cosine Similarity
- scikit-learn
- LLM API

## Engineering / Validation

- Pytest
- Git / GitHub
- Regression Testing
- Rule-based Validation
- Judgment Logging
- Metadata Tracking

---

# 9. Quick Start

## Install

```bash
pip install -r requirements.txt
```

## Run

```bash
streamlit run sp_app.py
```

Windows에서는 다음 파일을 사용할 수도 있습니다.

```text
run_sp_app.bat
```

기본 접속 주소:

```text
http://localhost:8501
```

---

# 10. Gmail Ingestion

선택적으로 Gmail에서 SP PDF 첨부파일을 자동 수집할 수 있습니다.

필요 정보:

- Gmail 주소
- Gmail App Password
- 검색할 메일 제목 조건

예:

```text
메일 제목에 [식약처] 포함
        │
        ▼
PDF Attachment Download
        │
        ▼
incoming_sp_pdfs/
```

Gmail App Password는 일반 로그인 비밀번호가 아니며,  
Google 계정의 2단계 인증 후 별도로 생성해야 합니다.

---

# 11. Domain Context 확인

문서에서 인식한 회사·제품에 따라 어떤 Domain Context가 선택되는지 CLI에서 확인할 수 있습니다.

```powershell
python -m sp_pdf_judger.domain_details `
  --company "GC녹십자" `
  --product "녹십자-알부민주20%" `
  --show-context
```

예:

```text
overall.md
+
companies/gc_biopharma.md
+
products/albumin.md
```

---

# 12. RAG Source 확인

현재 적재된 RAG 데이터 수와 Source를 확인합니다.

```powershell
python -c "from sp_pdf_judger.rag import UcumRagStore; s=UcumRagStore(); print(len(s.docs)); print(*s.loaded_sources, sep='\n')"
```

---

# 13. Test

전체 테스트:

```bash
python -m pytest
```

Domain Context 관련 테스트:

```bash
python -m pytest tests/test_domain_details.py
```

---

# 14. Adding New Rules

새로운 검수 기준은 단순히 Prompt에 추가하지 않고, 먼저 규칙의 성격을 구분합니다.

### 기존 추출 정보를 의미적으로 해석하면 되는 경우

```text
Domain Context / LLM Assist
```

### 새로운 구조 추출 또는 정확한 계산이 필요한 경우

```text
Extractor
    ↓
Validator
    ↓
Pipeline
    ↓
Test
```

권장 절차:

```text
1. 규칙 및 적용 범위 정의
        ↓
2. 필요한 Field 존재 여부 확인
        ↓
3. 필요한 경우 Extractor 추가
        ↓
4. Deterministic Validator 구현
        ↓
5. Pipeline 연결
        ↓
6. 정상 / 오류 Test 추가
        ↓
7. Domain Context 업데이트
        ↓
8. Regression Test
```

---

# 15. Design Principles

## Rule First

명확하게 코드로 검증할 수 있는 항목은 LLM에 맡기지 않습니다.

---

## Evidence Before Judgment

LLM 판단 전에 관련 기준과 근거를 먼저 검색하여 제공합니다.

---

## Domain-aware Context

모든 제품에 동일한 Prompt를 사용하는 대신  
회사·제품에 필요한 Context만 선택적으로 제공합니다.

---

## Hold Instead of Guess

정보가 부족한 경우 임의의 결론을 만들지 않고 Hold 처리합니다.

---

## Traceability

어떤 문서·Context·모델이 판정에 사용됐는지 기록합니다.

---

## Regression Safety

Rule, RAG, Domain Context 변경이 기존 판정에 미치는 영향을 확인합니다.

---

# 16. Security & Data Sharing

공개 Repository에는 실제 운영 과정에서 사용될 수 있는 다음 정보가 포함되지 않도록 관리합니다.

- Gmail Password
- Gmail App Password
- API Key
- `.env`
- 인증 Token
- 개인정보
- 실제 기관 수신 문서
- 외부 공개가 제한된 내부 자료

실제 운영 환경에서는 민감 데이터와 인증정보를 별도의 보안 환경에서 관리해야 합니다.

---

# 17. Documentation

보다 상세한 시스템 구조와 운영 방법은 `docs` 폴더를 참고합니다.

### RAG Architecture

```text
docs/RAG_ARCHITECTURE_AND_DATA_GUIDE.md
```

- RAG 데이터 구조
- 검색 방식
- Domain Context
- LLM 입력
- Permit Document 연계
- 데이터 추가 절차

### Regression / Rule Operations

```text
docs/RULE_REGRESSION_AND_RAG_OPERATIONS.md
```

- 규칙별 구현 상태
- PDF별 Regression Log
- 정답지 관리
- RAG / Domain Context 변경 추적
- 실행 Fingerprint

---

# 18. Engineering Perspective

본 프로젝트는 단순한 LLM 기반 문서 요약 또는 질의응답 데모가 아니라,

```text
Document Parsing
      ↓
Structured Data
      ↓
Deterministic Validation
      ↓
Evidence Retrieval
      ↓
Domain-aware Context
      ↓
LLM-assisted Reasoning
      ↓
Pass / Hold / Fail
      ↓
Regression & Traceability
```

까지 연결하는 **End-to-End AI 검수 시스템**을 목표로 합니다.

프로젝트를 진행하며 가장 중요하게 둔 질문은 다음과 같습니다.

> **“AI가 무엇을 판단할 수 있는가?”보다  
> “어떤 판단은 반드시 코드와 근거가 책임져야 하는가?”**

이를 기준으로 Rule, Retrieval, LLM, Validation의 책임을 분리하여  
실제 업무 환경에서 신뢰하고 사용할 수 있는 AI 시스템을 설계하고 있습니다.
