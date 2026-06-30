# -*- coding: utf-8 -*-
from __future__ import annotations

import csv
import json
import re
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class TestResult:
    name: str
    method: str
    criteria: str
    period: str
    result: str
    verdict: str


@dataclass
class StageNode:
    id: str
    label: str
    tests: list[TestResult] = field(default_factory=list)
    pass_count: int = 0
    fail_count: int = 0
    hold_count: int = 0
    total_count: int = 0
    layer: int = 0
    lane: int = 0


@dataclass
class FlowEdge:
    source: str
    target: str


@dataclass
class ManufacturingGraph:
    product_name: str
    nodes: list[StageNode] = field(default_factory=list)
    edges: list[FlowEdge] = field(default_factory=list)
    layers: list[list[str]] = field(default_factory=list)


PHASE_KEYWORDS: list[tuple[str, int]] = [
    ("마스터 세포주", 0),
    ("마스터세포주", 0),
    ("master", 0),
    ("제조용 세포주", 1),
    ("제조용세포주", 1),
    ("working cell", 1),
    ("중간체", 2),
    ("중간체원액", 2),
    ("intermediate", 2),
    ("component", 2),
    ("나노파티클", 3),
    ("원액", 3),
    ("bulk", 3),
    ("최종원액", 4),
    ("최종 원액", 4),
    ("final bulk", 4),
    ("완제", 5),
    ("완제의약품", 5),
    ("final product", 5),
    ("drug product", 5),
]


def _slugify(name: str) -> str:
    s = name.strip().lower()
    s = re.sub(r"[.\s/()]+", "_", s)
    s = re.sub(r"[^a-z0-9가-힣_]", "", s)
    return s.strip("_") or "stage"


def _normalize(name: str) -> str:
    return re.sub(r"[\s._\-/()]+", "", name.strip().lower())


def _detect_phase(label: str) -> int | None:
    low = label.lower()
    best_phase = None
    best_len = 0
    for kw, phase in PHASE_KEYWORDS:
        if kw in low and len(kw) > best_len:
            best_phase = phase
            best_len = len(kw)
    return best_phase


def _extract_prefix(label: str) -> str | None:
    low = label.lower()
    for pat in [
        r"^(cho)\b",
        r"^(e[\.\s]*coli)\b",
        r"^(component\s*[a-z])\b",
        r"^([a-z]+)\s+(마스터|제조용|중간체)",
    ]:
        m = re.match(pat, low)
        if m:
            return re.sub(r"[\s.]+", "", m.group(1))
    return None


