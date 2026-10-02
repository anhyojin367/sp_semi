# SP 시험결과 자동검수 시스템

> **2026-10-02 재검토 의견 반영 · v108** — 단위 대응 근거, 관련 MD 번호와 구현 범위, 집계 기준, 원문 펼치기, 검수 파일·코드 지문 및 다크 설정에서의 가독성을 보강했습니다. 기존 판정 기준과 결과 건수 계산은 유지합니다. [수정 내용·검증 기록](docs/FEEDBACK_20261002.md)을 참고하세요.

> **2026-10-01 검토 의견 반영 · v107** — 새 D00~D16(교체된 D06·D13 포함)에 맞춰 시험의 최종 상태와 이유, IU/EU 동일 분모 비교, 누락 공정의 연쇄 보류, 제품명 오류 문서의 검수 진입을 보강했습니다. A/B/C·공통 요구사항 82개를 삭제 없이 MD에 보존하고 실행 범위와 미지원 범위를 구분했습니다. [변경 내용과 검증 기록](docs/FEEDBACK_20261001.md)을 먼저 읽어 주세요. 82개 모두가 임의 문서에서 완전 자동 구현되었다는 뜻은 아닙니다.

> **2026-09-30 배포 호환성 수정 · v106** — 실제 배포 로그의 Python 3.14.7에 맞춰 실행 패키지를 고정했습니다. Camelot의 Python별 pypdf 요구 조건을 구분하고 Streamlit을 1.61.0으로 갱신했습니다. 판정 엔진·MD·제출 PDF는 변경하지 않았습니다. 새 3.14 환경의 D00~D16 오프라인 결과는 기존 3.10 기준과 17/17 일치합니다. [배포 호환성 확인](docs/PORTABLE_EXTRACTION.md#python-314-배포-호환성--v106)을 참고하세요.

> **2026-09-30 업데이트 · v105** — 표의 빈 셀·줄바꿈 처리, 추출 환경 고정·기록, 긴 PDF 분할 처리, 통신 실패와 문서 판정의 구분을 보강했습니다. 최신 결과와 제한은 [추출 이식성·검증 안내](docs/PORTABLE_EXTRACTION.md)를 참고하세요. 아래 v104는 이전 공개 기준입니다.

> **2026-09-29 업데이트 · v104** — 기존 UI와 메일 수신을 유지하면서 SP/허가서 직접 업로드, 실행 가능한 MD 규칙, 허가 기준 대조, 판정·시뮬레이션 작업 분리 및 D00~D16 회귀를 추가했습니다. 아래 기존 설명에 최신 동작을 반영했습니다. 모든 제품·양식의 무오류 판정이나 의약품 출하 승인을 보장하는 시스템은 아닙니다.

## 처음 보는 분을 위한 안내

| 알고 싶은 것 | 문서 |
|---|---|
| 두 번째 검토: 단위 설명·가독성·집계·검수 버전 (v108) | [재검토 의견 반영](docs/FEEDBACK_20261002.md) |
| 새 Word 검토 의견·더미·82개 요구사항 반영 (v107) | [최신 검토 의견 반영](docs/FEEDBACK_20261001.md) |
| 이번에 무엇을 바꿨고 어디까지 검증했는가 | [업데이트·인수인계](docs/RELEASE_20260929.md) |
| 전체 흐름과 파일별 역할 | [시스템 구조](docs/SYSTEM_OVERVIEW.md) |
| Python을 고치지 않고 MD 규칙을 수정하는 법 | [MD 작성·검증 안내](docs/MD_RULES_GUIDE.md) |
| D02 개별 시험 표시, D13~D16, 정상 문서 보류 이유 | [사용자 제보 수정](docs/FEEDBACK_20260929.md) |
| 로컬·배포 표 추출 차이와 긴 문서 처리 (v105) | [추출 이식성·검증 안내](docs/PORTABLE_EXTRACTION.md) |
| 아직 안 된 것 / A·B·C 요구사항 범위 | [검증 범위와 한계](docs/VALIDATION_SCOPE.md) |

### 기존 코드와 크게 달라진 점

- **입력**: Gmail 외에 화면의 파일 업로드로 SP PDF와 그 SP에 연결할 허가서를 함께 등록합니다. 문서함에는 원래 파일명도 표시합니다.
- **MD**: `sp_pdf_judger/rules/`의 YAML front matter를 읽어 규칙을 실행합니다. `domain_details/`의 설명용 MD와는 다릅니다. 수치·기간·누락 등은 검증된 Python 연산으로, `semantic_review`는 CLOVA 의미 판단으로 처리합니다.
- **허가서**: 연결된 허가서의 단계·시험·근거를 확인해 적용 기준을 대조합니다. 단순 검색 유사도만으로 통과시키지 않습니다. 불명확한 적용 범위나 환산 근거는 보류합니다.
- **판정 일관성**: D02의 생존율이 맞더라도 관찰기간이 부족하면 해당 시험 카드도 불충족입니다. 문서 규칙 결과와 개별 시험의 상태·근거를 연결합니다.
- **응답성·복구**: 검수/시뮬레이션 준비를 백그라운드 작업으로 분리하고, 캐시 식별·중복 실행·공유 상태 저장을 보호합니다. 실패한 판정의 단순 열람은 자동 유료 재시도를 하지 않습니다. 중간 단계부터 이어 하는 기능은 아직 미구현입니다.
- **재현 자료**: `더미데이터/`에 승인된 합성 D00~D16, 더미 허가서, 판정기준 Word를 포함합니다. 실제 수신함·캐시·키는 포함하지 않습니다.

### 빠른 실행 (Windows PowerShell)

```powershell
git clone https://github.com/anhyojin367/sp_semi.git
cd sp_semi
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
# .env의 CLOVA_API_KEY에 새로 발급한 본인 키를 입력합니다.
.\.venv\Scripts\python.exe -m streamlit run sp_app.py --server.port 8501 --server.address 127.0.0.1
```

브라우저에서 `http://127.0.0.1:8501`을 엽니다. 8501이 사용 중이면 기존 서버를 확인하거나 포트를 8503 등으로 바꾸세요.
새 복제본은 빈 문서함으로 시작합니다. **파일 업로드 → SP와 허가서 선택 → 등록 → 문서 선택 → 검수 진행** 순서로 사용합니다.
직접 업로드에는 Gmail 로그인이 필요 없습니다. CLOVA 판정은 외부 전송·API 비용이 발생합니다.
PDF 텍스트 추출과 달리 스캔 OCR에는 별도 OCR 환경이 필요하며, Windows OCR 관련 시험은 Windows 환경에서 검증했습니다.

### 빠른 검증 (API 호출 없음)

```powershell
.\.venv\Scripts\python.exe scripts/validate_rules.py
.\.venv\Scripts\python.exe scripts/prepare_test_fixtures.py
.\.venv\Scripts\python.exe -m pytest -q -rs
.\.venv\Scripts\python.exe scripts/validate_corpus.py --output-dir .local_validation/fresh_offline
```

저장 fixture 재생과 새 PDF 판정은 구별합니다. CLOVA 통합 검증은 `--live`를 명시해야 하며 별도 비용이 발생합니다.
v104의 기존 자동시험 결과는 **1,622개 통과**(미구현 단계 재개 초안 7개 제외), 실제 D00~D16 오프라인 지정 오류 누락은 0건입니다.
v104는 초안 7개를 기본 pytest에서 **사유가 보이는 skip**으로 남겼습니다. 당시 게시 파일만 펼친 새 사본에서도 **1,622 통과 / 7 skip / 실패 0**을 재확인했습니다. v105 로컬 개선의 전체시험은 **1,655 통과 / 7 skip / 실패 0**, 독립 추출 환경 D00~D16 비교는 **17/17 일치**입니다. 이는 클라우드에서 전 문서를 재검수했다는 뜻은 아닙니다. 상세 검증 결과와 보류 사유는 [v105 안내](docs/PORTABLE_EXTRACTION.md)와 [이전 업데이트 기록](docs/RELEASE_20260929.md)을 참고하세요.

> **보안 주의:** 과거 커밋의 예제 설정에 실키가 포함되어 있었습니다. 현재 예제는 빈 값이지만 Git 이력은 재작성하지 않았습니다. 해당 키는 폐기·재발급해야 합니다. `.env`와 `.streamlit/secrets.toml`은 Git에 올리지 마세요.

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
현재 SP 문서와 연결된 허가서만 검토에 사용합니다. 최신 경로에서는 보류 항목에만 한정하지 않고,
SP 기준상 합격인 시험도 연결된 권위 있는 허가 기준과 대조합니다.

```text
SP 기준 1차 판정 (Pass / Fail / Hold)
      ↓
연결 허가서의 적용 단계·시험·원문 근거 확인
      ↓
허가 기준 대조 + 확정 가능한 수치 재계산
      ↓
MD 규칙 / 개별 시험 상태 연결 → Pass / Fail / Hold
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
| Permit Review | 연결 허가서의 적용 범위·원문 근거 확인 및 허가 기준 우선 대조 |
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
├── sp_document_upload.py / sp_document_models.py
│   └── SP·허가서 직접 업로드 및 제출 건 모델
│
├── sp_judgement_bridge.py / sp_review_jobs.py / sp_process_lock.py
│   └── 검수 작업·캐시·프로세스 잠금 및 상태 저장
│
├── sp_simulation_jobs.py / sp_flowchart_data.py
│   └── 시뮬레이션 비동기 준비·공정 및 판정 숫자 연결
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
│   ├── rules/ (document.md, sky_covione.md 등)
│   │   └── 실행 가능한 MD 규칙 및 검토 설정
│   │
│   ├── policy_engine.py / policy_schema.py / policy_summary.py
│   │   └── MD 검증·실행·개별 시험 상태 연결
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
│   └── 추출·MD·허가·UI·동시성 회귀 및 과거 저장 fixture
│
├── 더미데이터/
│   └── 공개 승인된 D00~D16, 더미 허가서, 판정기준 Word
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

개발 의존성을 설치하고 저장 fixture를 준비한 뒤 전체 테스트를 실행합니다.
7개의 미구현 단계 재개 계약시험은 skip으로 표시하며, 환경 의존 시험의 skip도 `-rs`로 확인합니다.

```bash
python -m pip install -r requirements-dev.txt
python scripts/prepare_test_fixtures.py
python -m pytest -q -rs
```

Domain Context 관련 테스트:

```bash
python -m pytest tests/test_domain_details.py
```

---

# 14. Adding New Rules

**최신 실행 MD 경로는 `sp_pdf_judger/rules/`입니다.** 기존 연산으로 표현 가능한 조건은
`document.md` / `sky_covione.md`의 구조화 규칙을 수정하고 검증하면 됩니다.
자연어 의미 규칙은 `operation: semantic_review`로 명시합니다. 단순히 설명 문장을 아무 MD에 쓰면 자동 실행되는 것은 아닙니다.
새로운 추출 필드나 지원하지 않는 연산이 필요하면 Python 구현과 회귀시험도 필요합니다.
[실제 작성 예제와 명령](docs/MD_RULES_GUIDE.md)을 먼저 참고하세요. 아래는 기존 원칙입니다.

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

이번 버전에는 사용자 공개 승인을 받은 합성 더미자료와 허가서/참고자료가 포함됩니다.
실제 메일 수신함(`incoming_sp_pdfs/`), 판정 상태(`.sp_judgement_status/`),
렌더 PDF(`static/pdf_view/`), 가상환경·로컬 검증 캐시는 추적하지 않습니다.
기존 로컬 파일은 삭제하지 않고 Git 추적에서만 제외했습니다.
과거 커밋의 노출 키는 예제에서 삭제해도 무효화되지 않습니다. 반드시 발급처에서 폐기·재발급하세요.

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
