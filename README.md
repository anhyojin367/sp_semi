# SP 시험결과 자동판별 시스템 1차 프로토타입

이 폴더는 Gmail로 수신한 SP PDF 문서를 자동 저장하고, Streamlit 화면에서 제조요약도만 추출해 보여주는 1차 프로토타입입니다.

## 실행 방법

1. Python 환경에서 필요한 패키지가 있는지 확인합니다.

```powershell
python -m pip install streamlit pypdf pypdfium2 pillow
```

2. 앱을 실행합니다.

```powershell
streamlit run sp_app.py
```

또는 Windows에서는 아래 파일을 더블클릭해도 됩니다.

```text
run_sp_app.bat
```

3. 브라우저에서 아래 주소를 엽니다.

```text
http://localhost:8501
```

## Gmail 연동 방법

Streamlit 왼쪽 패널에 다음 값을 입력합니다.

- Gmail 주소
- Gmail 앱 비밀번호
- 메일 제목 포함 문구, 기본값: `[식약처]`

Gmail 앱 비밀번호는 일반 로그인 비밀번호가 아닙니다. Google 계정에서 2단계 인증을 켠 뒤 생성한 16자리 앱 비밀번호를 입력해야 합니다.

앱은 Gmail에서 제목에 `[식약처]`가 포함된 메일을 찾고, PDF 첨부파일을 `incoming_sp_pdfs` 폴더에 자동 저장합니다.

## 주요 파일

- `sp_app.py`: Streamlit 화면과 전체 UI 흐름
- `sp_gmail_ingest.py`: Gmail IMAP 연결 및 PDF 첨부 저장
- `sp_pdf_viewer.py`: 제조요약도 페이지 탐색, 렌더링, 플로우차트 영역 크롭
- `sp_document_metadata.py`: PDF 내부 텍스트에서 회사명, 문서 제목, 제조번호, 버전, 접수날짜 추출
- `sp_company_logos.py`: 회사명과 실제 로고 파일 매핑
- `company_logos/logos.json`: 회사 로고 매핑 파일
- `incoming_sp_pdfs/`: Gmail에서 받은 PDF가 저장되는 폴더

## 에이전트 시뮬레이션 붙일 위치

오른쪽 제조요약도 이미지는 `sp_app.py`의 아래 함수에서 표시합니다.

```python
def _render_selected_pdf(pdf_path: Path) -> None:
```

현재 흐름은 다음과 같습니다.

```text
선택된 PDF 경로
-> render_manufacturing_flowchart()
-> 제조요약도 이미지를 base64로 변환
-> flowchart-frame 안에 img로 표시
```

에이전트 시뮬레이션은 `flowchart-frame` 내부에 overlay 레이어를 추가하는 방식으로 붙이면 됩니다.

예상 구조:

```html
<div class="flowchart-frame">
  <img class="flowchart-image" ... />
  <div class="agent-overlay">
    <!-- 로봇/에이전트 애니메이션 -->
  </div>
</div>
```

## 회사 로고 추가 방법

임의 로고는 생성하지 않습니다. 실제 회사 로고 파일만 표시합니다.

예를 들어 `동국바이오사이언스` 로고를 추가하려면:

1. 로고 파일을 `company_logos/dongkook.png`로 저장합니다.
2. `company_logos/logos.json`을 아래처럼 수정합니다.

```json
{
  "동국바이오사이언스": "dongkook.png"
}
```

그러면 회사명이 매칭될 때 왼쪽 문서 카드에 로고가 표시됩니다.

## 공유 시 주의

이 공유본에는 Gmail 비밀번호, 앱 비밀번호, 수신 PDF를 포함하지 않습니다. 각 사용자가 자기 Gmail 주소와 자기 앱 비밀번호를 입력해서 사용해야 합니다.