def load_from_csv_directory(
    csv_dir: Path,
    summary_name: str = "summary.csv",
    summary_path: Path | None = None,
) -> ManufacturingGraph:
    csv_dir = Path(csv_dir)
    product_name = csv_dir.name.replace("_제조요약도_csv", "").replace("_csv", "")

    # summary 는 명시 경로(상위 폴더 등) 우선, 없으면 csv_dir 내부에서 읽는다.
    summary_path = Path(summary_path) if summary_path else (csv_dir / summary_name)
    stages: list[tuple[str, int, int, int, int]] = []

    if summary_path.exists():
        with open(summary_path, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                name = row.get("제조명", "").strip()
                if not name or name in {"전체", "단계 미분류 시험"}:
                    continue
                stages.append((
                    name,
                    int(row.get("검수합격", 0)),
                    int(row.get("검수불합격", 0)),
                    int(row.get("검수보류", 0)),
                    int(row.get("총 계", 0)),
                ))

    nodes: list[StageNode] = []
    used_ids: set[str] = set()
    for name, pc, fc, hc, tc in stages:
        slug = _slugify(name)
        if slug in used_ids:
            slug = f"{slug}_{len(used_ids)}"
        used_ids.add(slug)
        tests = _load_stage_tests(csv_dir, name)
        nodes.append(StageNode(
            id=slug,
            label=name,
            tests=tests,
            pass_count=pc,
            fail_count=fc,
            hold_count=hc,
            total_count=tc,
        ))

    if not nodes:
        for csv_file in sorted(csv_dir.glob("*.csv")):
            if csv_file.name == "summary.csv":
                continue
            name = csv_file.stem.replace("_", " ")
            if name == "단계 미분류 시험":
                continue
            tests = _load_stage_tests(csv_dir, name)
            slug = _slugify(name)
            if slug in used_ids:
                slug = f"{slug}_{len(used_ids)}"
            used_ids.add(slug)
            pc = sum(1 for t in tests if t.verdict == "검수합격")
            fc = sum(1 for t in tests if t.verdict == "검수불합격")
            hc = sum(1 for t in tests if t.verdict == "검수보류")
            nodes.append(StageNode(
                id=slug, label=name, tests=tests,
                pass_count=pc, fail_count=fc, hold_count=hc,
                total_count=len(tests),
            ))

    edges = _infer_edges(nodes)
    _assign_layers(nodes, edges)

    graph = ManufacturingGraph(product_name=product_name, nodes=nodes, edges=edges)
    graph.layers = _build_layer_list(nodes)
    return graph


def _load_stage_tests(csv_dir: Path, stage_name: str) -> list[TestResult]:
    norm = _normalize(stage_name)
    for csv_file in csv_dir.glob("*.csv"):
        if csv_file.name == "summary.csv":
            continue
        if _normalize(csv_file.stem) == norm:
            return _parse_test_csv(csv_file)
    for csv_file in csv_dir.glob("*.csv"):
        if csv_file.name == "summary.csv":
            continue
        if norm in _normalize(csv_file.stem) or _normalize(csv_file.stem) in norm:
            return _parse_test_csv(csv_file)
    return []


def _parse_test_csv(path: Path) -> list[TestResult]:
    tests: list[TestResult] = []
    with open(path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            tests.append(TestResult(
                name=row.get("시험명", "").strip(),
                method=row.get("시험 방법", "").strip(),
                criteria=row.get("시험 기준", "").strip(),
                period=row.get("시험 기간", "").strip(),
                result=row.get("시험 결과", "").strip(),
                verdict=row.get("검수 결과", "").strip(),
            ))
    return tests


def _infer_edges(nodes: list[StageNode]) -> list[FlowEdge]:
    if len(nodes) <= 1:
        return []

    phases: dict[int, list[StageNode]] = defaultdict(list)
    unphased: list[StageNode] = []
    for n in nodes:
        p = _detect_phase(n.label)
        if p is not None:
            phases[p] = phases.get(p, [])
            phases[p].append(n)
        else:
            unphased.append(n)

    if not phases:
        return [FlowEdge(nodes[i].id, nodes[i + 1].id) for i in range(len(nodes) - 1)]

    sorted_phases = sorted(phases.keys())

    for n in unphased:
        best_phase = sorted_phases[-1]
        phases[best_phase].append(n)

    sorted_phases = sorted(phases.keys())
    edges: list[FlowEdge] = []

    for idx in range(len(sorted_phases) - 1):
        cur_phase = sorted_phases[idx]
        nxt_phase = sorted_phases[idx + 1]
        cur_nodes = phases[cur_phase]
        nxt_nodes = phases[nxt_phase]

        matched_targets: set[str] = set()
        for cn in cur_nodes:
            cp = _extract_prefix(cn.label)
            if cp:
                for nn in nxt_nodes:
                    np_ = _extract_prefix(nn.label)
                    if np_ and cp == np_:
                        edges.append(FlowEdge(cn.id, nn.id))
                        matched_targets.add(nn.id)

        unmatched_src = [cn for cn in cur_nodes if not any(e.source == cn.id for e in edges if e.target in {nn.id for nn in nxt_nodes})]
        unmatched_tgt = [nn for nn in nxt_nodes if nn.id not in matched_targets]

        if unmatched_src and unmatched_tgt:
            if len(unmatched_src) == len(unmatched_tgt):
                for s, t in zip(unmatched_src, unmatched_tgt):
                    edges.append(FlowEdge(s.id, t.id))
            else:
                for s in unmatched_src:
                    for t in unmatched_tgt:
                        edges.append(FlowEdge(s.id, t.id))
        elif unmatched_src and not unmatched_tgt:
            for s in unmatched_src:
                for t in nxt_nodes:
                    edges.append(FlowEdge(s.id, t.id))

    return edges


def _assign_layers(nodes: list[StageNode], edges: list[FlowEdge]) -> None:
    if not nodes:
        return

    id_to_node = {n.id: n for n in nodes}
    children: dict[str, list[str]] = defaultdict(list)
    parents: dict[str, list[str]] = defaultdict(list)
    for e in edges:
        children[e.source].append(e.target)
        parents[e.target].append(e.source)

    roots = [n.id for n in nodes if n.id not in parents]
    if not roots:
        roots = [nodes[0].id]

    layers: dict[str, int] = {}
    visited: set[str] = set()
    queue = list(roots)
    for r in roots:
        layers[r] = 0

    while queue:
        nid = queue.pop(0)
        if nid in visited:
            continue
        visited.add(nid)
        cur_layer = layers.get(nid, 0)
        for child in children.get(nid, []):
            layers[child] = max(layers.get(child, 0), cur_layer + 1)
            queue.append(child)

    for n in nodes:
        if n.id not in layers:
            layers[n.id] = max(layers.values(), default=0) + 1

    layer_groups: dict[int, list[str]] = defaultdict(list)
    for n in nodes:
        n.layer = layers[n.id]
        layer_groups[n.layer].append(n.id)

    for layer_idx in sorted(layer_groups.keys()):
        group = layer_groups[layer_idx]
        for lane, nid in enumerate(group):
            id_to_node[nid].lane = lane


def _build_layer_list(nodes: list[StageNode]) -> list[list[str]]:
    layer_map: dict[int, list[str]] = defaultdict(list)
    for n in nodes:
        layer_map[n.layer].append(n.id)
    max_layer = max(layer_map.keys(), default=0)
    return [layer_map.get(i, []) for i in range(max_layer + 1)]


def graph_to_json(graph: ManufacturingGraph) -> str:
    data = {
        "product_name": graph.product_name,
        "nodes": [asdict(n) for n in graph.nodes],
        "edges": [asdict(e) for e in graph.edges],
        "layers": graph.layers,
    }
    return json.dumps(data, ensure_ascii=False)


def _read_summary_counts(path: Path) -> dict[str, dict[str, int]]:
    """Read summary CSV as {stage_name: {pass, fail, hold, total}}.

    The generated summary files use a stable positional layout:
    stage, pass, fail, hold, total. Reading by position is more robust than
    trusting localized headers, which may be mojibaked in some Windows paths.
    """
    out: dict[str, dict[str, int]] = {}
    if not path.exists():
        return out

    def to_int(value):
        try:
            return int(str(value).replace(",", "").strip() or "0")
        except Exception:
            return 0

    total_markers = {"전체", "총계", "합계", "total", "overall"}
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))

    for row in rows[1:]:
        if not row or not any(str(cell).strip() for cell in row):
            continue
        padded = row + ["0"] * 5
        name = str(padded[0]).strip()
        if not name or name.casefold() in total_markers:
            continue
        out[name] = {
            "pass": to_int(padded[1]),
            "fail": to_int(padded[2]),
            "hold": to_int(padded[3]),
            "total": to_int(padded[4]),
        }
    return out


