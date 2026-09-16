from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from .permit_catalog import PermitPolicy
from .permit_ocr import PermitPageText, extract_permit_page_texts
from .schemas import ExtractedRecord
from .utils import clean_text


_HEADING_RE = re.compile(r"(?m)^\s*(?P<section>\d+(?:\.\d+)+(?:\.)?)\s+(?P<title>[^\n]+?)\s*$")
_LEADING_SECTION_RE = re.compile(r"^\s*\d+(?:\.\d+)+(?:\.)?\s*")
_NON_IDENTITY_RE = re.compile(r"[^0-9a-zA-Z가-힣]+")
_GENERIC_TEST_NAMES = {"확인시험", "성상", "무균시험"}
_GENERIC_STAGE_TOKENS = {"시험", "기준", "대한", "및", "test"}


def normalize_permit_heading(text: str) -> str:
    """Return a heading's semantic title without its dotted section number."""
    return clean_text(_LEADING_SECTION_RE.sub("", text or ""))


def _compact_identity(text: str, *, strip_number: bool) -> str:
    value = normalize_permit_heading(text) if strip_number else clean_text(text)
    return _NON_IDENTITY_RE.sub("", value.casefold())


def _section_depth(section_number: str) -> int:
    return len([part for part in section_number.rstrip(".").split(".") if part])


def _record_path_values(record: ExtractedRecord) -> list[str]:
    values: list[str] = []
    for item in record.section_path or []:
        if isinstance(item, str):
            values.append(item)
        elif isinstance(item, dict):
            values.extend(
                str(value)
                for key, value in item.items()
                if key in {"title", "section_title", "name", "label"} and value
            )
    return values


def _stage_match_score(record_stage: str, chunk_stage: str) -> float:
    """Score actual stage overlap, favoring a specific normalized path."""
    record_identity = _compact_identity(record_stage, strip_number=True)
    chunk_identity = _compact_identity(chunk_stage, strip_number=True)
    if not record_identity or not chunk_identity:
        return 0.0
    if record_identity == chunk_identity:
        return 100.0
    if record_identity in chunk_identity:
        return 80.0
    if chunk_identity in record_identity:
        return 20.0 + (30.0 * len(chunk_identity) / len(record_identity))

    record_tokens = {
        token
        for token in re.findall(r"[a-z]+|[가-힣]+", record_stage.casefold())
        if token not in _GENERIC_STAGE_TOKENS
    }
    chunk_tokens = {
        token
        for token in re.findall(r"[a-z]+|[가-힣]+", chunk_stage.casefold())
        if token not in _GENERIC_STAGE_TOKENS
    }
    overlap = record_tokens & chunk_tokens
    if not overlap:
        return 0.0
    return float(len(overlap) * 5)


@dataclass(frozen=True)
class PermitChunk:
    source_file: str
    page_number: int
    section_number: str
    title: str
    text: str
    page_start: int | None = None
    page_end: int | None = None
    section_path_titles: tuple[str, ...] = ()
    normalized_test_name: str = ""
    normalized_stage_path: str = ""


