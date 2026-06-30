from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import fitz

from .schemas import ExtractedRecord
from .utils import clean_text


@dataclass
class PermitChunk:
    source_file: str
    page_number: int
    section_number: str
    title: str
    text: str


class PermitPdfStore:
    """
    허가서 PDF를 읽어서 섹션 단위 chunk로 만들고,
    검수 대상 시험 항목과 관련 있는 허가서 문단을 검색한다.
    """

    def __init__(self, permit_pdf_paths: list[Path] | None = None) -> None:
        self.permit_pdf_paths = permit_pdf_paths or []
        self.chunks: list[PermitChunk] = []

        for path in self.permit_pdf_paths:
            if path.exists() and path.suffix.lower() == ".pdf":
                self.chunks.extend(self._load_pdf(path))

    @property
    def enabled(self) -> bool:
        return bool(self.chunks)

    def _load_pdf(self, pdf_path: Path) -> list[PermitChunk]:
        chunks: list[PermitChunk] = []

        with fitz.open(pdf_path) as doc:
            for idx, page in enumerate(doc, start=1):
                text = clean_text(page.get_text("text") or "")
                if not text:
                    continue

                chunks.extend(
                    self._split_page_into_sections(
                        source_file=pdf_path.name,
                        page_number=idx,
                        text=text,
                    )
                )

        return chunks

    def _split_page_into_sections(
        self,
        source_file: str,
        page_number: int,
        text: str,
    ) -> list[PermitChunk]:
        """
        예:
        2.1.2.1.1 세포주 확인 시험
        2.1.2.1.2 세포성장 및 증식확인시험
        같은 번호 heading을 기준으로 chunk 분리.
        """
        pattern = re.compile(
            r"(?P<section>\d+(?:\.\d+)+)\s+(?P<title>[^\n]+)"
        )

        matches = list(pattern.finditer(text))

        if not matches:
            return [
                PermitChunk(
                    source_file=source_file,
                    page_number=page_number,
                    section_number="",
                    title="허가서 본문",
                    text=text,
                )
            ]

        chunks: list[PermitChunk] = []

        for idx, match in enumerate(matches):
            start = match.start()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)

            section_number = clean_text(match.group("section"))
            title = clean_text(match.group("title"))
            body = clean_text(text[start:end])

            if not body:
                continue

            chunks.append(
                PermitChunk(
                    source_file=source_file,
                    page_number=page_number,
                    section_number=section_number,
                    title=title,
                    text=body,
                )
            )

        return chunks

    def search(self, record: ExtractedRecord, top_k: int = 5) -> list[PermitChunk]:
        if not self.chunks:
            return []

        query_parts = [
            record.section_number,
            record.section_title,
            record.test_name,
            record.method,
            record.criteria,
            record.result,
            record.raw_text,
        ]
        query = " ".join(clean_text(x) for x in query_parts if clean_text(x))
        query_tokens = self._tokens(query)

        if not query_tokens:
            return []

        scored: list[tuple[float, PermitChunk]] = []

        for chunk in self.chunks:
            score = self._score_chunk(query_tokens, record, chunk)
            if score > 0:
                scored.append((score, chunk))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [chunk for _, chunk in scored[:top_k]]

    def _tokens(self, text: str) -> list[str]:
        text = clean_text(text).casefold()
        text = text.replace("e.coli", "ecoli")
        text = text.replace("e.coli".casefold(), "ecoli")
        text = text.replace("e.coLi".casefold(), "ecoli")

        text = re.sub(r"[^0-9a-zA-Z가-힣μµ%./^]+", " ", text)
        tokens = [x for x in text.split() if len(x) >= 2]

        stopwords = {
            "시험",
            "시험기준",
            "시험결과",
            "시험방법",
            "실측치",
            "기준",
            "결과",
            "확인",
        }

        return [t for t in tokens if t not in stopwords]

    def _score_chunk(
        self,
        query_tokens: list[str],
        record: ExtractedRecord,
        chunk: PermitChunk,
    ) -> float:
        chunk_text = f"{chunk.section_number} {chunk.title} {chunk.text}"
        chunk_norm = chunk_text.casefold()
        chunk_norm = chunk_norm.replace("e.coli", "ecoli")
        chunk_norm = chunk_norm.replace("e.coLi".casefold(), "ecoli")

        score = 0.0

        # 섹션 번호가 완전히 같으면 최우선
        if record.section_number and record.section_number == chunk.section_number:
            score += 80.0

        # 하위/상위 섹션 일부가 같아도 가산
        if record.section_number and chunk.section_number:
            if record.section_number.startswith(chunk.section_number) or chunk.section_number.startswith(record.section_number):
                score += 20.0

        test_name = clean_text(record.test_name)
        if test_name and test_name in chunk_text:
            score += 40.0

        section_title = clean_text(record.section_title)
        if section_title and section_title in chunk_text:
            score += 25.0

        method = clean_text(record.method)
        if method and method in chunk_text:
            score += 20.0

        for token in query_tokens:
            if token in chunk_norm:
                score += 2.0

        return score

    def format_context(self, chunks: list[PermitChunk]) -> str:
        if not chunks:
            return ""

        parts: list[str] = []

        for idx, chunk in enumerate(chunks, start=1):
            parts.append(
                f"[허가서 근거 {idx}]\n"
                f"- 파일: {chunk.source_file}\n"
                f"- 페이지: {chunk.page_number}\n"
                f"- 섹션: {chunk.section_number} {chunk.title}\n"
                f"- 내용:\n{chunk.text}"
            )

        return "\n\n".join(parts)