def _locate_summary(csv_dir: Path, name: str) -> Path | None:
    """summary 파일을 상위 폴더(프로젝트 루트) → csv_dir 순으로 찾는다."""
    for base in (csv_dir.parent, csv_dir):
        cand = base / name
        if cand.exists():
            return cand
    return None


def extract_pdf_flowchart(pdf_path: Path):
    """extractor_codex_0517 로 PDF의 실제 제조 요약도(노드/연결)를 추출한다.

    반환: (node_names[순서], edge_pairs[(src_name,tgt_name)]) 또는 None(추출 실패).
    이름 추론이 아니라 PDF 다이어그램의 실제 구조를 그대로 사용한다.
    """
    try:
        import extractor_codex_0517 as ex
        pages = ex.PDFReader(str(pdf_path)).read()
        items = ex.Normalizer().run(pages)
        sections = ex.SectionBuilder().run(items)
        blocks = ex.BlockBuilder().run(items, sections)
        records = ex.RecordExtractor().run(blocks)
    except Exception:
        return None

    for r in records:
        dd = getattr(r, "diagram_data", None)
        if getattr(r, "record_type", None) == "flowchart" and dd:
            nodes = dd.get("nodes") or []
            edges = dd.get("edges") or []
            id2name = {n.get("node_id"): (n.get("name") or "").strip() for n in nodes}
            node_names = [(n.get("name") or "").strip() for n in nodes if (n.get("name") or "").strip()]
            edge_pairs = []
            for e in edges:
                s, t = id2name.get(e.get("from")), id2name.get(e.get("to"))
                if s and t:
                    edge_pairs.append((s, t))
            if node_names:
                return node_names, edge_pairs
    return None