class PermitPdfStore:
    """Extract and search permit paragraphs without treating section numbers as identity."""

    def __init__(
        self,
        permit_pdf_paths: list[Path] | None = None,
        policy: PermitPolicy | None = None,
        page_text_extractor: Callable[..., tuple[list[PermitPageText], list[str]]] = extract_permit_page_texts,
    ) -> None:
        self.permit_pdf_paths = permit_pdf_paths or []
        self.policy = policy
        self.page_text_extractor = page_text_extractor
        self.chunks: list[PermitChunk] = []
        self.extraction_errors: list[str] = []
        for path in self.permit_pdf_paths:
            if path.exists() and path.suffix.lower() == ".pdf":
                self.chunks.extend(self._load_pdf(path))

    @classmethod
    def from_page_texts(
        cls,
        pages: Iterable[PermitPageText | tuple[int, str]],
        policy: PermitPolicy | None = None,
        source_file: str = "",
    ) -> "PermitPdfStore":
        """Build a deterministic store from already extracted physical page text."""
        store = cls([], policy=policy)
        store.chunks = store._parse_page_texts(pages, source_file)
        return store

    @property
    def enabled(self) -> bool:
        return bool(self.chunks)

    def _load_pdf(self, pdf_path: Path) -> list[PermitChunk]:
        try:
            pages, errors = self.page_text_extractor(
                pdf_path,
                ocr_mode=self.policy.ocr_mode if self.policy is not None else "auto",
            )
        except Exception as exc:
            self.extraction_errors.append(f"Could not extract permit {pdf_path.name}: {exc}")
            return []
        self.extraction_errors.extend(errors)
        return self._parse_page_texts(pages, pdf_path.name)

    def _split_page_into_sections(self, source_file: str, page_number: int, text: str) -> list[PermitChunk]:
        """Compatibility helper for callers that still split one physical page."""
        return self._parse_page_texts([(page_number, text)], source_file)

    def _parse_page_texts(
        self,
        pages: Iterable[PermitPageText | tuple[int, str]],
        source_file: str,
    ) -> list[PermitChunk]:
        chunks: list[PermitChunk] = []
        path_stack: list[tuple[int, str]] = []
        open_section: dict[str, object] | None = None

        def finish_open() -> None:
            nonlocal open_section
            if open_section is None:
                return
            text = clean_text("\n".join(open_section["parts"]))  # type: ignore[index]
            if text:
                chunks.append(
                    PermitChunk(
                        source_file=source_file,
                        page_number=open_section["page_start"],  # type: ignore[arg-type,index]
                        section_number=open_section["section_number"],  # type: ignore[arg-type,index]
                        title=open_section["title"],  # type: ignore[arg-type,index]
                        text=text,
                        page_start=open_section["page_start"],  # type: ignore[arg-type,index]
                        page_end=open_section["page_end"],  # type: ignore[arg-type,index]
                        section_path_titles=open_section["path_titles"],  # type: ignore[arg-type,index]
                        normalized_test_name=open_section["normalized_test_name"],  # type: ignore[arg-type,index]
                        normalized_stage_path=open_section["normalized_stage_path"],  # type: ignore[arg-type,index]
                    )
                )
            open_section = None

        def start_section(page_number: int, section_number: str, title: str) -> None:
            nonlocal open_section, path_stack
            finish_open()
            depth = _section_depth(section_number)
            path_stack = [entry for entry in path_stack if entry[0] < depth]
            path_stack.append((depth, title))
            path_titles = tuple(item_title for _, item_title in path_stack)
            open_section = {
                "page_start": page_number,
                "page_end": page_number,
                "section_number": section_number.rstrip("."),
                "title": title,
                "parts": [f"{section_number} {title}"],
                "path_titles": path_titles,
                "normalized_test_name": normalize_permit_heading(title),
                "normalized_stage_path": " ".join(
                    normalize_permit_heading(stage_title) for stage_title in path_titles[:-1]
                ),
            }

        for raw_page in pages:
            if self.policy is None and open_section is not None:
                finish_open()
                path_stack = []
            if isinstance(raw_page, PermitPageText):
                page_number, page_text = raw_page.page_number, raw_page.text
            else:
                if not isinstance(raw_page, tuple) or len(raw_page) != 2:
                    raise ValueError("page texts must contain (page_number, text) tuples")
                page_number, page_text = raw_page[0], raw_page[1]
            text = clean_text(page_text or "")
            if not text:
                continue
            matches = list(_HEADING_RE.finditer(text))
            if not matches:
                if open_section is None:
                    chunks.append(
                        PermitChunk(source_file, page_number, "", "허가서 본문", text, page_number, page_number,
                                    normalized_test_name="허가서 본문")
                    )
                else:
                    open_section["parts"].append(text)  # type: ignore[index]
                    open_section["page_end"] = page_number
                continue

            prefix = clean_text(text[: matches[0].start()])
            if prefix:
                if open_section is not None:
                    open_section["parts"].append(prefix)  # type: ignore[index]
                    open_section["page_end"] = page_number
                else:
                    chunks.append(
                        PermitChunk(source_file, page_number, "", "허가서 본문", prefix, page_number, page_number,
                                    normalized_test_name="허가서 본문")
                    )

            for index, match in enumerate(matches):
                section_number = clean_text(match.group("section"))
                title = clean_text(match.group("title"))
                start_section(page_number, section_number, title)
                body = clean_text(text[match.end(): matches[index + 1].start() if index + 1 < len(matches) else len(text)])
                if body:
                    open_section["parts"].append(body)  # type: ignore[index]

        finish_open()
        return chunks

    def search(self, record: ExtractedRecord, top_k: int = 5) -> list[PermitChunk]:
        if not self.chunks:
            return []
        query = " ".join(
            clean_text(value)
            for value in [record.section_number, record.section_title, record.test_name, record.method,
                          record.criteria, record.result, record.raw_text]
            if clean_text(value)
        )
        query_tokens = self._tokens(query)
        if not query_tokens:
            return []
        scored: list[tuple[float, int, PermitChunk]] = []
        for index, chunk in enumerate(self.chunks):
            score = self._score_chunk(query_tokens, record, chunk)
            if score > 0:
                scored.append((score, index, chunk))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [chunk for _, _, chunk in scored[:top_k]]

    def _tokens(self, text: str) -> list[str]:
        value = clean_text(text).casefold().replace("e.coli", "ecoli")
        value = re.sub(r"[^0-9a-zA-Z가-힣μµ%./^]+", " ", value)
        return [
            token for token in value.split() if len(token) >= 2 and token not in
            {"시험", "시험기준", "시험결과", "시험방법", "실측치", "기준", "결과", "확인"}
        ]

    def _score_chunk(self, query_tokens: list[str], record: ExtractedRecord, chunk: PermitChunk) -> float:
        if self.policy is None:
            return self._legacy_score_chunk(query_tokens, record, chunk)
        semantic_score = self._semantic_score(record, chunk)
        if self.policy.ignore_section_numbers:
            return semantic_score
        return semantic_score + self._legacy_score_chunk(query_tokens, record, chunk)

    def _legacy_score_chunk(self, query_tokens: list[str], record: ExtractedRecord, chunk: PermitChunk) -> float:
        chunk_text = f"{chunk.section_number} {chunk.title} {chunk.text}"
        chunk_norm = chunk_text.casefold().replace("e.coli", "ecoli")
        score = 0.0
        if record.section_number and record.section_number == chunk.section_number:
            score += 80.0
        if record.section_number and chunk.section_number and (
            record.section_number.startswith(chunk.section_number) or chunk.section_number.startswith(record.section_number)
        ):
            score += 20.0
        if clean_text(record.test_name) and clean_text(record.test_name) in chunk_text:
            score += 40.0
        if clean_text(record.section_title) and clean_text(record.section_title) in chunk_text:
            score += 25.0
        if clean_text(record.method) and clean_text(record.method) in chunk_text:
            score += 20.0
        return score + sum(2.0 for token in query_tokens if token in chunk_norm)

    def _semantic_score(self, record: ExtractedRecord, chunk: PermitChunk) -> float:
        record_test = _compact_identity(record.test_name or "", strip_number=True)
        chunk_test = _compact_identity(chunk.normalized_test_name or chunk.title, strip_number=True)
        if not record_test or not chunk_test:
            return 0.0
        if record_test == chunk_test:
            test_score = 100.0
        elif record_test in chunk_test or chunk_test in record_test:
            test_score = 60.0
        else:
            return 0.0

        stage_values = [record.section_title or "", *_record_path_values(record)]
        record_stage_path = " ".join(value for value in stage_values if clean_text(value))
        stage_path = chunk.normalized_stage_path
        stage_score = _stage_match_score(record_stage_path, stage_path)
        if chunk_test in _GENERIC_TEST_NAMES and stage_score == 0:
            return 0.0
        return test_score + stage_score

    def format_context(self, chunks: list[PermitChunk]) -> str:
        parts: list[str] = []
        for index, chunk in enumerate(chunks, start=1):
            page_start = chunk.page_start if chunk.page_start is not None else chunk.page_number
            page_end = chunk.page_end if chunk.page_end is not None else chunk.page_number
            page_range = str(page_start) if page_start == page_end else f"{page_start}-{page_end}"
            parts.append(
                f"[허가서 근거 {index}]\n- 파일: {chunk.source_file}\n- 페이지: {page_range}\n"
                f"- 섹션: {chunk.section_number} {chunk.title}\n- 내용:\n{chunk.text}"
            )
        return "\n\n".join(parts)