def build_graph_from_flowchart(
    product_name: str,
    node_names: list[str],
    edge_pairs: list[tuple[str, str]],
    counts: dict[str, dict[str, int]],
) -> ManufacturingGraph:
    """PDF에서 추출한 실제 노드/연결 + summary 카운트로 그래프를 만든다."""
    count_by_norm = {_normalize(k): v for k, v in counts.items()}
    used_ids: set[str] = set()
    name_to_id: dict[str, str] = {}
    nodes: list[StageNode] = []
    for name in node_names:
        if name in name_to_id:
            continue
        slug = _slugify(name)
        if slug in used_ids:
            slug = f"{slug}_{len(used_ids)}"
        used_ids.add(slug)
        name_to_id[name] = slug
        c = count_by_norm.get(_normalize(name), {})
        nodes.append(StageNode(
            id=slug, label=name,
            pass_count=int(c.get("pass", 0)),
            fail_count=int(c.get("fail", 0)),
            hold_count=int(c.get("hold", 0)),
            total_count=int(c.get("total", 0)),
        ))
    edges = [FlowEdge(name_to_id[s], name_to_id[t])
             for s, t in edge_pairs if s in name_to_id and t in name_to_id]
    _assign_layers(nodes, edges)
    graph = ManufacturingGraph(product_name=product_name, nodes=nodes, edges=edges)
    graph.layers = _build_layer_list(nodes)
    return graph


def load_simulation_graph(
    csv_dir: Path,
    pdf_path: Path | None = None,
    explicit_summary_path: Path | None = None,
) -> tuple[ManufacturingGraph, dict[str, dict[str, int]]]:
    """검수 시뮬레이션용 그래프를 로드한다.

    - 구조(노드/연결): pdf_path 가 주어지면 extractor_codex 로 PDF의 실제 제조 요약도를
      그대로 사용한다. 실패하거나 PDF 가 없으면 CSV 이름 추론으로 폴백.
    - 카운트: explicit_summary_path 가 주어지면 그 파일을 직접 사용한다.
      없으면 판정 완료 후 생성된 summary_after.csv를 우선 사용하고,
      없으면 summary.csv를 사용한다.
    반환: (최종 summary 그래프, {제조명: 최종 summary 카운트})
    """
    csv_dir = Path(csv_dir)
    if explicit_summary_path is not None and Path(explicit_summary_path).exists():
        summary_path: Path | None = Path(explicit_summary_path)
    else:
        summary_path = _locate_summary(csv_dir, "summary_after.csv") or _locate_summary(csv_dir, "summary.csv")
    summary_counts = _read_summary_counts(summary_path) if summary_path else {}

    product_name = csv_dir.name.replace("_제조요약도_csv", "").replace("_csv", "")
    # Prefer the curated manufacturing-summary CSV. The PDF flowchart extractor
    # can return partial graphs on table-heavy pages, which breaks the simulation.
    graph = load_from_csv_directory(csv_dir, summary_path=summary_path)

    if not graph.nodes and pdf_path:
        fc = extract_pdf_flowchart(Path(pdf_path))
        if fc:
            node_names, edge_pairs = fc
            graph = build_graph_from_flowchart(product_name, node_names, edge_pairs, summary_counts)

    return graph, summary_counts


def _after_lookup(after: dict[str, dict[str, int]]) -> dict[str, dict[str, int]]:
    return {_normalize(k): v for k, v in after.items()}


def simulation_graph_to_json(graph: ManufacturingGraph, summary: dict[str, dict[str, int]]) -> str:
    """최종 summary 카운트를 화면 표시 필드와 after_* 목표 필드에 같이 직렬화한다."""
    summary_norm = _after_lookup(summary)
    nodes = []
    for n in graph.nodes:
        d = asdict(n)
        counts = summary_norm.get(_normalize(n.label))
        if counts:
            d["pass_count"] = counts["pass"]
            d["fail_count"] = counts["fail"]
            d["hold_count"] = counts["hold"]
            d["total_count"] = counts["total"]
            d["after_pass"] = counts["pass"]
            d["after_fail"] = counts["fail"]
            d["after_hold"] = counts["hold"]
            d["after_total"] = counts["total"]
        else:
            d["after_pass"] = n.pass_count
            d["after_fail"] = n.fail_count
            d["after_hold"] = n.hold_count
            d["after_total"] = n.total_count
        nodes.append(d)
    data = {
        "product_name": graph.product_name,
        "nodes": nodes,
        "edges": [asdict(e) for e in graph.edges],
        "layers": graph.layers,
    }
    return json.dumps(data, ensure_ascii=False)
